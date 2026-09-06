"""Session support for long-running exec workflows."""

from __future__ import annotations

import asyncio
import codecs
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from navin.agent.command_output import compact_session_body
from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.context import current_request_session_key
from navin.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from navin.utils.proc import kill_posix_process_group, kill_windows_process_tree

DEFAULT_YIELD_MS = 1000
# Poll window for background exec / write_stdin (long compiles, installs).
MAX_YIELD_MS = 300_000
DEFAULT_WAIT_FOR_MS = 10_000
# Emulator cold boot (WSL / -accel off) can take many minutes.
# Ceiling only - out-of-range values are clamped, not rejected.
MAX_WAIT_FOR_MS = 1_800_000
DEFAULT_MAX_OUTPUT_CHARS = 10_000
MAX_OUTPUT_CHARS = 100_000
OUTPUT_DRAIN_GRACE_S = 0.1
# Ceiling on output held between two polls. A chatty dev server left alone for
# half an hour writes far more than any poll will ever return; past this the
# oldest chunks are dropped and counted, instead of growing without bound.
BUFFER_CAP_CHARS = 2_000_000
# Rolling tail kept for non-consuming observers (webui process manager).
TAIL_CAP_CHARS = 8_000


@dataclass(slots=True)
class _SessionPoll:
    output: str
    done: bool
    exit_code: int | None
    elapsed_s: float = 0.0
    timed_out: bool = False
    terminated: bool = False
    stdin_closed: bool = False
    truncated_chars: int = 0


@dataclass(slots=True)
class ExecSessionInfo:
    session_id: str
    command: str
    cwd: str
    elapsed_s: float
    idle_s: float
    remaining_s: float
    returncode: int | None
    owner_session_key: str | None = None


