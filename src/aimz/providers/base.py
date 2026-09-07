"""Provider interfaces.

Every concrete provider inherits :class:`Provider` and executes real work only inside
``with self.authorized(ctx, purpose, estimated_cost) as call:``. That context manager:

1. asks the :class:`BudgetManager` to authorize the estimated cost (raises ``BudgetDenied``);
2. for paid providers, also checks the kill switch;
3. records ``provider_usage`` with tokens/duration/actual cost on exit;
4. settles the ledger authorization with the actual cost.

Agents never call a provider's raw transport; they only see the interface methods below.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from aimz.core.budget import BudgetManager
from aimz.core.killswitch import KillSwitch
from aimz.core.runs import AgentSpan
from aimz.db import Database
from aimz.domain.models import (
    AssetCandidate,
    AssetRecord,
    FetchedComment,
    FetchedItem,
    MetricsSnapshot,
    PublishResult,
    RenderResult,
    Timeline,
)
from aimz.util import new_id, now_iso

T = TypeVar("T", bound=BaseModel)


@dataclass
class ProviderContext:
    """Everything a provider needs to run one call under supervision."""

    db: Database
    budget: BudgetManager
    killswitch: KillSwitch
    run_id: str | None = None
    content_id: str | None = None
    span: AgentSpan | None = None

    def child(self, content_id: str | None = None) -> ProviderContext:
        return ProviderContext(
            self.db, self.budget, self.killswitch, self.run_id, content_id or self.content_id, self.span
        )


@dataclass
class HealthStatus:
    ok: bool
    detail: str
    fix: str = ""


@dataclass
class CallRecord:
    """Mutable holder the provider fills in during the call."""

    actual_cost: float = 0.0
    tokens_in: int | None = None
    tokens_out: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Provider(ABC):
    name: str = "provider"
    kind: str = "generic"
    is_paid: bool = False  # paid providers are never registered unless the owner opts in

    def estimate_cost(self, purpose: str, **kwargs: Any) -> float:
        return 0.0

    @abstractmethod
    def health(self) -> HealthStatus: ...

    @contextmanager
    def authorized(
        self,
        ctx: ProviderContext,
        purpose: str,
        estimated_cost: float | None = None,
        **estimate_kwargs: Any,
    ) -> Iterator[CallRecord]:
        est = self.estimate_cost(purpose, **estimate_kwargs) if estimated_cost is None else estimated_cost
        if self.is_paid:
            ctx.killswitch.guard(f"paid provider {self.name}")
        auth = ctx.budget.authorize(self.name, purpose, est, run_id=ctx.run_id, content_id=ctx.content_id)
        rec = CallRecord()
        t0 = time.perf_counter()
        status = "ok"
        try:
            yield rec
        except Exception:
            status = "failed"
            raise
        finally:
            dur_ms = int((time.perf_counter() - t0) * 1000)
            ctx.budget.settle(auth, rec.actual_cost, status)
            ctx.db.insert(
                "provider_usage",
                {
                    "id": new_id("use"),
                    "run_id": ctx.run_id,
                    "agent_run_id": ctx.span.id if ctx.span else None,
                    "provider": self.name,
                    "action": purpose,
                    "content_id": ctx.content_id,
                    "estimated_cost_usd": est,
                    "actual_cost_usd": rec.actual_cost,
                    "tokens_in": rec.tokens_in,
                    "tokens_out": rec.tokens_out,
                    "duration_ms": dur_ms,
                    "status": status,
                    "created_at": now_iso(),
                },
            )
            if ctx.span is not None:
                ctx.span.add_cost(est, rec.actual_cost)


# --------------------------------------------------------------------------------------
# LLM
# --------------------------------------------------------------------------------------
@dataclass
class LLMResponse:
    text: str
    model: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    duration_ms: int = 0


class LLMProvider(Provider):
    kind = "llm"

    @abstractmethod
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
    ) -> LLMResponse: ...

    def complete_model(
        self,
        ctx: ProviderContext,
        system: str,
        user: str,
        schema: type[T],
        purpose: str,
        *,
        temperature: float = 0.4,
        max_tokens: int = 2048,
        attempts: int = 3,
    ) -> T:
        """Call the model with structured output and validate; retry with the error fed back."""
        from aimz.core.errors import ValidationError
        from aimz.providers.llm.json_repair import extract_json

        last_err: Exception | None = None
        user_msg = user
        for _ in range(attempts):
            resp = self.complete(
                ctx, system, user_msg, purpose, schema=schema, temperature=temperature, max_tokens=max_tokens
            )
            try:
                data = extract_json(resp.text)
                return schema.model_validate(data)
            except Exception as exc:  # pydantic or JSON error
                last_err = exc
                user_msg = (
                    user
                    + "\n\nYour previous answer was not valid. Error: "
                    + str(exc)[:600]
                    + "\nReturn ONLY a JSON object matching the schema."
                )
                temperature = max(0.0, temperature - 0.15)
        raise ValidationError(f"LLM output failed validation for {schema.__name__}: {last_err}")


# --------------------------------------------------------------------------------------
# TTS
# --------------------------------------------------------------------------------------
@dataclass
class TTSResult:
    path: str
    duration_s: float
    sample_rate: int


class TTSProvider(Provider):
    kind = "tts"

    @abstractmethod
    def synthesize(
        self, ctx: ProviderContext, text: str, out_path: Path, purpose: str = "narration"
    ) -> TTSResult: ...


# --------------------------------------------------------------------------------------
# Images / cards
# --------------------------------------------------------------------------------------
class ImageProvider(Provider):
    kind = "image"

    @abstractmethod
    def render_card(self, ctx: ProviderContext, spec: dict[str, Any], out_path: Path) -> Path: ...


# --------------------------------------------------------------------------------------
# Video rendering
# --------------------------------------------------------------------------------------
class VideoProvider(Provider):
    kind = "video"

    @abstractmethod
    def render(self, ctx: ProviderContext, timeline: Timeline, out_dir: Path) -> RenderResult: ...

    @abstractmethod
    def probe_duration(self, path: Path) -> float: ...


# --------------------------------------------------------------------------------------
# Assets
# --------------------------------------------------------------------------------------
class AssetProvider(Provider):
    kind = "asset"

    @abstractmethod
    def search(self, ctx: ProviderContext, query: str, max_results: int = 5) -> list[AssetCandidate]: ...

    @abstractmethod
    def fetch(
        self, ctx: ProviderContext, candidate: AssetCandidate, dest_dir: Path
    ) -> AssetRecord | None: ...


# --------------------------------------------------------------------------------------
# Research
# --------------------------------------------------------------------------------------
class ResearchProvider(Provider):
    kind = "research"
    kinds: tuple[str, ...] = ()

    @abstractmethod
    def fetch(self, ctx: ProviderContext, source: dict[str, Any]) -> list[FetchedItem]: ...


# --------------------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------------------
class Publisher(Provider):
    kind = "publisher"
    platform: str = ""
    performs_api_writes: bool = False

    @abstractmethod
    def publish(
        self,
        ctx: ProviderContext,
        video: dict[str, Any],
        script: dict[str, Any],
        metadata: dict[str, Any],
        package_dir: Path,
    ) -> PublishResult: ...


# --------------------------------------------------------------------------------------
# Analytics
# --------------------------------------------------------------------------------------
class AnalyticsProvider(Provider):
    kind = "analytics"
    platform: str = ""

    @abstractmethod
    def fetch_metrics(self, ctx: ProviderContext, publication: dict[str, Any]) -> MetricsSnapshot | None: ...

    def fetch_comments(self, ctx: ProviderContext, publication: dict[str, Any]) -> list[FetchedComment]:
        return []
