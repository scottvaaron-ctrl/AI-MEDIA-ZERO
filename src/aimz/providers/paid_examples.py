"""Templates for *future* paid providers. Disabled by default and never registered in V0.

They exist to prove the budget gate end-to-end: a paid provider must (1) set ``is_paid = True``,
(2) return a non-zero ``estimate_cost``, and (3) do its real work only inside ``self.authorized``.
With ``MONTHLY_BUDGET_USD=0.00`` the BudgetManager denies the call before any network I/O.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from aimz.providers.base import (
    HealthStatus,
    LLMProvider,
    LLMResponse,
    ProviderContext,
    TTSProvider,
    TTSResult,
)


class PaidTTSProviderExample(TTSProvider):
    """Example of a usage-billed TTS (e.g. a cloud voice API). Per-character pricing."""

    name = "PaidTTSProvider"
    is_paid = True

    def __init__(self, usd_per_1k_chars: float = 0.30):
        self.usd_per_1k_chars = usd_per_1k_chars

    def estimate_cost(self, purpose: str, **kwargs: Any) -> float:
        chars = int(kwargs.get("chars", 0))
        return round(chars / 1000.0 * self.usd_per_1k_chars, 4)

    def health(self) -> HealthStatus:
        return HealthStatus(False, "disabled example provider")

    def synthesize(
        self, ctx: ProviderContext, text: str, out_path: Path, purpose: str = "narration"
    ) -> TTSResult:
        with self.authorized(ctx, purpose, chars=len(text)):
            raise NotImplementedError(
                "This example never performs a paid call; wire a real client here in V1."
            )


class PaidLLMProviderExample(LLMProvider):
    """Example of a token-billed OpenAI-compatible endpoint."""

    name = "PaidLLMProvider"
    is_paid = True

    def __init__(self, usd_per_1k_in: float = 0.005, usd_per_1k_out: float = 0.015):
        self.usd_per_1k_in = usd_per_1k_in
        self.usd_per_1k_out = usd_per_1k_out

    def estimate_cost(self, purpose: str, **kwargs: Any) -> float:
        tok_in = int(kwargs.get("tokens_in", 2000))
        tok_out = int(kwargs.get("tokens_out", 1000))
        return round(tok_in / 1000 * self.usd_per_1k_in + tok_out / 1000 * self.usd_per_1k_out, 4)

    def health(self) -> HealthStatus:
        return HealthStatus(False, "disabled example provider")

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
        with self.authorized(ctx, purpose, tokens_in=(len(system) + len(user)) // 4, tokens_out=max_tokens):
            raise NotImplementedError(
                "This example never performs a paid call; wire a real client here in V1."
            )
