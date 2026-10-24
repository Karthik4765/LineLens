"""Deterministic provider used by tests and by local runs without API keys."""

from __future__ import annotations

import json
from typing import Any

from app.llm.base import LLMProvider


class FakeProvider(LLMProvider):
    """A local, deterministic stand-in for a real language model.

    It derives explanations from the AST facts. It is intentionally obvious that
    it is not an AI provider: the API reports its name as ``fake``.
    """

    def __init__(
        self,
        *,
        name: str = "fake",
        invalid_attempts: int = 0,
        fail_with: Exception | None = None,
    ) -> None:
        self.name = name
        self.invalid_attempts = invalid_attempts
        self.fail_with = fail_with
        self.calls: list[str] = []

    def _generate(self, prompt: str, code: str, analysis: dict[str, Any]) -> str:
        self.calls.append(prompt)
        if self.fail_with is not None:
            raise self.fail_with
        if len(self.calls) <= self.invalid_attempts:
            return "This is deliberately not JSON."

        source_lines = code.splitlines()
        lines = []
        for facts in analysis.get("lines", []):
            number = facts["line"]
            source = source_lines[number - 1] if number <= len(source_lines) else ""
            if not source.strip():
                continue
            lines.append(
                {
                    "line": number,
                    "explanation": self._explain_line(source, facts, analysis),
                }
            )

        payload = {"summary": self._summary(analysis), "lines": lines}
        return json.dumps(payload)

    def _summary(self, analysis: dict[str, Any]) -> str:
        line_count = analysis.get("total_lines", 0)
        function_count = len(analysis.get("functions", []))
        import_count = len(analysis.get("imports", []))
        loop_count = analysis.get("max_loop_nesting", 0)
        loop_text = {
            0: "no loops",
            1: "a single loop",
            2: "nested loops",
        }.get(loop_count, "deeply nested loops")
        return (
            f"This {line_count}-line Python program defines {function_count} "
            f"function(s), imports {import_count} module binding(s), and contains "
            f"{loop_text}. The explanations below describe each line in order."
        )

    def _explain_line(
        self,
        source: str,
        facts: dict[str, Any],
        analysis: dict[str, Any],
    ) -> str:
        stripped = source.strip()
        if stripped.startswith("#"):
            comment = stripped.lstrip("#").strip()
            return f"This comment notes: {comment}" if comment else "This is a comment."

        types = set(facts.get("node_types", []))
        defined = facts.get("names_defined", [])
        used = facts.get("names_used", [])

        function = next(
            (item for item in analysis.get("functions", []) if item["line"] == facts["line"]),
            None,
        )
        if "function_def" in types and function:
            args = ", ".join(function["args"]) or "no parameters"
            kind = "async function" if function.get("async") else "function"
            return f"Defines the {kind} `{function['name']}` with {args}."

        class_def = next(
            (item for item in analysis.get("classes", []) if item["line"] == facts["line"]),
            None,
        )
        if "class_def" in types and class_def:
            return f"Defines a class named `{class_def['name']}`."

        if "import" in types:
            imports = [
                item for item in analysis.get("imports", []) if item["line"] == facts["line"]
            ]
            if imports:
                modules = ", ".join(f"`{item['module']}`" for item in imports)
                return f"Imports {modules} for use later in the program."

        if "for" in types:
            target = ", ".join(defined) or "the loop variable"
            return f"Starts a `for` loop, binding `{target}` for each value it iterates over."
        if "while" in types:
            return "Starts a `while` loop that repeats while its condition remains true."
        if "if" in types:
            return "Checks a condition and runs the indented block only when it is true."
        if "return" in types:
            value = ", ".join(used) or "a value"
            return f"Returns {value} to the code that called this function."
        if "assign" in types or "aug_assign" in types:
            target = ", ".join(defined) or "a variable"
            return f"Stores or updates a value in `{target}`."
        if "call" in types:
            value = ", ".join(used) or "a function"
            return f"Calls {value} and uses the result of that call."
        if "raise" in types:
            return "Raises an exception to signal that something went wrong."
        if "with" in types:
            return "Opens a context-managed resource and runs the indented block."
        return "Runs this statement as part of the program's control flow."
