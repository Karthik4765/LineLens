"""Unit tests for the subprocess-backed execution tracer."""

from __future__ import annotations

import sys

import pytest

from app.services.tracer import trace_code


def test_trace_three_line_example_records_real_values():
    result = trace_code("a, b = 1, 2\nc = a + b\nprint(c)\n")

    assert result.status == "ok"
    assert result.note is None
    assert result.final_output == "3\n"
    assert [step.line for step in result.steps] == [1, 2, 3]
    assert result.steps[0].variables == {"a": "1", "b": "2"}
    assert result.steps[1].variables == {"c": "3"}
    assert result.steps[2].stdout == "3\n"
    assert result.run_counts == {1: 1, 2: 1, 3: 1}
    assert result.run_steps == {1: [1], 2: [2], 3: [3]}
    assert result.error is None


def test_trace_loop_keeps_every_run_and_the_real_count():
    result = trace_code(
        "total = 0\nfor i in range(3):\n    total += i\nprint(total)\n"
    )

    assert result.status == "ok"
    assert result.final_output == "3\n"
    # The body line runs exactly three times.
    assert result.run_counts[3] == 3
    assert len(result.run_steps[3]) == 3
    body_steps = [step for step in result.steps if step.line == 3]
    # total += 0 leaves the value unchanged, so the first body step has no diff.
    assert [step.variables.get("total") for step in body_steps] == [None, "1", "3"]


def test_trace_attributes_a_change_to_the_line_that_finished():
    result = trace_code("x = 1\ny = 2\n")

    first, second = result.steps
    assert first.line == 1
    assert first.variables == {"x": "1"}
    assert second.line == 2
    assert second.variables == {"y": "2"}


def test_trace_records_function_call_depth_and_return_value():
    result = trace_code(
        "def add(a, b):\n    total = a + b\n    return total\n\nprint(add(2, 3))\n"
    )

    assert result.status == "ok"
    assert result.final_output == "5\n"
    function_steps = [step for step in result.steps if step.function == "add"]
    assert function_steps[0].depth == 1
    assert any(step.event == "return" and step.variables.get("return") == "5" for step in function_steps)


def test_trace_truncates_long_values_and_collections():
    result = trace_code("values = list(range(100))\nword = 'x' * 200\n")

    assert result.status == "ok"
    values = result.steps[0].variables["values"]
    word = result.steps[1].variables["word"]
    assert len(values) <= 80
    assert "..." in values
    assert len(word) <= 80


def test_trace_skips_dunder_names_and_modules():
    result = trace_code("__private__ = 1\nvalue = 2\nimport math\n")

    all_names = set()
    for step in result.steps:
        all_names.update(step.variables)
    assert "__private__" not in all_names
    assert "math" not in all_names
    assert "value" in all_names


def test_trace_allows_an_allowlisted_import():
    result = trace_code("import math\nprint(math.sqrt(9))\n")

    assert result.status == "ok"
    assert result.final_output == "3.0\n"


@pytest.mark.parametrize("module", ["os", "sys", "subprocess", "pathlib", "importlib"])
def test_trace_blocks_other_imports_with_a_clear_note(module: str):
    result = trace_code(f"import {module}\n")

    assert result.status == "unavailable"
    assert result.note is not None
    assert "not allowed in LineLens" in result.note
    assert module in result.note
    assert result.steps == []


@pytest.mark.parametrize("name", ["open", "exec", "eval", "compile", "breakpoint"])
def test_trace_removes_dangerous_builtins(name: str):
    result = trace_code(f"{name}('x')\n")

    assert result.status == "ok"
    assert result.error is not None
    assert result.error.type == "NameError"
    assert name in result.error.message
    assert any(step.failing for step in result.steps)


def test_trace_reads_optional_stdin():
    result = trace_code(
        "name = input()\nprint(f'Hi {name}')\n",
        stdin="Ada\n",
    )

    assert result.status == "ok"
    assert result.final_output == "Hi Ada\n"
    assert result.steps[0].variables["name"] == "'Ada'"


def test_trace_input_without_stdin_reports_eof_instead_of_hanging():
    result = trace_code("input()\n")

    assert result.status == "ok"
    assert result.error is not None
    assert result.error.type == "EOFError"
    assert result.error.line == 1
    assert result.steps[0].failing is True


def test_trace_keeps_the_failing_line_and_marks_it():
    result = trace_code("x = 1\nprint(x / 0)\n")

    assert result.status == "ok"
    assert result.error is not None
    assert result.error.type == "ZeroDivisionError"
    assert result.error.line == 2
    assert [step.line for step in result.steps] == [1, 2]
    assert result.steps[-1].failing is True
    assert "ZeroDivisionError" in (result.note or "")


def test_trace_timeout_returns_unavailable():
    result = trace_code(
        "while True:\n    pass\n",
        timeout_seconds=0.25,
        max_events=1_000_000,
    )

    assert result.status == "unavailable"
    assert result.note is not None
    assert "0.25 seconds" in result.note
    assert result.steps == []


def test_trace_caps_output_and_stops():
    result = trace_code("print('x' * 100000)\n", max_output_chars=100)

    assert result.status == "ok"
    assert result.truncated is True
    assert len(result.final_output) == 100
    assert result.note is not None
    assert "Output was capped" in result.note


def test_trace_caps_events_and_keeps_the_real_prefix():
    result = trace_code("for i in range(100):\n    x = i\n", max_events=5)

    assert result.status == "ok"
    assert result.truncated is True
    assert len(result.steps) == 5
    assert result.note is not None
    assert "5 steps" in result.note


def test_trace_syntax_error_returns_unavailable():
    result = trace_code("if True\n    pass\n")

    assert result.status == "unavailable"
    assert result.note is not None
    assert "syntax error" in result.note.lower()


def test_trace_system_exit_is_not_reported_as_a_failure():
    result = trace_code("print('before')\nexit()\n")

    assert result.status == "ok"
    assert result.error is None
    assert result.final_output == "before\n"


@pytest.mark.skipif(sys.platform == "win32", reason="resource limits are POSIX-only")
def test_trace_memory_limit_is_supported_on_posix():
    result = trace_code("values = [0] * (1024 * 1024 * 1024)\n")

    assert result.status in {"ok", "unavailable"}
    if result.status == "ok":
        assert result.error is not None
