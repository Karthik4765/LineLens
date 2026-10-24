"""Shared LLM provider interface, prompt construction, and response validation."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

JSON_INSTRUCTIONS = """You are LineLens, a patient Python tutor. Explain the supplied Python
program to a learner who understands basic syntax but wants to know what each line
actually does.

Return JSON only. Do not use markdown fences, prose before the JSON, or comments.
Use exactly this shape:
{"summary": "a short plain-English overview", "lines": [{"line": 1, "explanation": "what line 1 does"}]}

Rules:
- Explain every non-blank source line exactly once, using its real 1-based line number.
- Skip blank lines entirely.
- Keep each explanation concrete, beginner-friendly, and focused on that line.
- Mention important names, conditions, and effects, but do not invent behaviour.
- The AST facts are supporting metadata, not a replacement for reading the code.
- Never include raw code, HTML, or markdown in the explanation strings.
"""


class ProviderError(Exception):
    """Base class for failures raised by an LLM provider."""


class RetryableProviderError(ProviderError):
    """A provider failure for which trying the backup provider is sensible."""


class ProviderRateLimitError(RetryableProviderError):
    """The provider refused the request due to rate limiting."""


class ProviderServerError(RetryableProviderError):
    """The provider returned a server-side error."""


class ProviderUnavailableError(RetryableProviderError):
    """The provider could not be reached or is misconfigured."""


class InvalidLLMResponse(ProviderError):
    """The provider returned text that is not a valid explanation payload."""


class LineExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line: int = Field(ge=1)
    explanation: str = Field(min_length=1)

    @field_validator("explanation")
    @classmethod
    def explanation_is_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Explanation cannot be blank.")
        return cleaned


class LLMExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    lines: list[LineExplanation]

    @field_validator("summary")
    @classmethod
    def summary_is_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Summary cannot be blank.")
        return cleaned

    @model_validator(mode="after")
    def line_numbers_are_unique(self) -> LLMExplanation:
        seen: set[int] = set()
        for item in self.lines:
            if item.line in seen:
                raise ValueError(f"Duplicate explanation for line {item.line}.")
            seen.add(item.line)
        return self


def build_prompt(code: str, analysis: dict[str, Any]) -> str:
    """Build the provider-independent prompt sent to an LLM."""

    facts = json.dumps(analysis, ensure_ascii=False, separators=(",", ":"))
    return (
        f"{JSON_INSTRUCTIONS}\n"
        "SOURCE CODE:\n"
        f"<python>\n{code}\n</python>\n\n"
        "AST FACTS (JSON):\n"
        f"<facts>{facts}</facts>\n"
    )


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if lines:
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_explanation(raw: str, code: str) -> LLMExplanation:
    """Parse and validate a model response, removing blank-line entries."""

    payload = json.loads(_strip_fences(raw))
    explanation = LLMExplanation.model_validate(payload)
    source_lines = code.splitlines()
    valid_lines: list[LineExplanation] = []
    for item in explanation.lines:
        if item.line > len(source_lines):
            raise ValueError(f"Line {item.line} does not exist in the source.")
        if source_lines[item.line - 1].strip():
            valid_lines.append(item)
    return LLMExplanation(summary=explanation.summary, lines=valid_lines)


class LLMProvider(ABC):
    """One provider behind a stable interface.

    The public ``explain`` method owns the invalid-JSON retry so every provider
    behaves the same way. Subclasses only implement the network call.
    """

    name = "provider"

    def explain(self, code: str, analysis: dict[str, Any]) -> LLMExplanation:
        """Ask the model for a structured explanation, retrying malformed JSON once."""

        prompt = build_prompt(code, analysis)
        last_error: Exception | None = None
        for attempt in range(2):
            raw = self._generate(prompt, code, analysis)
            try:
                return parse_explanation(raw, code)
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = exc
                logger.warning(
                    "%s returned invalid explanation JSON (attempt %s/2): %s",
                    self.name,
                    attempt + 1,
                    type(exc).__name__,
                )
        raise InvalidLLMResponse(
            "The AI provider returned an invalid explanation. Please try again."
        ) from last_error

    @abstractmethod
    def _generate(self, prompt: str, code: str, analysis: dict[str, Any]) -> str:
        """Return raw model text for a prompt."""
