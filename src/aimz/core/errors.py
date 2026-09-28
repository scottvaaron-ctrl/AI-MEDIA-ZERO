"""Exception hierarchy."""

from __future__ import annotations


class AimzError(Exception):
    """Base class for all project errors."""


class BudgetDenied(AimzError):
    """Raised by the BudgetManager when a provider call would exceed the configured budget."""

    def __init__(self, provider: str, purpose: str, estimated_cost: float, available: float):
        self.provider = provider
        self.purpose = purpose
        self.estimated_cost = estimated_cost
        self.available = available
        super().__init__(
            "DENIED\n\n"
            f"Provider: {provider}\n"
            f"Purpose: {purpose}\n"
            f"Estimated cost: ${estimated_cost:.2f}\n"
            f"Available budget: ${available:.2f}"
        )


class KillSwitchEngaged(AimzError):
    """Raised when an outbound/paid/API-write action is attempted while the kill switch is on."""


class ProviderError(AimzError):
    """A provider failed (network, model, tool)."""


class ProviderUnavailable(ProviderError):
    """A provider's dependency is missing (binary, model, voice)."""


class PublishError(AimzError):
    """A publish attempt failed. Never retried blindly: a retry first confirms the post is not already live."""


class PermanentPublishError(PublishError):
    """The platform can never accept this video as it is (too long, too large, refused format). Not retried."""


class CannotVerify(AimzError):
    """A publisher cannot tell whether an earlier attempt already created the post."""


class TechnicalFailure(AimzError):
    """A stage failed for a technical reason (model down, timeout), not an editorial judgement.

    Work that hits this is set aside and retried later; it is never rejected as if the content were bad.
    """


class ValidationError(AimzError):
    """Structured output from the LLM did not validate."""


class OwnerApprovalRequired(AimzError):
    """The action is gated behind an explicit owner approval."""
