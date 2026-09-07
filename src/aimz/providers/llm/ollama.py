"""OllamaProvider: local inference through the Ollama HTTP API. Cost: $0.

Uses ``POST /api/chat`` with ``stream=false``. When a pydantic schema is given, its JSON
schema is passed as ``format`` (Ollama structured outputs) and ``think`` is disabled for
thinking-capable models so that the content field contains only the JSON object.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from pydantic import BaseModel

from aimz.core.errors import ProviderError, ProviderUnavailable
from aimz.core.retry import retry
from aimz.providers.base import HealthStatus, LLMProvider, LLMResponse, ProviderContext

log = logging.getLogger("aimz.llm.ollama")


class OllamaProvider(LLMProvider):
    name = "OllamaProvider"
    is_paid = False

    def __init__(
        self,
        host: str = "http://127.0.0.1:11434",
        model: str = "qwen3:4b",
        timeout_s: int = 600,
        num_ctx: int = 8192,
        think: bool = False,
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.num_ctx = num_ctx
        self.think = think
        self._client = httpx.Client(timeout=httpx.Timeout(timeout_s, connect=10))

    # -- health ----------------------------------------------------------------------
    def list_models(self) -> list[str]:
        r = self._client.get(f"{self.host}/api/tags", timeout=10)
        r.raise_for_status()
        return [m.get("name", "") for m in r.json().get("models", [])]

    def health(self) -> HealthStatus:
        try:
            models = self.list_models()
        except Exception as exc:
            return HealthStatus(
                False, f"Ollama not reachable at {self.host}: {exc}", "Install Ollama and run `ollama serve`."
            )
        base = self.model.split(":")[0]
        if self.model in models or any(m.split(":")[0] == base and ":" not in self.model for m in models):
            return HealthStatus(True, f"Ollama reachable; model {self.model} present ({len(models)} models)")
        return HealthStatus(
            False,
            f"Model {self.model} not found (have: {', '.join(models) or 'none'})",
            f"Run `ollama pull {self.model}`.",
        )

    # -- inference -------------------------------------------------------------------
    def complete(
        self,
        ctx: ProviderContext,
        system: str,
        user: str,
        purpose: str,
        *,
        schema: type[BaseModel] | None = None,
        temperature: float = 0.4,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "think": self.think,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": temperature, "num_ctx": self.num_ctx, "num_predict": max_tokens},
        }
        if schema is not None:
            body["format"] = schema.model_json_schema()

        with self.authorized(ctx, purpose) as rec:
            t0 = time.perf_counter()

            def _call() -> dict[str, Any]:
                try:
                    r = self._client.post(f"{self.host}/api/chat", json=body)
                except httpx.ConnectError as exc:
                    raise ProviderUnavailable(f"Ollama not reachable at {self.host}") from exc
                if r.status_code >= 500:
                    raise ProviderError(f"Ollama server error {r.status_code}: {r.text[:300]}")
                if r.status_code >= 400:
                    # 4xx (bad model, bad schema) is not retryable.
                    raise _Fatal(f"Ollama error {r.status_code}: {r.text[:300]}")
                return r.json()

            try:
                data = retry(
                    _call,
                    attempts=3,
                    base_delay=2.0,
                    retry_on=(ProviderError, httpx.TimeoutException),
                    label="ollama chat",
                )
            except _Fatal as exc:
                raise ProviderError(str(exc)) from exc

            msg = data.get("message", {}) or {}
            text = msg.get("content", "") or ""
            rec.tokens_in = data.get("prompt_eval_count")
            rec.tokens_out = data.get("eval_count")
            rec.actual_cost = 0.0
            dur = int((time.perf_counter() - t0) * 1000)
            log.debug(
                "ollama %s: %s tok in / %s tok out in %dms", purpose, rec.tokens_in, rec.tokens_out, dur
            )
            return LLMResponse(
                text=text,
                model=self.model,
                tokens_in=rec.tokens_in,
                tokens_out=rec.tokens_out,
                duration_ms=dur,
            )


class _Fatal(Exception):
    pass
