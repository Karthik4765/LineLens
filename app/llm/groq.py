"""Groq implementation of the LLM provider interface."""

from __future__ import annotations

import logging
from typing import Any

from groq import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    Groq,
    InternalServerError,
    RateLimitError,
)

from app.llm.base import (
    LLMProvider,
    ProviderRateLimitError,
    ProviderServerError,
    ProviderUnavailableError,
)

logger = logging.getLogger(__name__)


class GroqProvider(LLMProvider):
    """Generate explanations with a Groq-hosted model."""

    name = "groq"

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise ValueError("GROQ_API_KEY is required for the Groq provider.")
        self.model = model
        self._client = Groq(api_key=api_key)

    def _generate(self, prompt: str, code: str, analysis: dict[str, Any]) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
        except RateLimitError as exc:
            raise ProviderRateLimitError("Groq rate limit reached.") from exc
        except InternalServerError as exc:
            raise ProviderServerError("Groq returned a server error.") from exc
        except APIStatusError as exc:
            if exc.status_code == 429:
                raise ProviderRateLimitError("Groq rate limit reached.") from exc
            if exc.status_code >= 500:
                raise ProviderServerError("Groq returned a server error.") from exc
            raise ProviderUnavailableError("Groq request failed.") from exc
        except (APIConnectionError, APITimeoutError) as exc:
            raise ProviderUnavailableError("Groq is unavailable.") from exc
        except Exception as exc:  # unexpected transport failures
            logger.warning("Groq transport failure: %s", type(exc).__name__)
            raise ProviderUnavailableError("Groq is unavailable.") from exc

        return response.choices[0].message.content or ""
