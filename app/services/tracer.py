"""Record a real Python execution so explanations can use real values.

The tracer runs the learner's program in a separate, short-lived Python process.
The child compiles the source with the filename ``<user_code>`` and installs a
``sys.settrace`` hook that only follows frames from that file. The parent never
executes user code itself.

This is a teaching tool, not a security sandbox. The child removes the most
obvious dangerous builtins and restricts imports, but a determined program can
still consume CPU until the wall-clock timeout. For a public deployment, run
this module inside a container or a dedicated sandbox service.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 3.0
DEFAULT_MAX_EVENTS = 500
DEFAULT_MAX_OUTPUT_CHARS = 10_000
DEFAULT_MEMORY_LIMIT_MB = 256

USER_CODE_FILENAME = "<user_code>"

#: Modules a learner may import. Everything else is rejected with a clear note.
ALLOWED_IMPORTS = frozenset(
    {
        "math",
        "random",
        "time",
        "datetime",
        "json",
        "re",
        "collections",
        "itertools",
        "functools",
        "string",
        "typing",
        "dataclasses",
        "statistics",
        "decimal",
        "fractions",
        "heapq",
        "bisect",
        "operator",
        "copy",
    }
)


@dataclass
class TraceStep:
    """One observable moment in execution order."""

    step: int
    line: int
    function: str
    depth: int
    event: str
    variables: dict[str, str | None] = field(default_factory=dict)
    stdout: str = ""
    failing: bool = False


@dataclass
class TraceError:
    """An uncaught exception that stopped the program."""

    type: str
    message: str
    line: int | None = None


@dataclass
class TraceResult:
    """The complete result of one tracing attempt."""

    status: str
    note: str | None
    steps: list[TraceStep] = field(default_factory=list)
    final_output: str = ""
    run_counts: dict[int, int] = field(default_factory=dict)
    run_steps: dict[int, list[int]] = field(default_factory=dict)
    error: TraceError | None = None
    truncated: bool = False
    duration_ms: int = 0

    @property
    def available(self) -> bool:
        return self.status == "ok"

    def to_storage(self) -> dict[str, Any]:
        """Return a JSON-friendly payload for the explanation row."""

        return {
            "steps": [asdict(step) for step in self.steps],
            "error": asdict(self.error) if self.error is not None else None,
        }

    def to_prompt(self) -> dict[str, Any]:
        """Return a compact trace for the model prompt.

        The model only needs the execution order, the changed values, and the
        output. Long key names are shortened to keep the prompt small.
        """

        compact_steps: list[dict[str, Any]] = []
        for step in self.steps:
            item: dict[str, Any] = {"line": step.line, "fn": step.function}
            if step.event != "line":
                item["event"] = step.event
            if step.variables:
                item["changed"] = step.variables
            if step.stdout:
                item["out"] = step.stdout
            if step.failing:
                item["failed"] = True
            compact_steps.append(item)
        return {
            "final_output": self.final_output,
            "error": asdict(self.error) if self.error is not None else None,
            "steps": compact_steps,
        }


def _memory_limiter(limit_mb: int):
    """Return a preexec hook that caps address space, or ``None`` when unsupported."""

    try:
        import resource
    except ImportError:  # Windows has no resource module; skip safely.
        return None

    limit = getattr(resource, "RLIMIT_AS", None)
    if limit is None:
        return None
    byte_limit = max(1, limit_mb) * 1024 * 1024

    def apply_limit() -> None:
        resource.setrlimit(limit, (byte_limit, byte_limit))

    return apply_limit


def _unavailable(note: str, *, final_output: str = "", duration_ms: int = 0) -> TraceResult:
    return TraceResult(
        status="unavailable",
        note=note,
        final_output=final_output,
        duration_ms=duration_ms,
    )


def _coerce_steps(raw_steps: Any) -> list[TraceStep]:
    steps: list[TraceStep] = []
    if not isinstance(raw_steps, list):
        return steps
    for raw in raw_steps:
        if not isinstance(raw, dict):
            continue
        try:
            variables = raw.get("variables") or {}
            if not isinstance(variables, dict):
                variables = {}
            steps.append(
                TraceStep(
                    step=int(raw.get("step", len(steps) + 1)),
                    line=int(raw.get("line", 0)),
                    function=str(raw.get("function", "<module>")),
                    depth=int(raw.get("depth", 0)),
                    event=str(raw.get("event", "line")),
                    variables={
                        str(name): (None if value is None else str(value))
                        for name, value in variables.items()
                    },
                    stdout=str(raw.get("stdout", "")),
                    failing=bool(raw.get("failing", False)),
                )
            )
        except (TypeError, ValueError):
            continue
    return steps


def _coerce_error(raw: Any) -> TraceError | None:
    if not isinstance(raw, dict) or not raw.get("type"):
        return None
    line = raw.get("line")
    try:
        line_number = int(line) if line is not None else None
    except (TypeError, ValueError):
        line_number = None
    return TraceError(
        type=str(raw.get("type")),
        message=str(raw.get("message", "")),
        line=line_number,
    )


def trace_code(
    code: str,
    stdin: str = "",
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_events: int = DEFAULT_MAX_EVENTS,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
) -> TraceResult:
    """Execute ``code`` in a child process and return its execution trace.

    The function always returns a :class:`TraceResult`; it never raises for a
    problem in the learner's code. A caller can therefore always fall back to a
    structure-only explanation when ``status`` is ``"unavailable"``.
    """

    timeout_seconds = max(0.05, float(timeout_seconds))
    max_events = max(1, int(max_events))
    max_output_chars = max(1, int(max_output_chars))
    memory_limit_mb = max(1, int(memory_limit_mb))

    with tempfile.TemporaryDirectory(prefix="linelens-trace-") as directory:
        working_dir = Path(directory)
        source_path = working_dir / "source.txt"
        script_path = working_dir / "tracer_child.py"
        result_path = working_dir / "result.json"
        source_path.write_text(code, encoding="utf-8")
        script_path.write_text(_CHILD_SCRIPT, encoding="utf-8")

        command = [
            sys.executable,
            "-I",
            "-B",
            "-X",
            "utf8",
            str(script_path),
            str(source_path),
            str(result_path),
            str(max_events),
            str(max_output_chars),
        ]
        run_options: dict[str, Any] = {
            "cwd": directory,
            # A fresh child with no inherited environment variables.
            "env": {},
            "input": stdin,
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": timeout_seconds,
            "check": False,
        }
        preexec = _memory_limiter(memory_limit_mb) if os.name == "posix" else None
        if preexec is not None:
            run_options["preexec_fn"] = preexec

        started = time.perf_counter()
        try:
            completed = subprocess.run(command, **run_options)
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            partial = exc.stdout if isinstance(exc.stdout, str) else ""
            return _unavailable(
                (
                    f"Tracing stopped after {timeout_seconds:g} seconds. "
                    "The program may contain an infinite loop or a very slow operation."
                ),
                final_output=partial[:max_output_chars],
                duration_ms=duration_ms,
            )
        except OSError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.warning("Tracer could not start: %s", type(exc).__name__)
            return _unavailable(
                "The tracer could not start Python in this environment.",
                duration_ms=duration_ms,
            )

        duration_ms = int((time.perf_counter() - started) * 1000)
        if not result_path.exists():
            logger.warning(
                "Tracer produced no result (exit %s): %s",
                completed.returncode,
                (completed.stderr or "")[:1000],
            )
            return _unavailable(
                "The tracer crashed before it could record a trace.",
                duration_ms=duration_ms,
            )

        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Tracer result could not be read.")
            return _unavailable(
                "The tracer returned an unreadable result.",
                duration_ms=duration_ms,
            )

        if not isinstance(payload, dict):
            return _unavailable("The tracer returned an invalid result.", duration_ms=duration_ms)

        status = str(payload.get("status", "unavailable"))
        note_value = payload.get("note")
        note = str(note_value) if note_value else None
        final_output = str(payload.get("final_output", ""))[:max_output_chars]
        error = _coerce_error(payload.get("error"))
        truncated = bool(payload.get("truncated", False))

        if status != "ok":
            return TraceResult(
                status="unavailable",
                note=note or "Execution tracing was unavailable for this program.",
                final_output=final_output,
                error=error,
                truncated=truncated,
                duration_ms=duration_ms,
            )

        steps = _coerce_steps(payload.get("steps"))
        run_counts: dict[int, int] = {}
        run_steps: dict[int, list[int]] = {}
        for step in steps:
            if step.event != "line" or step.line <= 0:
                continue
            run_counts[step.line] = run_counts.get(step.line, 0) + 1
            bucket = run_steps.setdefault(step.line, [])
            if len(bucket) < 5:
                bucket.append(step.step)

        if error is not None and error.line is not None:
            for step in reversed(steps):
                if step.event == "line" and step.line == error.line:
                    step.failing = True
                    break

        return TraceResult(
            status="ok",
            note=note,
            steps=steps,
            final_output=final_output,
            run_counts=run_counts,
            run_steps=run_steps,
            error=error,
            truncated=truncated,
            duration_ms=duration_ms,
        )


# The child is deliberately self-contained. It is written to a temporary
# directory and started with ``-I -B`` so it cannot import the application or
# write bytecode next to the source.
_CHILD_SCRIPT = r'''"""Isolated trace collector used by app.services.tracer."""
import builtins
import json
import reprlib
import sys
import types

FILENAME = "<user_code>"
ALLOWED = {
    "math", "random", "time", "datetime", "json", "re", "collections",
    "itertools", "functools", "string", "typing", "dataclasses", "statistics",
    "decimal", "fractions", "heapq", "bisect", "operator", "copy",
}

MAX_EVENTS = 500
MAX_OUTPUT = 10000
capture = None


class _TraceLimit(BaseException):
    pass


class _OutputLimit(BaseException):
    pass


class _BlockedImport(ImportError):
    pass


class _Capture:
    """A tiny stdout replacement that enforces the output cap."""

    def __init__(self, limit):
        self.limit = limit
        self.parts = []
        self.length = 0
        self.stopped = False
        self.truncated = False

    def write(self, text):
        if self.stopped:
            return 0
        if not isinstance(text, str):
            text = str(text)
        remaining = self.limit - self.length
        if remaining <= 0:
            self._stop()
        if len(text) > remaining:
            self.parts.append(text[:remaining])
            self.length += remaining
            self._stop()
        self.parts.append(text)
        self.length += len(text)
        return len(text)

    def _stop(self):
        self.stopped = True
        self.truncated = True
        sys.settrace(None)
        raise _OutputLimit()

    def flush(self):
        pass

    def isatty(self):
        return False

    def getvalue(self):
        return "".join(self.parts)


_repr = reprlib.Repr()
_repr.maxlevel = 2
_repr.maxlist = 3
_repr.maxtuple = 3
_repr.maxdict = 3
_repr.maxset = 3
_repr.maxfrozenset = 3
_repr.maxstring = 60
_repr.maxother = 80


def _safe_repr(value):
    if isinstance(value, types.FunctionType):
        return "<function %s>" % value.__name__
    if isinstance(value, types.BuiltinFunctionType):
        return "<built-in function %s>" % value.__name__
    if isinstance(value, type):
        return "<class %s>" % value.__name__
    try:
        text = _repr.repr(value)
    except Exception:
        return "<%s>" % type(value).__name__
    if len(text) > 80:
        text = text[:79] + "..."
    return text


def _snapshot(locals_dict):
    snapshot = {}
    for name, value in list(locals_dict.items()):
        if name.startswith("__") and name.endswith("__"):
            continue
        if isinstance(value, types.ModuleType):
            continue
        if isinstance(value, (types.BuiltinFunctionType, types.BuiltinMethodType)):
            continue
        snapshot[name] = _safe_repr(value)
    return snapshot


steps = []
state = {
    "snapshots": {},
    "last": {},
    "depths": {},
    "stack": [],
    "exception_frames": set(),
    "error": None,
    "cursor": 0,
    "truncated": False,
    "note": None,
    "blocked": None,
}
stdin_state = {"lines": [], "index": 0}


def _flush(frame):
    key = id(frame)
    previous = state["snapshots"].get(key)
    if previous is None:
        return
    current = _snapshot(frame.f_locals)
    changed = {}
    for name, value in current.items():
        if previous.get(name) != value:
            changed[name] = value
    for name in previous:
        if name not in current:
            changed[name] = None
    index = state["last"].get(key)
    if index is not None:
        step = steps[index]
        if changed:
            step["variables"].update(changed)
        full_output = capture.getvalue()
        new_output = full_output[state["cursor"]:]
        if new_output:
            step["stdout"] += new_output
            state["cursor"] = len(full_output)
    state["snapshots"][key] = current


def _limit_reached():
    state["truncated"] = True
    state["note"] = "Tracing stopped after %d steps." % MAX_EVENTS
    sys.settrace(None)
    raise _TraceLimit()


def _record_line(frame):
    key = id(frame)
    steps.append({
        "step": len(steps) + 1,
        "line": frame.f_lineno,
        "function": frame.f_code.co_name,
        "depth": state["depths"].get(key, 0),
        "event": "line",
        "variables": {},
        "stdout": "",
        "failing": False,
    })
    state["last"][key] = len(steps) - 1
    if len(steps) >= MAX_EVENTS:
        _limit_reached()


def _record_return(frame, value):
    if frame.f_code.co_name == "<module>":
        return
    key = id(frame)
    variables = {}
    if value is not None:
        variables["return"] = _safe_repr(value)
    steps.append({
        "step": len(steps) + 1,
        "line": frame.f_lineno,
        "function": frame.f_code.co_name,
        "depth": state["depths"].get(key, 0),
        "event": "return",
        "variables": variables,
        "stdout": "",
        "failing": False,
    })
    if len(steps) >= MAX_EVENTS:
        _limit_reached()


def _pop(frame):
    key = id(frame)
    state["snapshots"].pop(key, None)
    state["last"].pop(key, None)
    state["exception_frames"].discard(key)
    if state["stack"] and state["stack"][-1] == key:
        state["stack"].pop()
    else:
        try:
            state["stack"].remove(key)
        except ValueError:
            pass
    state["depths"].pop(key, None)


def _local_trace(frame, event, arg):
    key = id(frame)
    if event == "line":
        _flush(frame)
        state["error"] = None
        state["exception_frames"].discard(key)
        _record_line(frame)
        return _local_trace
    if event == "return":
        _flush(frame)
        if not (key in state["exception_frames"] and arg is None):
            _record_return(frame, arg)
        _pop(frame)
        return None
    if event == "exception":
        _flush(frame)
        state["exception_frames"].add(key)
        exc_type, exc_value, _ = arg
        if state["error"] is None:
            state["error"] = {
                "type": exc_type.__name__,
                "message": str(exc_value)[:500],
                "line": frame.f_lineno,
            }
        return _local_trace
    return _local_trace


def _global_trace(frame, event, arg):
    if frame.f_code.co_filename != FILENAME:
        return None
    if event == "call":
        key = id(frame)
        state["stack"].append(key)
        state["depths"][key] = len(state["stack"]) - 1
        state["snapshots"][key] = _snapshot(frame.f_locals)
        state["last"][key] = None
        state["exception_frames"].discard(key)
        return _local_trace
    return None


def _safe_input(prompt=""):
    if prompt:
        capture.write(str(prompt))
    if stdin_state["index"] >= len(stdin_state["lines"]):
        raise EOFError("EOF when reading a line")
    line = stdin_state["lines"][stdin_state["index"]]
    stdin_state["index"] += 1
    if line.endswith("\n"):
        line = line[:-1]
    if line.endswith("\r"):
        line = line[:-1]
    return line


def _make_import_hook():
    real_import = builtins.__import__

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        caller = sys._getframe(1)
        if caller.f_code.co_filename == FILENAME:
            root = name.split(".")[0]
            if level != 0 or root not in ALLOWED:
                state["blocked"] = root or name
                raise _BlockedImport(
                    "The module '%s' is not allowed in LineLens. Allowed modules: %s."
                    % (root or name, ", ".join(sorted(ALLOWED)))
                )
        return real_import(name, globals, locals, fromlist, level)

    return _guarded_import


def _safe_builtins():
    safe = {}
    removed = {"open", "exec", "eval", "compile", "breakpoint", "__import__"}
    for name, value in vars(builtins).items():
        if name in removed:
            continue
        safe[name] = value
    safe["__import__"] = _make_import_hook()
    safe["input"] = _safe_input
    return safe


def _user_line_from_tb(tb):
    line = None
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == FILENAME:
            line = tb.tb_lineno
        tb = tb.tb_next
    return line


def _write_result(path, payload):
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
    except Exception:
        pass


def main():
    global MAX_EVENTS, MAX_OUTPUT, capture
    source_path = sys.argv[1]
    result_path = sys.argv[2]
    if len(sys.argv) > 3:
        MAX_EVENTS = max(1, int(sys.argv[3]))
    if len(sys.argv) > 4:
        MAX_OUTPUT = max(1, int(sys.argv[4]))

    capture = _Capture(MAX_OUTPUT)
    source = open(source_path, encoding="utf-8").read()

    try:
        code_object = compile(source, FILENAME, "exec")
    except SyntaxError as exc:
        _write_result(result_path, {
            "status": "unavailable",
            "note": "The code has a syntax error on line %s: %s"
                    % (exc.lineno or 1, exc.msg or str(exc)),
            "steps": [],
            "final_output": "",
            "error": None,
            "truncated": False,
            "blocked_import": None,
        })
        return

    try:
        raw_stdin = sys.stdin.read()
    except Exception:
        raw_stdin = ""
    stdin_state["lines"] = raw_stdin.splitlines(keepends=True)

    user_globals = {
        "__name__": "__main__",
        "__doc__": None,
        "__package__": None,
        "__spec__": None,
        "__builtins__": _safe_builtins(),
    }

    real_stdout = sys.stdout
    sys.stdout = capture
    status = "ok"
    note = None
    sys.settrace(_global_trace)
    try:
        exec(code_object, user_globals)
    except _BlockedImport as exc:
        status = "unavailable"
        note = str(exc)
        state["error"] = None
    except SystemExit:
        state["error"] = None
    except _TraceLimit:
        state["error"] = None
    except _OutputLimit:
        state["error"] = None
    except BaseException as exc:
        if state["error"] is None:
            state["error"] = {
                "type": type(exc).__name__,
                "message": str(exc)[:500],
                "line": _user_line_from_tb(exc.__traceback__),
            }
    finally:
        sys.settrace(None)
        sys.stdout = real_stdout

        full_output = capture.getvalue()
        if steps and state["cursor"] < len(full_output):
            steps[-1]["stdout"] += full_output[state["cursor"]:]
            state["cursor"] = len(full_output)

        if state["error"] is not None:
            error_line = state["error"].get("line")
            for step in reversed(steps):
                if step["event"] == "line" and step["line"] == error_line:
                    step["failing"] = True
                    break

        if status == "ok" and note is None:
            if capture.truncated:
                state["truncated"] = True
                note = "Output was capped at %d characters, so tracing stopped." % MAX_OUTPUT
            elif state["truncated"]:
                note = state["note"]
            elif state["error"] is not None:
                error = state["error"]
                location = " on line %s" % error["line"] if error["line"] else ""
                note = "The program raised %s%s: %s" % (
                    error["type"], location, error["message"]
                )

        _write_result(result_path, {
            "status": status,
            "note": note,
            "steps": [] if status != "ok" else steps,
            "final_output": full_output,
            "error": state["error"],
            "truncated": state["truncated"],
            "blocked_import": state["blocked"],
        })


main()
'''
