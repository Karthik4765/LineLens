"""Unit tests for the subprocess-backed code runner."""

from __future__ import annotations

from app.services.runner import check_syntax, run_code


def test_run_prints_output():
    result = run_code("print('hello')\n")

    assert result.ok is True
    assert result.stdout == "hello\n"
    assert result.stderr == ""
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.error is None
    assert result.duration_ms >= 0


def test_run_reports_syntax_error_without_starting_a_process():
    result = run_code("if True\n    pass\n")

    assert result.ok is False
    assert result.timed_out is False
    assert result.error is not None
    assert result.error.type == "SyntaxError"
    assert result.error.line == 1
    assert result.error.phase == "syntax"
    assert "colon" in result.error.hint.lower()
    assert "SyntaxError" in result.stderr
    assert "<your code>" in result.stderr


def test_run_explains_an_indentation_error():
    result = run_code("for i in range(3):\nprint(i)\n")

    assert result.ok is False
    assert result.error is not None
    assert result.error.type == "IndentationError"
    assert result.error.line == 2
    assert "indent" in result.error.hint.lower()


def test_run_reports_runtime_error_and_line():
    result = run_code("value = 10\nprint(value / 0)\n")

    assert result.ok is False
    assert result.exit_code != 0
    assert result.error is not None
    assert result.error.type == "ZeroDivisionError"
    assert result.error.line == 2
    assert "zero" in result.error.hint.lower()
    assert "ZeroDivisionError" in result.stderr
    assert "<your code>" in result.stderr


def test_run_reports_name_error():
    result = run_code("print(missing_name)\n")

    assert result.error is not None
    assert result.error.type == "NameError"
    assert result.error.line == 1
    assert "not been defined" in result.error.hint


def test_run_times_out_and_reports_a_timeout():
    result = run_code("while True:\n    pass\n", timeout_seconds=0.5)

    assert result.ok is False
    assert result.timed_out is True
    assert result.exit_code is None
    assert result.error is not None
    assert result.error.type == "TimeoutError"
    assert "infinite loop" in result.error.hint


def test_run_clips_large_output():
    result = run_code("print('x' * 5000)\n", max_output_chars=200)

    assert result.ok is True
    assert "output truncated" in result.stdout
    assert len(result.stdout) < 300


def test_check_syntax_returns_none_for_valid_code():
    assert check_syntax("print('ok')\n") is None


def test_check_syntax_reports_tab_error():
    result = check_syntax("if True:\n\tprint('tab')\n        print('spaces')\n")

    assert result is not None
    assert result.type in {"TabError", "IndentationError"}
    assert "tabs" in result.hint.lower() or "indent" in result.hint.lower()
