"""Provider selection and fallback orchestration."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.config import Settings, get_settings
from app.llm.base import (
    InvalidLLMResponse,
    LLMExplanation,
    LLMProvider,
    ProviderError,
    ProviderUnavailableError,
)
from app.llm.fake import FakeProvider
from app.llm.gemini import GeminiProvider
from app.llm.groq import GroqProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExplanationResult:
    """A validated explanation plus provenance for the response and usage log."""

    explanation: LLMExplanation
    provider: str
    fallback_used: bool


class Explainer:
    """Try the selected provider, then the backup, for retryable failures."""

    def __init__(self, providers: Sequence[LLMProvider]) -> None:
        if not providers:
            raise ValueError("At least one LLM provider is required.")
        self.providers = list(providers)

    @property
    def provider_names(self) -> list[str]:
        return [provider.name for provider in self.providers]

    def explain(
        self,
        code: str,
        analysis: dict[str, Any],
        error_context: dict[str, Any] | None = None,
        trace: dict[str, Any] | None = None,
    ) -> ExplanationResult:
        """Return a validated explanation, falling back when appropriate."""

        last_error: ProviderError | None = None
        for index, provider in enumerate(self.providers):
            try:
                explanation = provider.explain(code, analysis, error_context, trace)
                return ExplanationResult(
                    explanation=explanation,
                    provider=provider.name,
                    fallback_used=index > 0,
                )
            except InvalidLLMResponse:
                # A malformed response is not an availability problem. The same
                # provider was already retried once; switching providers would hide
                # a real response-contract failure.
                raise
            except ProviderError as exc:
                last_error = exc
                logger.warning("Provider %s failed: %s", provider.name, type(exc).__name__)
                continue

        raise ProviderUnavailableError(
            "No AI provider is currently available. Please try again shortly."
        ) from last_error


def build_explainer(settings: Settings) -> Explainer:
    """Build the provider chain from environment configuration."""

    if settings.llm_provider == "fake":
        return Explainer([FakeProvider()])

    configured: dict[str, LLMProvider] = {}
    if settings.gemini_api_key:
        configured["gemini"] = GeminiProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
        )
    if settings.groq_api_key:
        configured["groq"] = GroqProvider(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
        )

    if not configured:
        logger.warning(
            "No Gemini or Groq API key configured; using the deterministic fake provider."
        )
        return Explainer([FakeProvider()])

    requested = settings.llm_provider
    if requested in ("gemini", "groq"):
        primary = configured.get(requested)
        backup_name = "groq" if requested == "gemini" else "gemini"
        backup = configured.get(backup_name)
        if primary is None:
            logger.warning(
                "LLM_PROVIDER=%s has no API key; using %s instead.",
                requested,
                backup_name,
            )
            primary = backup
            backup = None
        return Explainer([provider for provider in (primary, backup) if provider is not None])

    logger.warning(
        "Unknown LLM_PROVIDER=%s; using the configured provider order.", requested
    )
    ordered = [configured[name] for name in ("gemini", "groq") if name in configured]
    return Explainer(ordered)


@lru_cache
def get_explainer() -> Explainer:
    """Return the process-wide explainer dependency."""

    return build_explainer(get_settings())
