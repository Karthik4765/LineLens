"""Gemini implementation of the LLM provider interface."""

from __future__ import annotations

import logging
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.llm.base import (
    LLMProvider,
    ProviderRateLimitError,
    ProviderServerError,
    ProviderUnavailableError,
)

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    """Generate explanations with Google Gemini through the google-genai SDK."""

    name = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for the Gemini provider.")
        self.model = model
        self._client = genai.Client(api_key=api_key)

    def _generate(
        self,
        prompt: str,
        code: str,
        analysis: dict[str, Any],
        error_context: dict[str, Any] | None = None,
    ) -> str:
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
        except genai_errors.APIError as exc:
            code_value = getattr(exc, "code", None)
            if code_value == 429:
                raise ProviderRateLimitError("Gemini rate limit reached.") from exc
            if isinstance(code_value, int) and code_value >= 500:
                raise ProviderServerError("Gemini returned a server error.") from exc
            raise ProviderUnavailableError("Gemini request failed.") from exc
        except Exception as exc:  # network and SDK transport failures
            logger.warning("Gemini transport failure: %s", type(exc).__name__)
            raise ProviderUnavailableError("Gemini is unavailable.") from exc

        return response.text or ""