class _ExecSession:
    def __init__(
        self,
        *,
        session_id: str,
        process: asyncio.subprocess.Process,
        command: str,
        cwd: str,
        timeout: int | None,
        owner_session_key: str | None = None,
        on_output: Callable[[str], None] | None = None,
        on_exit: Callable[[int | None], None] | None = None,
        on_finished: Callable[["_ExecSession"], None] | None = None,
    ) -> None:
        self.session_id = session_id
        self.process = process
        self.command = command
        self.cwd = cwd
        self.owner_session_key = owner_session_key
        self.started_at = time.monotonic()
        # timeout None/0 means no limit; an infinite deadline is never reached.
        self.deadline = time.monotonic() + timeout if timeout else float("inf")
        self.last_access = time.monotonic()
        self._chunks: list[str] = []
        self._buffered_chars = 0
        self._dropped_chars = 0
        # Non-consuming rolling tail for observers (process manager UI):
        # unlike _chunks it is never cleared by poll(), only capped.
        self._tail = ""
        self._lock = asyncio.Lock()
        self._timed_out = False
        # Set once a poll has handed the exit to the agent, so the completion
        # announcement does not tell it twice.
        self.exit_reported = False
        # Live taps for the editor's agent-terminal tabs: called as output is
        # read, independent of when the agent next polls the session.
        self._on_output = on_output
        self._stdout_task = asyncio.create_task(self._read_stream(process.stdout, ""))
        self._stderr_task = asyncio.create_task(self._read_stream(process.stderr, "STDERR:\n"))
        self._exit_notify_task = (
            asyncio.create_task(self._notify_exit(on_exit, on_finished))
            if on_exit is not None or on_finished is not None
            else None
        )

    async def _notify_exit(
        self,
        on_exit: Callable[[int | None], None] | None,
        on_finished: Callable[["_ExecSession"], None] | None,
    ) -> None:
        with suppress(Exception):
            await self.process.wait()
            # Let the readers drain what the process wrote before exiting.
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(self._stdout_task, self._stderr_task),
                    timeout=2.0,
                )
        if on_exit is not None:
            with suppress(Exception):
                on_exit(self.process.returncode)
        if on_finished is not None:
            with suppress(Exception):
                on_finished(self)

    async def _read_stream(
        self,
        stream: asyncio.StreamReader | None,
        prefix: str,
    ) -> None:
        if stream is None:
            return
        # Incremental, because read() slices at arbitrary byte offsets: a
        # multi-byte character cut at a 4096-byte boundary decoded chunk by
        # chunk turns into replacement characters.
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        first = True
        while True:
            chunk = await stream.read(4096)
            text = decoder.decode(chunk, final=not chunk)
            if text:
                if prefix and first:
                    text = prefix + text
                    first = False
                async with self._lock:
                    self._chunks.append(text)
                    self._buffered_chars += len(text)
                    while self._buffered_chars > BUFFER_CAP_CHARS and len(self._chunks) > 1:
                        dropped = self._chunks.pop(0)
                        self._buffered_chars -= len(dropped)
                        self._dropped_chars += len(dropped)
                    self._tail = (self._tail + text)[-TAIL_CAP_CHARS:]
                if self._on_output is not None:
                    with suppress(Exception):
                        self._on_output(text)
            if not chunk:
                break

    async def write(self, chars: str) -> str | None:
        if self.process.returncode is not None:
            return "session has already exited"
        if self.process.stdin is None:
            return "session stdin is not available"
        try:
            self.process.stdin.write(chars.encode("utf-8"))
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            return "session stdin is closed"
        return None

    async def close_stdin(self) -> str | None:
        if self.process.returncode is not None:
            return "session has already exited"
        if self.process.stdin is None:
            return "session stdin is not available"
        self.process.stdin.close()
        with suppress(BrokenPipeError, ConnectionResetError):
            await self.process.stdin.wait_closed()
        return None

    async def poll(
        self,
        yield_time_ms: int,
        max_output_chars: int,
        *,
        terminated: bool = False,
        stdin_closed: bool = False,
    ) -> _SessionPoll:
        self.last_access = time.monotonic()
        if yield_time_ms > 0 and self.process.returncode is None:
            wait_s = min(yield_time_ms, MAX_YIELD_MS) / 1000
            remaining_s = self.deadline - time.monotonic()
            if remaining_s <= 0:
                wait_s = 0
            else:
                wait_s = min(wait_s, remaining_s)
            if wait_s > 0:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.process.wait(), timeout=wait_s)

        if self.process.returncode is None and time.monotonic() >= self.deadline:
            self._timed_out = True
            await self.kill()

        if self.process.returncode is not None:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(self._stdout_task, self._stderr_task),
                    timeout=2.0,
                )
            # Safety-net reap after normal exit.
            from navin.agent.tools.shell import _reap_pid
            _reap_pid(self.process.pid)
            self.exit_reported = True
        elif yield_time_ms > 0:
            await self._wait_for_buffered_output()

        async with self._lock:
            output = "".join(self._chunks)
            self._chunks.clear()
            self._buffered_chars = 0
            dropped = self._dropped_chars
            self._dropped_chars = 0

        output, truncated = _truncate_output(output, max_output_chars)
        return _SessionPoll(
            output=output,
            done=self.process.returncode is not None,
            exit_code=self.process.returncode,
            elapsed_s=max(0.0, time.monotonic() - self.started_at),
            timed_out=self._timed_out,
            terminated=terminated,
            stdin_closed=stdin_closed,
            truncated_chars=truncated + dropped,
        )

    @property
    def tail(self) -> str:
        """The last :data:`TAIL_CAP_CHARS` of output, never consumed by polls."""
        return self._tail

    async def kill(self) -> None:
        if self.process.returncode is not None:
            return
        self.process.kill()
        # Long-running sessions are the whole point of this class, and what they
        # run - a dev server, a watcher - is a child of the shell. kill() stops
        # at the shell, so ending a session would leave the server holding its
        # port with no session left to stop it. taskkill /T walks the tree on
        # Windows; killpg covers the group the shell leads on POSIX.
        kill_windows_process_tree(self.process.pid)
        kill_posix_process_group(self.process.pid)
        try:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
        finally:
            # Safety-net waitpid - prevent zombie if asyncio's child watcher
            # did not reap the process (common in containers).
            from navin.agent.tools.shell import _reap_pid
            _reap_pid(self.process.pid)

    async def _wait_for_buffered_output(self) -> None:
        deadline = time.monotonic() + OUTPUT_DRAIN_GRACE_S
        while time.monotonic() < deadline:
            async with self._lock:
                if self._chunks:
                    return
            await asyncio.sleep(0.01)


