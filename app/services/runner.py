"""Compile and run Python snippets for the in-browser Run button.

The runner executes code in a short-lived subprocess with a wall-clock timeout
and a hard cap on captured output. That is enough to keep a teaching demo from
hanging the web worker, but it is **not** a hardened security sandbox: the child
process still has the permissions of the server user and can use the network.
For a public deployment, put execution in a container or a dedicated sandbox
service with CPU, memory, filesystem, and network limits.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from app.services.child_env import minimal_child_env

DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_OUTPUT_CHARS = 16_000
_RUNNER_FILENAME = "main.py"

_FRAME_PATTERN = re.compile(
    r'^\s*File "(?P<file>.+?)", line (?P<line>\d+)(?:, in (?P<name>.+))?\s*$'
)
_EXCEPTION_PATTERN = re.compile(
    r"^(?P<type>[A-Za-z_][\w.]*)(?::\s*(?P<message>.*))?$"
)


@dataclass(frozen=True)
class CodeError:
    """A syntax or runtime problem described in learner-friendly terms."""

    type: str
    message: str
    line: int | None
    column: int | None
    hint: str
    phase: str


@dataclass(frozen=True)
class RunResult:
    """Everything the API and UI need to show one execution."""

    ok: bool
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    timed_out: bool
    error: CodeError | None


_SYNTAX_HINTS: tuple[tuple[str, str], ...] = (
    (
        "expected an indented block",
        "The line after a colon must be indented. Add four spaces before the body of the block.",
    ),
    (
        "unexpected indent",
        "This line is indented further than the block around it. Remove the extra indentation.",
    ),
    (
        "unindent does not match any outer indentation level",
        "This line does not line up with an enclosing block. Use four spaces for each level and keep it consistent.",
    ),
    (
        "expected ':'",
        "A block statement such as if, for, while, def, class, try, or with must end with a colon.",
    ),
    (
        "unexpected eof while parsing",
        "A block, bracket, or parenthesis is still open at the end of the file. Close it.",
    ),
    (
        "unterminated string literal",
        "A string is missing its closing quote.",
    ),
    (
        "unmatched ')'",
        "There is an extra closing parenthesis. Remove it or add the matching opening one.",
    ),
    (
        "cannot assign to literal",
        "The left side of the assignment must be a variable or another assignable target.",
    ),
    (
        "leading zeros in decimal integer literals are not permitted",
        "Remove the leading zero, or use 0o before the digits for an octal number.",
    ),
    (
        "missing parentheses in call to 'print'",
        "This looks like Python 2. Call print as a function, for example print(value).",
    ),
    (
        "invalid character",
        "The code contains a character Python does not accept outside a string or comment.",
    ),
    (
        "invalid syntax",
        "Look near the marked line for a missing bracket, quote, comma, or colon.",
    ),
)

_RUNTIME_HINTS: dict[str, str] = {
    "NameError": (
        "Python reached a name that has not been defined. Check the spelling and make sure "
        "the variable is assigned before this line runs."
    ),
    "UnboundLocalError": (
        "This variable is assigned somewhere in the function, so Python treats it as local. "
        "Assign it before using it, or pass it in as an argument."
    ),
    "TypeError": (
        "A value has the wrong type, or a function received the wrong number of arguments. "
        "Check the values and the call on this line."
    ),
    "ValueError": (
        "A function received a value it cannot use. Check the input on this line."
    ),
    "ZeroDivisionError": (
        "Something is being divided by zero. Guard the denominator before dividing."
    ),
    "IndexError": (
        "The index is outside the list or string. Check its length and your loop bounds."
    ),
    "KeyError": (
        "That key is not in the dictionary. Check the spelling, or use .get() with a default."
    ),
    "AttributeError": (
        "The object does not have that attribute or method. Check its type and the spelling."
    ),
    "FileNotFoundError": (
        "The file path does not exist relative to the runner's temporary directory."
    ),
    "ModuleNotFoundError": (
        "That module is not installed in the runner environment. Standard-library modules "
        "are available; third-party packages may not be."
    ),
    "ImportError": (
        "The module or name could not be imported. Check the import statement."
    ),
    "RecursionError": (
        "A function keeps calling itself without reaching a stopping condition."
    ),
    "AssertionError": (
        "An assert statement was false. Check the condition you expected to hold."
    ),
    "TimeoutError": (
        "The program did not finish in time. Look for an infinite loop or a very slow operation."
    ),
    "MemoryError": (
        "The program tried to use more memory than the runner allows. Check for huge lists "
        "or runaway loops."
    ),
}


def _syntax_hint(message: str, error_type: str) -> str:
    text = message.lower()
    if error_type == "TabError" or "inconsistent use of tabs" in text:
        return (
            "This file mixes tabs and spaces. Use spaces (four per indent level) "
            "consistently, then try again."
        )
    for needle, hint in _SYNTAX_HINTS:
        if needle in text:
            return hint
    if error_type == "IndentationError":
        return (
            "Python uses indentation to define blocks. Align each line with the block it "
            "belongs to, using four spaces per level."
        )
    return (
        "Look near the marked line for a missing colon, bracket, quote, or comma, and "
        "check that the indentation matches the surrounding block."
    )


def _runtime_hint(error_type: str) -> str:
    return _RUNTIME_HINTS.get(
        error_type,
        "Read the last line of the traceback: it names the exception and the line where "
        "the program stopped.",
    )


def _syntax_error(exc: SyntaxError, code: str) -> CodeError:
    error_type = type(exc).__name__
    message = exc.msg or str(exc)
    return CodeError(
        type=error_type,
        message=message,
        line=exc.lineno or 1,
        column=exc.offset,
        hint=_syntax_hint(message, error_type),
        phase="syntax",
    )


def check_syntax(code: str) -> CodeError | None:
    """Return a friendly syntax problem, or ``None`` when the code compiles."""

    try:
        compile(code, "<linelens>", "exec")
    except SyntaxError as exc:
        return _syntax_error(exc, code)
    except ValueError as exc:
        return CodeError(
            type="ValueError",
            message=str(exc),
            line=1,
            column=None,
            hint=(
                "The code contains characters Python cannot parse. Remove null bytes or "
                "unusual control characters."
            ),
            phase="syntax",
        )
    return None


def format_syntax_error(code: str, error: CodeError) -> str:
    """Build a compact, traceback-like message for the console."""

    source_lines = code.splitlines()
    line_number = error.line or 1
    source = source_lines[line_number - 1] if 0 < line_number <= len(source_lines) else ""
    expanded = source.expandtabs(4)
    parts = [f'  File "<your code>", line {line_number}']
    if source:
        parts.append(f"    {expanded}")
    if error.column and error.column > 0:
        prefix = source[: error.column - 1].expandtabs(4)
        parts.append(f"    {' ' * len(prefix)}^")
    parts.append(f"{error.type}: {error.message}")
    return "\n".join(parts)


def _clean_paths(text: str, script: Path, directory: str) -> str:
    return text.replace(str(script), "<your code>").replace(directory, "<run>")


def _runtime_error(stderr: str, script: Path) -> CodeError:
    lines = [line.rstrip() for line in stderr.splitlines() if line.strip()]
    exception_type = "Exception"
    message = "The program stopped with an error."

    for line in reversed(lines):
        match = _EXCEPTION_PATTERN.match(line.strip())
        if not match:
            continue
        candidate = match.group("type").split(".")[-1]
        if candidate.endswith(("Error", "Exception", "Exit", "Interrupt")):
            exception_type = candidate
            message = match.group("message") or candidate
            break

    line_number: int | None = None
    for line in reversed(lines):
        match = _FRAME_PATTERN.match(line)
        if match and Path(match.group("file")) == script:
            line_number = int(match.group("line"))
            break

    return CodeError(
        type=exception_type,
        message=message,
        line=line_number,
        column=None,
        hint=_runtime_hint(exception_type),
        phase="runtime",
    )


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... output truncated ...\n"


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_code(
    code: str,
    stdin: str = "",
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
) -> RunResult:
    """Compile and execute ``code``, returning output and a diagnosis.

    Syntax errors are reported without starting a process. Valid programs run in
    an isolated Python process inside a temporary directory. Output is clipped so
    one runaway print loop cannot fill the database or the browser.
    """

    syntax_error = check_syntax(code)
    if syntax_error is not None:
        return RunResult(
            ok=False,
            stdout="",
            stderr=format_syntax_error(code, syntax_error),
            exit_code=None,
            duration_ms=0,
            timed_out=False,
            error=syntax_error,
        )

    with tempfile.TemporaryDirectory(prefix="linelens-run-") as directory:
        script = Path(directory) / _RUNNER_FILENAME
        script.write_text(code, encoding="utf-8")
        started = time.perf_counter()

        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-B", "-X", "utf8", str(script)],
                cwd=directory,
                env=minimal_child_env(),
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            stdout = _clip(_as_text(exc.stdout), max_output_chars)
            stderr = _clip(_as_text(exc.stderr), max_output_chars)
            error = CodeError(
                type="TimeoutError",
                message=f"Execution exceeded the {timeout_seconds:g}-second limit.",
                line=None,
                column=None,
                hint=_runtime_hint("TimeoutError"),
                phase="runtime",
            )
            return RunResult(
                ok=False,
                stdout=stdout,
                stderr=stderr,
                exit_code=None,
                duration_ms=duration_ms,
                timed_out=True,
                error=error,
            )
        except OSError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            error = CodeError(
                type=type(exc).__name__,
                message=str(exc),
                line=None,
                column=None,
                hint="The runner could not start Python. Check the server environment.",
                phase="runtime",
            )
            return RunResult(
                ok=False,
                stdout="",
                stderr=str(exc),
                exit_code=None,
                duration_ms=duration_ms,
                timed_out=False,
                error=error,
            )

        duration_ms = int((time.perf_counter() - started) * 1000)
        stdout = _clip(completed.stdout or "", max_output_chars)
        raw_stderr = completed.stderr or ""
        error = _runtime_error(raw_stderr, script) if completed.returncode != 0 else None
        stderr = _clip(_clean_paths(raw_stderr, script, directory), max_output_chars)

        return RunResult(
            ok=completed.returncode == 0,
            stdout=stdout,
            stderr=stderr,
            exit_code=completed.returncode,
            duration_ms=duration_ms,
            timed_out=False,
            error=error,
        )
