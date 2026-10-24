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

    def _generate(
        self,
        prompt: str,
        code: str,
        analysis: dict[str, Any],
        error_context: dict[str, Any] | None = None,
        trace: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append(prompt)
        if self.fail_with is not None:
            raise self.fail_with
        if len(self.calls) <= self.invalid_attempts:
            return "This is deliberately not JSON."

        trace_by_line = self._trace_by_line(trace)
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
                    "explanation": self._explain_line(
                        source, facts, analysis, error_context, trace, trace_by_line
                    ),
                }
            )

        payload = {
            "summary": self._summary(analysis, error_context, trace),
            "lines": lines,
        }
        return json.dumps(payload)

    @staticmethod
    def _trace_by_line(trace: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
        if not trace:
            return {}
        by_line: dict[int, dict[str, Any]] = {}
        for step in trace.get("steps", []):
            if step.get("event", "line") == "line":
                by_line[int(step["line"])] = step
        return by_line

    def _summary(
        self,
        analysis: dict[str, Any],
        error_context: dict[str, Any] | None = None,
        trace: dict[str, Any] | None = None,
    ) -> str:
        line_count = analysis.get("total_lines", 0)
        function_count = len(analysis.get("functions", []))
        import_count = len(analysis.get("imports", []))
        loop_count = analysis.get("max_loop_nesting", 0)
        loop_text = {
            0: "no loops",
            1: "a single loop",
            2: "nested loops",
        }.get(loop_count, "deeply nested loops")
        summary = (
            f"This {line_count}-line Python program defines {function_count} "
            f"function(s), imports {import_count} module binding(s), and contains "
            f"{loop_text}. The explanations below describe each line in order."
        )
        if error_context:
            line = error_context.get("line")
            location = f" on line {line}" if line is not None else ""
            summary += (
                f" When it ran, it stopped with {error_context.get('type', 'an error')}"
                f"{location}: {error_context.get('message', '')}. "
                "Fix that problem first, then run the program again."
            )
        if trace:
            final_output = str(trace.get("final_output", ""))
            step_count = len(trace.get("steps", []))
            if final_output:
                preview = final_output.rstrip("\n").replace("\n", "\\n")
                if len(preview) > 80:
                    preview = preview[:79] + "..."
                summary += (
                    f" When run, it printed `{preview}` across {step_count} trace steps."
                )
            else:
                summary += (
                    f" When run, it finished without printing output across "
                    f"{step_count} trace steps."
                )
        return summary

    def _finish(
        self,
        base: str,
        facts: dict[str, Any],
        error_context: dict[str, Any] | None,
        trace: dict[str, Any] | None,
        trace_by_line: dict[int, dict[str, Any]],
    ) -> str:
        with_error = self._add_error_note(base, facts, error_context)
        return self._add_trace_note(with_error, facts, trace, trace_by_line)

    def _explain_line(
        self,
        source: str,
        facts: dict[str, Any],
        analysis: dict[str, Any],
        error_context: dict[str, Any] | None = None,
        trace: dict[str, Any] | None = None,
        trace_by_line: dict[int, dict[str, Any]] | None = None,
    ) -> str:
        trace_by_line = trace_by_line or {}
        stripped = source.strip()
        if stripped.startswith("#"):
            comment = stripped.lstrip("#").strip()
            base = f"This comment notes: {comment}" if comment else "This is a comment."
            return self._finish(base, facts, error_context, trace, trace_by_line)

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
            base = f"Defines the {kind} `{function['name']}` with {args}."
            return self._finish(base, facts, error_context, trace, trace_by_line)

        class_def = next(
            (item for item in analysis.get("classes", []) if item["line"] == facts["line"]),
            None,
        )
        if "class_def" in types and class_def:
            base = f"Defines a class named `{class_def['name']}`."
            return self._finish(base, facts, error_context, trace, trace_by_line)

        if "import" in types:
            imports = [
                item for item in analysis.get("imports", []) if item["line"] == facts["line"]
            ]
            if imports:
                modules = ", ".join(f"`{item['module']}`" for item in imports)
                base = f"Imports {modules} for use later in the program."
                return self._finish(base, facts, error_context, trace, trace_by_line)

        if "for" in types:
            target = ", ".join(defined) or "the loop variable"
            base = f"Starts a `for` loop, binding `{target}` for each value it iterates over."
        elif "while" in types:
            base = "Starts a `while` loop that repeats while its condition remains true."
        elif "if" in types:
            base = "Checks a condition and runs the indented block only when it is true."
        elif "return" in types:
            value = ", ".join(used) or "a value"
            base = f"Returns {value} to the code that called this function."
        elif "assign" in types or "aug_assign" in types:
            target = ", ".join(defined) or "a variable"
            base = f"Stores or updates a value in `{target}`."
        elif "call" in types:
            value = ", ".join(used) or "a function"
            base = f"Calls {value} and uses the result of that call."
        elif "raise" in types:
            base = "Raises an exception to signal that something went wrong."
        elif "with" in types:
            base = "Opens a context-managed resource and runs the indented block."
        else:
            base = "Runs this statement as part of the program's control flow."
        return self._finish(base, facts, error_context, trace, trace_by_line)

    @staticmethod
    def _add_trace_note(
        explanation: str,
        facts: dict[str, Any],
        trace: dict[str, Any] | None,
        trace_by_line: dict[int, dict[str, Any]],
    ) -> str:
        if not trace:
            return explanation
        step = trace_by_line.get(int(facts.get("line", 0)))
        if step is None:
            return f"{explanation} This line did not run during the traced execution."
        parts = [explanation]
        changed = step.get("changed") or {}
        if changed:
            values = []
            for name, value in changed.items():
                if value is None:
                    values.append(f"`{name}` was removed")
                else:
                    values.append(f"`{name}` = `{value}`")
            parts.append("After this line, " + ", ".join(values) + ".")
        output = step.get("out")
        if output:
            preview = str(output).rstrip("\n").replace("\n", "\\n")
            if len(preview) > 80:
                preview = preview[:79] + "..."
            parts.append(f"It printed: `{preview}`.")
        if step.get("failed"):
            error = trace.get("error") or {}
            parts.append(
                f"This line raised {error.get('type', 'an error')}: "
                f"{error.get('message', '')}"
            )
        return " ".join(parts)

    @staticmethod
    def _add_error_note(
        explanation: str,
        facts: dict[str, Any],
        error_context: dict[str, Any] | None,
    ) -> str:
        if not error_context or error_context.get("line") != facts.get("line"):
            return explanation
        return (
            f"{explanation} This is the line that raised "
            f"{error_context.get('type', 'an error')}: "
            f"{error_context.get('message', '')}"
        )
