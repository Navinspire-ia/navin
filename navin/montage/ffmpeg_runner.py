# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Safe, cross-platform process runner for FFmpeg and FFprobe."""

from __future__ import annotations

import asyncio
import re
import shlex
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from navin.utils.proc import no_window_kwargs

_SECRET_FLAGS = frozenset(
    {
        "-api_key",
        "--api-key",
        "-authorization",
        "--authorization",
        "-headers",
        "--headers",
        "-password",
        "--password",
        "-token",
        "--token",
    }
)
_SECRET_KEYS = re.compile(r"(?i)(api[-_]?key|authorization|bearer|password|secret|signature|token)")
_ASSIGNMENT = re.compile(
    r"(?i)\b(api[-_]?key|authorization|password|secret|signature|token)=([^\s,;&]+)"
)


@dataclass(frozen=True, slots=True)
class ProcessError:
    kind: str
    message: str
    exit_code: int | None = None
    stderr: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    ok: bool
    argv: tuple[str, ...]
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    raw_stderr: str
    duration_s: float
    timed_out: bool = False
    error: ProcessError | None = None
    #: The caller asked for the process to stop (a cancelled render), as
    #: opposed to the process failing on its own.
    cancelled: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["argv"] = list(self.argv)
        data.pop("raw_stderr", None)
        return data