class ExecSessionManager:
    def __init__(self, *, max_sessions: int = 8, idle_timeout: int = 1800) -> None:
        self.max_sessions = max_sessions
        self.idle_timeout = idle_timeout
        self._sessions: dict[str, _ExecSession] = {}
        self._lock = asyncio.Lock()

    def has_open_sessions(self) -> bool:
        """True while any background session is registered, exited or not.

        Synchronous and lock-free on purpose: the prompt builder asks this once
        per turn to decide whether ``write_stdin`` ships, and a session whose
        process has exited but was never polled still has output to hand over.
        """
        return bool(self._sessions)

    async def start(
        self,
        *,
        command: str,
        cwd: str,
        env: dict[str, str],
        timeout: int | None,
        shell_program: str | None,
        login: bool,
        yield_time_ms: int,
        max_output_chars: int,
        owner_session_key: str | None = None,
        on_output: Callable[[str], None] | None = None,
        on_exit: Callable[[int | None], None] | None = None,
        on_finished: Callable[[_ExecSession], None] | None = None,
    ) -> tuple[str, _SessionPoll]:
        async with self._lock:
            await self._cleanup_locked()
            if len(self._sessions) >= self.max_sessions:
                raise RuntimeError(f"maximum exec sessions reached ({self.max_sessions})")
            process = await self._spawn(command, cwd, env, shell_program, login)
            session_id = uuid.uuid4().hex[:12]
            session = _ExecSession(
                session_id=session_id,
                process=process,
                command=command,
                cwd=cwd,
                timeout=timeout,
                owner_session_key=owner_session_key,
                on_output=on_output,
                on_exit=on_exit,
                on_finished=on_finished,
            )
            self._sessions[session_id] = session

        poll = await session.poll(yield_time_ms, max_output_chars)
        if poll.done:
            async with self._lock:
                self._sessions.pop(session_id, None)
        return session_id, poll

    async def adopt(
        self,
        *,
        process: asyncio.subprocess.Process,
        command: str,
        cwd: str,
        timeout: int | None,
        owner_session_key: str | None = None,
        on_output: Callable[[str], None] | None = None,
        on_exit: Callable[[int | None], None] | None = None,
        on_finished: Callable[[_ExecSession], None] | None = None,
    ) -> str:
        """Take over a process another code path already spawned.

        This is how a foreground ``exec`` that outlives its window keeps running
        instead of being killed: the readers pick up the pipes where the
        foreground pump stopped, so nothing the process prints is lost, and
        ``write_stdin`` can poll or terminate it like any other session.
        """
        async with self._lock:
            await self._cleanup_locked()
            if len(self._sessions) >= self.max_sessions:
                raise RuntimeError(f"maximum exec sessions reached ({self.max_sessions})")
            session_id = uuid.uuid4().hex[:12]
            self._sessions[session_id] = _ExecSession(
                session_id=session_id,
                process=process,
                command=command,
                cwd=cwd,
                timeout=timeout,
                owner_session_key=owner_session_key,
                on_output=on_output,
                on_exit=on_exit,
                on_finished=on_finished,
            )
        return session_id

    async def poll(
        self,
        *,
        session_id: str,
        yield_time_ms: int,
        max_output_chars: int,
        owner_session_key: str | None = None,
    ) -> _SessionPoll:
        """Wait up to ``yield_time_ms`` and return the latest output snapshot."""
        return await self.write(
            session_id=session_id,
            chars=None,
            close_stdin=False,
            terminate=False,
            yield_time_ms=yield_time_ms,
            max_output_chars=max_output_chars,
            owner_session_key=owner_session_key,
        )

    async def write(
        self,
        *,
        session_id: str,
        chars: str | None,
        close_stdin: bool,
        terminate: bool,
        yield_time_ms: int,
        max_output_chars: int,
        owner_session_key: str | None = None,
    ) -> _SessionPoll:
        async with self._lock:
            await self._cleanup_locked()
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        if (
            owner_session_key
            and session.owner_session_key
            and session.owner_session_key != owner_session_key
        ):
            raise KeyError(session_id)

        if chars:
            error = await session.write(chars)
            if error:
                raise RuntimeError(error)
        stdin_closed = False
        if close_stdin:
            error = await session.close_stdin()
            if error:
                raise RuntimeError(error)
            stdin_closed = True
        if terminate:
            await session.kill()
        poll = await session.poll(
            yield_time_ms,
            max_output_chars,
            terminated=terminate,
            stdin_closed=stdin_closed,
        )
        if poll.done:
            async with self._lock:
                self._sessions.pop(session_id, None)
        return poll

    async def list(self, *, owner_session_key: str | None = None) -> list[ExecSessionInfo]:
        async with self._lock:
            await self._cleanup_locked()
            now = time.monotonic()
            return [
                ExecSessionInfo(
                    session_id=session_id,
                    command=session.command,
                    cwd=session.cwd,
                    elapsed_s=max(0.0, now - session.started_at),
                    idle_s=max(0.0, now - session.last_access),
                    remaining_s=max(0.0, session.deadline - now),
                    returncode=session.process.returncode,
                    owner_session_key=session.owner_session_key,
                )
                for session_id, session in sorted(self._sessions.items())
                if not owner_session_key
                or not session.owner_session_key
                or session.owner_session_key == owner_session_key
            ]

    async def peek(self, session_id: str) -> str:
        """Return the rolling output tail without consuming the poll buffer.

        Unlike poll(), this does not touch last_access, so an observer UI
        refreshing every few seconds does not keep an abandoned session alive
        past its idle timeout.
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
        async with session._lock:
            return session._tail

    async def kill_session(self, session_id: str) -> None:
        """Kill a session and remove it from the registry."""
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            raise KeyError(session_id)
        # The operator or the UI stopped it on purpose: nothing to announce.
        session.exit_reported = True
        await session.kill()

    async def _cleanup_locked(self) -> None:
        now = time.monotonic()
        stale = [
            session_id
            for session_id, session in self._sessions.items()
            if now - session.last_access > self.idle_timeout
        ]
        for session_id in stale:
            session = self._sessions.pop(session_id)
            session.exit_reported = True
            await session.kill()

    async def shutdown(self) -> int:
        """Kill every live session. Returns how many were still running.

        Without this the interpreter tears down the event loop while a
        subprocess transport is still open, and the garbage collector prints an
        "Event loop is closed" traceback after the agent's last word.
        """
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.exit_reported = True
            with suppress(Exception):
                await session.kill()
        return len(sessions)

    async def _spawn(
        self,
        command: str,
        cwd: str,
        env: dict[str, str],
        shell_program: str | None,
        login: bool,
    ) -> asyncio.subprocess.Process:
        from navin.agent.tools.shell import ExecTool

        return await ExecTool._spawn(
            command, cwd, env, shell_program, login,
            stdin=asyncio.subprocess.PIPE,
        )


DEFAULT_EXEC_SESSION_MANAGER = ExecSessionManager()


def clamp_session_int(value: int | None, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    return min(max(value, minimum), maximum)


def _truncate_output(output: str, max_output_chars: int) -> tuple[str, int]:
    if len(output) <= max_output_chars:
        return output, 0
    half = max_output_chars // 2
    omitted = len(output) - max_output_chars
    return (
        output[:half]
        + f"\n\n... ({omitted:,} chars truncated) ...\n\n"
        + output[-half:],
        omitted,
    )


def format_session_poll(session_id: str, poll: _SessionPoll) -> str:
    parts = [poll.output] if poll.output else []
    if poll.truncated_chars:
        parts.append(f"(output truncated by {poll.truncated_chars:,} chars)")
    if poll.timed_out:
        parts.append("Error: Command timed out; session was terminated.")
    if poll.terminated and not poll.timed_out:
        parts.append("Session terminated.")
    if poll.stdin_closed:
        parts.append("Stdin closed.")
    if poll.done:
        parts.append(f"Exit code: {poll.exit_code}")
    else:
        parts.append(f"Process running. session_id: {session_id}")
        parts.append(
            "You will be told when it finishes; keep working meanwhile, or "
            "write_stdin with yield_time_ms to wait for it."
        )
    parts.append(f"Elapsed: {poll.elapsed_s:.1f}s")
    return "\n".join(parts) if parts else "(no output yet)"


@tool_parameters(
    tool_parameters_schema(
        session_id=StringSchema("Session id returned by exec when yield_time_ms is used."),
        chars=StringSchema(
            "Bytes/text to write to stdin. Omit or pass an empty string to only poll recent output.",
            nullable=True,
        ),
        close_stdin=BooleanSchema(
            description="Close stdin after writing chars. Useful for commands waiting for EOF.",
            default=False,
        ),
        terminate=BooleanSchema(
            description="Terminate the running exec session.",
            default=False,
        ),
        yield_time_ms=IntegerSchema(
            DEFAULT_YIELD_MS,
            description=(
                f"Milliseconds to wait before returning recent output "
                f"(default {DEFAULT_YIELD_MS}, max {MAX_YIELD_MS}). "
                "Oversized values are clamped."
            ),
            minimum=0,
            maximum=MAX_YIELD_MS,
        ),
        wait_for=StringSchema(
            "Optional text to wait for in output before returning. "
            "Useful for interactive commands and dev servers.",
            nullable=True,
        ),
        wait_timeout_ms=IntegerSchema(
            DEFAULT_WAIT_FOR_MS,
            description=(
                "Maximum milliseconds to wait for wait_for text "
                f"(default {DEFAULT_WAIT_FOR_MS}, max {MAX_WAIT_FOR_MS}). "
                "Oversized values are clamped (e.g. slow Android emulator boot)."
            ),
            minimum=0,
            maximum=MAX_WAIT_FOR_MS,
            nullable=True,
        ),
        max_output_chars=IntegerSchema(
            DEFAULT_MAX_OUTPUT_CHARS,
            description=(
                f"Maximum output characters to return from this poll "
                f"(default {DEFAULT_MAX_OUTPUT_CHARS}, max {MAX_OUTPUT_CHARS}). "
                "Oversized values are clamped."
            ),
            minimum=1000,
            maximum=MAX_OUTPUT_CHARS,
        ),
        max_output_tokens=IntegerSchema(
            DEFAULT_MAX_OUTPUT_CHARS,
            description="Compatibility alias for max_output_chars. The current runtime uses a character budget.",
            minimum=1000,
            maximum=MAX_OUTPUT_CHARS,
            nullable=True,
        ),
        required=["session_id"],
    )
)
class WriteStdinTool(Tool):
    """Write to or poll a running exec session."""

    _scopes = {"core", "subagent"}
    config_key = "exec"

    @classmethod
    def config_cls(cls):
        from navin.agent.tools.shell import ExecToolConfig

        return ExecToolConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.exec.enable

    def __init__(
        self,
        *,
        manager: ExecSessionManager | None = None,
        stdin_guard: Any | None = None,
    ) -> None:
        self._manager = manager or DEFAULT_EXEC_SESSION_MANAGER
        # Same policy as exec: without it, stdin into a live shell was a clean
        # bypass of the deny rules and of ask-every-command.
        self._stdin_guard = stdin_guard

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        # Deferred import: shell.py imports this module at load time.
        from navin.agent.tools.shell import StdinCommandGuard

        return cls(stdin_guard=StdinCommandGuard.from_config(
            ctx.config.exec, ctx.config.approvals,
        ))

    @property
    def exclusive(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "write_stdin"

    @property
    def description(self) -> str:
        return (
            "Interact with a running exec session created by exec with "
            "yield_time_ms. Use chars='' to poll without writing, chars to send "
            "stdin, close_stdin=true to send EOF, or terminate=true to stop the "
            "process. Use wait_for with wait_timeout_ms for dev servers, test "
            "watchers, and prompts where you need to wait for expected output. "
            "Do not use this to start new commands; start them with exec."
        )

    async def execute(
        self,
        session_id: str,
        chars: str | None = None,
        close_stdin: bool = False,
        terminate: bool = False,
        yield_time_ms: int | None = None,
        wait_for: str | None = None,
        wait_timeout_ms: int | None = None,
        max_output_chars: int | None = None,
        max_output_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            if max_output_chars is None:
                max_output_chars = max_output_tokens
            output_limit = clamp_session_int(
                max_output_chars,
                DEFAULT_MAX_OUTPUT_CHARS,
                1000,
                MAX_OUTPUT_CHARS,
            )
            command = await self._session_command(session_id)
            if self._stdin_guard is not None:
                refusal = await self._stdin_guard.check(
                    chars, session_command=command,
                )
                if refusal is not None:
                    return refusal
            if wait_for:
                return await self._wait_for_output(
                    session_id=session_id,
                    chars=chars,
                    close_stdin=close_stdin,
                    terminate=terminate,
                    wait_for=wait_for,
                    wait_timeout_ms=clamp_session_int(
                        wait_timeout_ms,
                        DEFAULT_WAIT_FOR_MS,
                        0,
                        MAX_WAIT_FOR_MS,
                    ),
                    max_output_chars=output_limit,
                    command=command,
                )
            poll = await self._manager.write(
                session_id=session_id,
                chars=chars,
                close_stdin=close_stdin,
                terminate=terminate,
                yield_time_ms=clamp_session_int(yield_time_ms, DEFAULT_YIELD_MS, 0, MAX_YIELD_MS),
                max_output_chars=output_limit,
                owner_session_key=current_request_session_key(),
            )
            if poll.done and poll.output and command:
                poll.output = compact_session_body(command, poll.output, poll.exit_code)
            result = format_session_poll(session_id, poll)
            return ToolResult.error(result) if poll.timed_out else result
        except KeyError:
            return ToolResult.error(await self._not_found_message(session_id))
        except Exception as exc:
            return ToolResult.error(f"Error writing to exec session: {exc}")

    async def _session_command(self, session_id: str) -> str | None:
        async with self._manager._lock:
            session = self._manager._sessions.get(session_id)
            return session.command if session is not None else None

    async def _not_found_message(self, session_id: str) -> str:
        """Explain a missing session with the ids that are actually reachable.

        The manager raises the same KeyError whether the id is unknown, has already
        exited, or belongs to another conversation, so name the reachable ids rather
        than leaving the agent to guess which of the three it hit.
        """
        reachable: list[str] = []
        with suppress(Exception):
            reachable = [
                info.session_id
                for info in await self._manager.list(
                    owner_session_key=current_request_session_key(),
                )
            ]
        if not reachable:
            return (
                f"Error: exec session not found: {session_id!r}. No exec session is "
                "active in this conversation; a session ends when its command exits "
                "or it times out. Start a new one with exec(background=true)."
            )
        return (
            f"Error: exec session not found: {session_id!r}. Active here: "
            f"{', '.join(reachable)}. Use list_exec_sessions for their state, or "
            "exec(background=true) to start a new one."
        )

    async def _wait_for_output(
        self,
        *,
        session_id: str,
        chars: str | None,
        close_stdin: bool,
        terminate: bool,
        wait_for: str,
        wait_timeout_ms: int,
        max_output_chars: int,
        command: str | None = None,
    ) -> str:
        deadline = time.monotonic() + (wait_timeout_ms / 1000)
        aggregate: list[str] = []
        first = True
        poll: _SessionPoll | None = None

        while True:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            step_ms = min(500, remaining_ms)
            poll = await self._manager.write(
                session_id=session_id,
                chars=chars if first else None,
                close_stdin=close_stdin if first else False,
                terminate=terminate if first else False,
                yield_time_ms=step_ms,
                max_output_chars=max_output_chars,
                owner_session_key=current_request_session_key(),
            )
            first = False
            if poll.output:
                aggregate.append(poll.output)
                joined = "".join(aggregate)
                if wait_for in joined:
                    poll.output = joined
                    # Still streaming / matched mid-run: keep raw for the match,
                    # compact only when the process has exited.
                    if poll.done and command:
                        poll.output = compact_session_body(
                            command, poll.output, poll.exit_code
                        )
                    result = format_session_poll(session_id, poll)
                    return ToolResult.error(result) if poll.timed_out else result
            if poll.done or remaining_ms <= 0:
                poll.output = "".join(aggregate)
                if poll.done and command:
                    poll.output = compact_session_body(
                        command, poll.output, poll.exit_code
                    )
                result = format_session_poll(session_id, poll)
                if wait_for not in ("".join(aggregate)):
                    result += f"\nWait target not observed: {wait_for!r}"
                return ToolResult.error(result) if poll.timed_out else result


@tool_parameters(tool_parameters_schema())
class ListExecSessionsTool(Tool):
    """List active exec sessions."""

    _scopes = {"core", "subagent"}
    config_key = "exec"

    @classmethod
    def config_cls(cls):
        from navin.agent.tools.shell import ExecToolConfig

        return ExecToolConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.exec.enable

    def __init__(
        self,
        *,
        manager: ExecSessionManager | None = None,
    ) -> None:
        self._manager = manager or DEFAULT_EXEC_SESSION_MANAGER

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls()

    @property
    def name(self) -> str:
        return "list_exec_sessions"

    @property
    def description(self) -> str:
        return (
            "List active long-running exec sessions, including session_id, cwd, "
            "elapsed time, idle time, remaining timeout, and command preview. "
            "Use this to recover a session_id after context shifts before "
            "polling, writing stdin, or terminating with write_stdin."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        try:
            sessions = await self._manager.list(
                owner_session_key=current_request_session_key(),
            )
            if not sessions:
                return "No active exec sessions."
            lines = []
            for info in sessions:
                command = " ".join(info.command.split())
                if len(command) > 120:
                    command = command[:119] + "..."
                status = "exited" if info.returncode is not None else "running"
                lines.append(
                    f"{info.session_id} | {status} | elapsed={info.elapsed_s:.1f}s "
                    f"| idle={info.idle_s:.1f}s | remaining={info.remaining_s:.1f}s "
                    f"| cwd={info.cwd} | {command}"
                )
            return "\n".join(lines)
        except Exception as exc:
            return ToolResult.error(f"Error listing exec sessions: {exc}")