def redact_argument(value: str) -> str:
    """Remove common credentials while preserving a useful command shape."""
    value = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}=<redacted>", value)
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or not parsed.netloc:
        return value
    hostname = parsed.hostname or ""
    netloc = hostname
    if parsed.port:
        netloc += f":{parsed.port}"
    if parsed.username or parsed.password:
        netloc = f"<redacted>@{netloc}"
    query = [
        (key, "<redacted>" if _SECRET_KEYS.search(key) else item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunsplit((parsed.scheme, netloc, parsed.path, urlencode(query), parsed.fragment))


def redact_argv(argv: Sequence[str]) -> tuple[str, ...]:
    out: list[str] = []
    hide_next = False
    for raw in argv:
        value = str(raw)
        if hide_next:
            out.append("<redacted>")
            hide_next = False
            continue
        out.append(redact_argument(value))
        hide_next = value.lower() in _SECRET_FLAGS
    return tuple(out)


#: Per-stream metadata ffmpeg prints for every input; it buries the one line
#: that says what went wrong (a five-clip edit prints dozens of these).
_STREAM_NOISE_PREFIXES = (
    "stream #",
    "metadata:",
    "handler_name",
    "vendor_id",
    "encoder ",
    "encoder:",
    "major_brand",
    "minor_version",
    "compatible_brands",
    "creation_time",
    "side data:",
    "displaymatrix",
    "guessed channel layout",
)


def _is_stream_noise(line: str) -> bool:
    lowered = line.lower()
    return lowered.startswith(_STREAM_NOISE_PREFIXES) or lowered.startswith("input #")


def useful_stderr(stderr: str, max_lines: int = 12) -> str:
    """Return the actionable tail of FFmpeg output without its banner."""
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    if not lines:
        return ""
    trimmed = [line for line in lines if not _is_stream_noise(line)]
    if trimmed:
        lines = trimmed
    markers = (
        "error",
        "failed",
        "invalid",
        "not found",
        "no such",
        "permission denied",
        "unknown",
        "unable",
    )
    marked = [index for index, line in enumerate(lines) if any(m in line.lower() for m in markers)]
    start = max(0, (marked[-1] if marked else len(lines) - 1) - max_lines + 1)
    return "\n".join(lines[start:][-max_lines:])


def _result(
    argv: Sequence[str],
    *,
    started: float,
    exit_code: int | None,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    exception: OSError | None = None,
    cancelled: bool = False,
) -> ProcessResult:
    safe = redact_argv(argv)
    useful = useful_stderr(stderr)
    error = None
    if exception is not None:
        error = ProcessError("spawn", str(exception), stderr=useful)
    elif cancelled:
        error = ProcessError("cancelled", "process cancelled", exit_code, useful)
    elif timed_out:
        error = ProcessError("timeout", "process timed out", exit_code, useful)
    elif exit_code != 0:
        error = ProcessError(
            "exit",
            useful or f"process exited with code {exit_code}",
            exit_code,
            useful,
        )
    return ProcessResult(
        ok=error is None,
        argv=safe,
        command=shlex.join(safe),
        exit_code=exit_code,
        stdout=stdout,
        stderr=useful,
        raw_stderr=stderr,
        duration_s=round(max(0.0, time.monotonic() - started), 3),
        timed_out=timed_out,
        error=error,
        cancelled=cancelled,
    )


LineCallback = Callable[[str], None]


async def run_process(
    argv: Sequence[str],
    *,
    timeout_s: float = 900.0,
    capture_stdout: bool = True,
    on_stdout_line: LineCallback | None = None,
    cancel: asyncio.Event | None = None,
) -> ProcessResult:
    """Run an argv asynchronously with timeout and Windows no-window flags.

    ``on_stdout_line`` receives each stdout line as it arrives, which is how
    ffmpeg ``-progress pipe:1`` reports are turned into live progress; the
    stdout text is still returned on the result. Setting ``cancel`` kills the
    process and marks the result ``cancelled`` instead of failed.
    """
    args = tuple(str(item) for item in argv)
    started = time.monotonic()
    stream_stdout = on_stdout_line is not None
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=(
                asyncio.subprocess.PIPE
                if capture_stdout or stream_stdout
                else asyncio.subprocess.DEVNULL
            ),
            stderr=asyncio.subprocess.PIPE,
            **no_window_kwargs(),
        )
    except OSError as exc:
        return _result(args, started=started, exit_code=None, exception=exc)

    if not stream_stdout and cancel is None:
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
        except TimeoutError:
            process.kill()
            out, err = await process.communicate()
            return _result(
                args,
                started=started,
                exit_code=process.returncode,
                stdout=(out or b"").decode("utf-8", "replace"),
                stderr=(err or b"").decode("utf-8", "replace"),
                timed_out=True,
            )
        return _result(
            args,
            started=started,
            exit_code=process.returncode,
            stdout=(out or b"").decode("utf-8", "replace"),
            stderr=(err or b"").decode("utf-8", "replace"),
        )

    async def pump_stdout() -> bytes:
        if process.stdout is None:
            return b""
        chunks: list[bytes] = []
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            chunks.append(line)
            if on_stdout_line is not None:
                try:
                    on_stdout_line(line.decode("utf-8", "replace").rstrip("\r\n"))
                except Exception:  # noqa: BLE001 - a progress listener must not kill the render
                    pass
        return b"".join(chunks)

    async def read_stderr() -> bytes:
        if process.stderr is None:
            return b""
        return await process.stderr.read()

    io_task = asyncio.ensure_future(asyncio.gather(pump_stdout(), read_stderr()))
    waiters: set[asyncio.Future[Any]] = {io_task}
    cancel_waiter: asyncio.Future[Any] | None = None
    if cancel is not None:
        cancel_waiter = asyncio.ensure_future(cancel.wait())
        waiters.add(cancel_waiter)
    done, _pending = await asyncio.wait(
        waiters, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
    )
    cancelled = cancel_waiter is not None and cancel_waiter in done
    timed_out = not done
    if cancelled or timed_out:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    if cancel_waiter is not None and not cancel_waiter.done():
        cancel_waiter.cancel()
    out, err = await io_task
    await process.wait()
    return _result(
        args,
        started=started,
        exit_code=process.returncode,
        stdout=(out or b"").decode("utf-8", "replace"),
        stderr=(err or b"").decode("utf-8", "replace"),
        timed_out=timed_out,
        cancelled=cancelled,
    )


def run_process_sync(
    argv: Sequence[str],
    *,
    timeout_s: float = 900.0,
    capture_stdout: bool = True,
) -> ProcessResult:
    """Synchronous counterpart used by existing non-async montage helpers."""
    args = tuple(str(item) for item in argv)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            args,
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
            check=False,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        return _result(
            args,
            started=started,
            exit_code=None,
            stdout=(exc.stdout or b"").decode("utf-8", "replace"),
            stderr=(exc.stderr or b"").decode("utf-8", "replace"),
            timed_out=True,
        )
    except OSError as exc:
        return _result(args, started=started, exit_code=None, exception=exc)
    return _result(
        args,
        started=started,
        exit_code=completed.returncode,
        stdout=(completed.stdout or b"").decode("utf-8", "replace"),
        stderr=(completed.stderr or b"").decode("utf-8", "replace"),
    )
