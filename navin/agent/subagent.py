# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Subagent manager for background task execution."""

import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from collections import deque
from collections.abc import Collection
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from loguru import logger

from navin.agent import worktree
from navin.agent.approval import (
    SubagentApprovalGate,
    bind_approval_gate,
    current_approval_gate,
    reset_approval_gate,
)
from navin.agent.checkpoints import (
    TurnRecorder,
    bind_checkpoint_recorder,
    reset_checkpoint_recorder,
)
from navin.agent.choice import (
    SubagentChoiceBroker,
    bind_choice_broker,
    current_choice_broker,
    reset_choice_broker,
)
from navin.agent.hook import AgentHook, AgentHookContext, CompositeHook
from navin.agent.run_policy import TurnPolicy, current_turn_policy
from navin.agent.runner import AgentLoopGuard, AgentRunner, AgentRunSpec
from navin.agent.subagent_journal import SubagentJournal, owner_alive
from navin.agent.tools.context import (
    RequestContext,
    ToolContext,
    bind_request_context,
    reset_request_context,
)
from navin.agent.tools.file_state import FileStates
from navin.agent.tools.loader import ToolLoader
from navin.agent.tools.registry import ToolRegistry
from navin.bus.events import InboundMessage
from navin.bus.outbound_events import (
    NotificationEvent,
    SubagentProgressEvent,
    TeamMessageEvent,
    outbound_message_for_event,
)
from navin.bus.queue import MessageBus
from navin.config.schema import AgentDefaults, ToolResultClearing, ToolsConfig
from navin.security.workspace_access import (
    WorkspaceScope,
    bind_live_restrict_to_workspace,
    bind_workspace_scope,
    reset_live_restrict_to_workspace,
    reset_workspace_scope,
    workspace_sandbox_status,
)
from navin.utils.llm_runtime import LLMRuntime
from navin.utils.prompt_templates import render_template

# Enough history to answer "what happened to the ones I started" across a few
# turns, small enough that a long session cannot grow it without bound.
#
# Has to cover a full wave with room to spare: this is the safety net for
# results the parent turn could not carry as injections, so a retention smaller
# than the concurrency width would quietly lose the verdict of a subagent that
# really ran. At 400 characters each, a saturated session holds well under a
# megabyte.
_MAX_OUTCOME_HISTORY = 1200
# Kept per finished run for spawn action=results and the next turn of the
# parent. A result that never reached the parent turn is only recoverable from
# here, so it keeps the file list and the merge note, not a 400-char teaser.
_MAX_OUTCOME_SUMMARY = 4000
# Files named in a result before the list is summarised as "and N more".
_MAX_CHANGED_FILES_LISTED = 25
_OUTCOME_FILE_STEM_CHARS = 48

# Waiting spawns one conversation may stack up before spawning is refused
# again. Sized well past any deliberate fan-out: a wave of a few hundred agents
# passes, a model looping on spawn hits the wall long before the process runs
# out of memory.
MAX_QUEUED_SUBAGENTS = 1000
# spawn() returns this prefix when the per-session wall is hit. The spawn
# tool shows that string to the model; Multitask must treat it as a refusal
# so the composer can put the prompt back instead of dropping it.
SPAWN_REFUSED_PREFIX = "Cannot spawn"


def spawn_was_accepted(detail: str) -> bool:
    """Whether ``SubagentManager.spawn`` accepted the task (started or queued)."""
    return isinstance(detail, str) and not detail.startswith(SPAWN_REFUSED_PREFIX)


_SPAWNED_ID = re.compile(r"\(id: ([^)\s]+)\)")


def spawned_task_id(detail: str) -> str:
    """The task id named in a spawn answer ("... started (id: ab12cd34)"), or ''."""
    found = _SPAWNED_ID.search(detail or "")
    return found.group(1) if found else ""


def _changed_files_note(
    validation: Any, *, partial: bool, kept_at: Path | str | None = None,
) -> str:
    """The files a run changed, appended to its result for the parent.

    A failed, cut or cancelled run still leaves its edits on disk. Without
    this list the parent only saw the last few tool calls, took the work for
    lost and redid it, or worse rewrote the files the run had just fixed.
    """
    # touched also holds docs, command-made files and the writes of a turn
    # that validates nothing; paths alone missed them.
    raw = sorted({
        *map(str, getattr(validation, "paths", None) or ()),
        *map(str, getattr(validation, "touched", None) or ()),
    })
    if not raw:
        return ""
    root = getattr(validation, "root", None)
    shown: list[str] = []
    for path in raw:
        text = Path(path).as_posix()
        if root is not None and Path(path).is_absolute():
            try:
                text = Path(path).relative_to(root).as_posix()
            except ValueError:
                pass
        if text not in shown:
            shown.append(text)
    if partial:
        where = f" in its checkout {kept_at}" if kept_at else ""
        head = (
            f"Files this subagent changed before it stopped ({len(shown)}), kept{where}. "
            "Check them and build on them; do not redo this work from scratch:"
        )
    else:
        head = f"Files this subagent changed ({len(shown)}):"
    lines = ["", "", head]
    lines.extend(f"- {path}" for path in shown[:_MAX_CHANGED_FILES_LISTED])
    if len(shown) > _MAX_CHANGED_FILES_LISTED:
        lines.append(f"- ... and {len(shown) - _MAX_CHANGED_FILES_LISTED} more")
    return "\n".join(lines)

# How often a still-alive card is republished so the IDE does not look frozen
# on a long tool or a long wait for a worktree. Does not cancel anything:
# killing a 20-minute compile because it was quiet is worse than a silent card.
# 8s is short enough that "Starting..." never looks dead; 60s felt stuck.
_HEARTBEAT_S = 8.0

# Prompt + tool registry stay off the event loop, on a dedicated pool so a
# wave of 50 cannot starve other to_thread work (git, worktrees, the parent
# turn). The work is Python holding the GIL (YAML, catalog, registry), so
# more threads only fight over it: a 60-teammate launch measured 8.2 s with
# eight workers and 3.8 s with two (tests/test_spawn_wave_latency.py), with
# the event loop smoother. Two also leaves the GIL to the chat the user types in.
_PREPARE_WORKERS = 2
_PREPARE_TIMEOUT_S = 20.0
# How long cancel_by_session waits for cancelled subagents to wind down.
_CANCEL_WAIT_S = 10.0
_PREPARE_EXECUTOR: ThreadPoolExecutor | None = None
_PERSIST_EXECUTOR: ThreadPoolExecutor | None = None
_JOURNAL_EXECUTOR: ThreadPoolExecutor | None = None
_OutcomeArgs = tuple[str, str, str, str, str, str | None]


def _prepare_executor() -> ThreadPoolExecutor:
    global _PREPARE_EXECUTOR
    if _PREPARE_EXECUTOR is None:
        workers = max(2, min(_PREPARE_WORKERS, (os.cpu_count() or 4) + 2))
        _PREPARE_EXECUTOR = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="navin-subagent-prep",
        )
    return _PREPARE_EXECUTOR


def _persist_executor() -> ThreadPoolExecutor:
    """Keep durable result writes off the UI and its general-purpose pool."""
    global _PERSIST_EXECUTOR
    if _PERSIST_EXECUTOR is None:
        _PERSIST_EXECUTOR = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="navin-subagent-save",
        )
    return _PERSIST_EXECUTOR


def _journal_executor() -> ThreadPoolExecutor:
    """One ordered durable writer keeps fsync and SQLite off the event loop."""
    global _JOURNAL_EXECUTOR
    if _JOURNAL_EXECUTOR is None:
        _JOURNAL_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="navin-agent-journal")
    return _JOURNAL_EXECUTOR


def _append_feed_line(path: Path, line: str, window: list[dict[str, Any]], session_key: str) -> None:
    """Append a team-journal line and keep the file bounded (journal thread)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        if path.stat().st_size > 2_000_000:
            # Keep the file bounded: rewrite it with the in-memory window.
            path.write_text(
                "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in window), encoding="utf-8",
            )
    except OSError as exc:
        logger.debug("Team journal for {} could not be saved: {}", session_key, exc)


def _outcomes_dir() -> Path:
    """Where finished-subagent history is persisted, one file per session.

    Under the runtime data dir rather than the workspace: the history belongs
    to a conversation, and a conversation can retarget its workspace scope
    mid-life without its subagent record following the folder around.

    The folder is created once per config location: ``get_runtime_subdir``
    does two ``mkdir`` syscalls, and this path is asked for on the event loop
    for every team-journal line and every status write of a wave. The cache
    follows the config path, so a relocated runtime dir (tests, a second
    profile) gets its own folder.
    """
    global _OUTCOMES_DIR
    from navin.config.paths import get_config_path, get_runtime_subdir

    data_dir = get_config_path().parent
    if _OUTCOMES_DIR is None or _OUTCOMES_DIR[0] != data_dir:
        _OUTCOMES_DIR = (data_dir, get_runtime_subdir("subagents"))
    return _OUTCOMES_DIR[1]


_OUTCOMES_DIR: tuple[Path, Path] | None = None
# Cap WS fan-out so a chatty subagent does not flood the UI: an unchanged
# line is re-sent at most this often, since it carries no news.
_PROGRESS_SAME_LINE_INTERVAL_S = 1.5
_LIVE_PROGRESS_CHANNELS = frozenset({"websocket", "cli"})
# Recent steps carried on each progress frame, so an expanded card can show
# what the subagent has been doing without a second request.
_MAX_PROGRESS_STEPS = 8
# Exchanges kept per conversation for the team view (replayed on reconnect).
_TEAM_FEED_MAX = 300
_COLLAB_ROUND_IDLE_S = 2 * 3600
# Anti-loop caps for team talk (see _check_debate_limits).
_TEAM_MAX_SENT_PER_RUN = 30
_TEAM_MAX_PAIR_EXCHANGES = 24
# Default of _announce_result(spawner=...): look the spawner up.
_AUTO_SPAWNER = object()
# Why a teammate writes: shown as a badge in the team journal.
TEAM_INTENTS = frozenset({"finding", "question", "answer", "challenge", "agree", "handoff", "update"})
# Argument keys worth keeping to describe an action; the rest (file bodies,
# patches) would only bloat the frame.
_DESCRIBING_ARG_KEYS = (
    "path", "file_path", "filename", "target", "directory", "dir",
    "command", "cmd", "query", "pattern", "url", "glob",
)

# tool name -> (kind, running verb, finished verb, base verb). ``kind`` is a
# stable key the WebUI translates; the verbs are the English status line.
_TOOL_VERBS: dict[str, tuple[str, str, str, str]] = {
    "read_file": ("read", "Reading", "Read", "read"),
    "write_file": ("write", "Writing", "Wrote", "write"),
    "edit_file": ("edit", "Editing", "Edited", "edit"),
    "apply_patch": ("edit", "Editing", "Edited", "edit"),
    "list_dir": ("list", "Listing", "Listed", "list"),
    "find_files": ("find", "Finding", "Found", "find"),
    "grep": ("search", "Searching", "Searched", "search"),
    "exec": ("run", "Running", "Ran", "run"),
    "web_search": ("web_search", "Searching the web for", "Searched the web for", "search the web for"),
    "web_fetch": ("fetch", "Fetching", "Fetched", "fetch"),
}
_PATH_TOOL_KINDS = frozenset({"read", "write", "edit", "list"})


def _short_path(raw: str) -> str:
    """Last segment of a POSIX, Windows or ``\\\\wsl.localhost`` path."""
    text = str(raw or "").strip().strip("'\"")
    if not text:
        return ""
    parts = [part for part in re.split(r"[\\/]+", text) if part]
    return parts[-1] if parts else text


def _compact_args(arguments: Any) -> dict[str, str]:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (TypeError, ValueError):
            return {}
    if not isinstance(arguments, dict):
        return {}
    out: dict[str, str] = {}
    for key in _DESCRIBING_ARG_KEYS:
        value = arguments.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            out[key] = str(value).strip()[:200]
    return out


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _describe_tool(name: str, args: dict[str, Any] | None) -> tuple[str, str, str, str, str]:
    """(kind, running verb, finished verb, base verb, target) for one call."""
    name = str(name or "tool").strip() or "tool"
    args = args or {}
    kind, running, finished, base = _TOOL_VERBS.get(
        name, ("tool", f"Using {name}", f"Used {name}", f"use {name}")
    )
    target = ""
    if kind in _PATH_TOOL_KINDS or kind == "tool":
        raw = next(
            (args[k] for k in ("path", "file_path", "filename", "target", "directory", "dir") if args.get(k)),
            "",
        )
        target = _short_path(raw) if raw else ""
    if not target:
        if kind == "run":
            target = _clip(args.get("command") or args.get("cmd") or "", 48)
        elif kind == "fetch":
            target = _clip(args.get("url") or "", 48)
        elif kind in {"search", "find", "web_search"}:
            raw = args.get("query") or args.get("pattern") or args.get("glob") or ""
            target = f'"{_clip(raw, 40)}"' if raw else ""
    if name == "apply_patch" and not target:
        # A patch names its files inside its body, which is not carried here;
        # a bare "Edited" reads as a sentence cut short.
        return "patch", "Editing files", "Edited files", "edit files", ""
    return kind, running, finished, base, target


# Exact openings of the errors the runner itself writes. Matched as prefixes,
# never as substrings: a model's prose or a file name that merely mentions a
# "timeout" or a "cancel" button must not be read as one.
_STALLED_PREFIXES = ("error calling llm: stream stalled",)
_TIMEOUT_PREFIXES = (
    "error calling llm: timed out",
    "error calling llm: request timed out",
)
_LIMIT_PREFIX = "subagent reached its iteration limit"
_ERROR_REASONS = {
    "cancelled": "Cancelled",
    "stalled": "Model stream stalled",
    "timeout": "Model timed out",
    "limit": "Reached its step limit",
    "other": "Stopped on an error",
}


def _humanize_error(err: str | None, kind: str | None = None) -> tuple[str, str]:
    """(error kind, short readable reason) for a subagent that stopped.

    ``kind`` is what the run already knows (the provider's ``error_kind``, or
    the stop reason); the text is only consulted for the runner's own exact
    prefixes. Anything else is "other" with a generic reason: the raw text can
    carry class names, paths or hosts, and is not shown on the card.
    """
    text = str(err or "").strip()
    if text == "cancelled" or kind == "cancelled":
        return "cancelled", _ERROR_REASONS["cancelled"]
    low = text.lower()
    if low.startswith(_STALLED_PREFIXES):
        return "stalled", _ERROR_REASONS["stalled"]
    if kind == "timeout" or low.startswith(_TIMEOUT_PREFIXES):
        return "timeout", _ERROR_REASONS["timeout"]
    if kind == "limit" or low.startswith(_LIMIT_PREFIX):
        return "limit", _ERROR_REASONS["limit"]
    return "other", _ERROR_REASONS["other"]


@dataclass(slots=True)
class SubagentStatus:
    """Real-time status of a running subagent."""

    task_id: str
    label: str
    task_description: str
    started_at: float          # time.monotonic()
    # queued | isolating | initializing | awaiting_model | thinking | responding | awaiting_tools
    # | awaiting_approval | tools_completed | final_response | done | error
    phase: str = "initializing"
    iteration: int = 0
    tool_events: list = field(default_factory=list)   # [{name, status, detail}, ...]
    usage: dict = field(default_factory=dict)          # token usage
    stop_reason: str | None = None
    error: str | None = None
    # Stable class of ``error`` when the run knows it (provider error_kind,
    # stop reason); see ``_humanize_error``.
    error_kind: str | None = None
    model: str | None = None
    origin_channel: str = "cli"
    origin_chat_id: str = "direct"
    last_progress_at: float = 0.0
    # Phase and line of the last frame sent, for the same-line throttle.
    last_status_line: str = ""
    # Rolling record of finished tool calls, newest last, for the card details.
    steps: list = field(default_factory=list)  # [{kind, verb, target, status}, ...]


@dataclass(slots=True)
class _QueuedSpawn:
    """A spawn accepted but not started, waiting for a slot to free up.

    Everything the parent turn contributed is captured here rather than read
    at start time: by then the turn is over and its contextvars (policy,
    workspace scope) are gone, so a deferred spawn would silently run with
    weaker rules than the one that asked for it.
    """

    task_id: str
    task: str
    label: str
    agent_name: str | None
    origin: dict[str, Any]
    runtime: Any
    origin_message_id: str | None
    workspace_scope: Any
    isolate: bool
    scope_paths: list[str] | None
    parent_metadata: dict[str, Any] | None
    policy: Any
    status: "SubagentStatus"
    session_key: str | None
    admission_generation: int = 0
    # Collaboration Mode teammate profile (navin.collab.team), or None.
    member: dict[str, Any] | None = None
    # The spawning turn's approval gate, so the subagent can ask the user.
    approval_gate: Any = None
    # The spawning turn's choice broker: ask_user cards reach the parent's chat.
    choice_broker: Any = None
    # Isolated checkout to continue in when a run is resumed ({path, origin, base}).
    resume_checkout: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SubagentOutcome:
    """What became of a finished subagent, kept after its live status is gone.

    A status is dropped the moment its task ends, so until now the fate of a
    subagent existed only in the announcement it published. If that
    announcement did not fit into the parent turn, the work had simply vanished:
    nothing could say whether it had succeeded, failed, or never run.
    """

    task_id: str
    label: str
    task_description: str
    status: str            # "ok" | "error"
    summary: str
    finished_at: float     # wall clock: an outcome outlives the turn that made it
    session_key: str | None = None


def _member_denied(base: Any, member: dict[str, Any] | None) -> Any:
    """Policy denylist plus what a teammate's sandbox forbids."""
    if not member:
        return base
    from navin.collab.team import denied_tools_for

    extra = {*denied_tools_for(member), *(member.get("_extra_denied") or ())}
    if not extra:
        return base
    return frozenset({*(base or ()), *extra})


def _agent_denied(base: Any, workspace: Path | None, agent_name: str | None) -> Any:
    """Add the ``disallowedTools`` of an agent definition to the denylist."""
    if not agent_name or workspace is None:
        return base
    from navin.agent.project_agents import find_project_agent

    try:
        definition = find_project_agent(Path(workspace), agent_name)
    except Exception:  # noqa: BLE001
        return base
    if definition is None or not definition.disallowed:
        return base
    return frozenset({*(base or ()), *definition.disallowed})


def _agent_permission_mode(workspace: Path | None, agent_name: str | None) -> str:
    """The ``permissionMode`` of an agent definition, "default" when unset."""
    if not agent_name or workspace is None:
        return "default"
    from navin.agent.project_agents import find_project_agent

    try:
        definition = find_project_agent(Path(workspace), agent_name)
    except Exception:  # noqa: BLE001
        return "default"
    return getattr(definition, "permission_mode", "default") if definition else "default"


def _saved_checkout(checkpoint: dict[str, Any] | None) -> dict[str, Any] | None:
    """The isolated checkout a journaled run worked in, if it still exists."""
    saved = (checkpoint or {}).get("checkout")
    if not isinstance(saved, dict):
        return None
    if not all(isinstance(saved.get(k), str) and saved.get(k) for k in ("path", "origin", "base")):
        return None
    return saved if Path(saved["path"]).is_dir() else None


_INTERRUPTED_CALL_NOTICE = (
    "Interrupted by a restart before this call finished: its effect is unknown. "
    "Check the current state (read the file, run a status command) before redoing it, "
    "then continue the task."
)


def _resume_after_interrupted_batch(
    messages: list[dict[str, Any]], checkpoint: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Conversation a run stopped in ``awaiting_tools`` can continue from.

    The assistant message carrying the pending tool calls becomes the last
    assistant message (taken from the checkpoint when the journal does not
    already end with it), then every pending call without a result gets a
    tool message saying the restart cut it short, so the model checks the
    state before redoing anything instead of hanging on an unanswered call.
    """
    checkpoint = checkpoint or {}
    result = [dict(m) for m in messages if isinstance(m, dict)]
    if checkpoint.get("phase") != "awaiting_tools":
        return result
    pending = [c for c in (checkpoint.get("pending_tool_calls") or []) if isinstance(c, dict)]
    if not pending:
        # Older checkpoints only carry the assistant message: its calls are the batch.
        assistant = checkpoint.get("assistant_message")
        if isinstance(assistant, dict):
            pending = [c for c in (assistant.get("tool_calls") or []) if isinstance(c, dict)]
    if not pending:
        return result
    pending_ids = {str(c.get("id")) for c in pending if c.get("id")}

    def _carries_batch(message: dict[str, Any]) -> bool:
        if message.get("role") != "assistant":
            return False
        ids = {str(tc.get("id")) for tc in (message.get("tool_calls") or []) if isinstance(tc, dict)}
        return bool(ids & pending_ids)

    batch_index = next((i for i in range(len(result) - 1, -1, -1) if _carries_batch(result[i])), None)
    if batch_index is None:
        assistant = checkpoint.get("assistant_message")
        if isinstance(assistant, dict):
            result.append(dict(assistant))
        else:
            result.append({"role": "assistant", "content": "", "tool_calls": [dict(c) for c in pending]})
        batch_index = len(result) - 1
    answered = {
        str(m.get("tool_call_id")) for m in result[batch_index + 1:]
        if m.get("role") == "tool" and m.get("tool_call_id")
    }
    for call in pending:
        call_id = call.get("id")
        if not call_id or str(call_id) in answered:
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        result.append({
            "role": "tool", "tool_call_id": call_id,
            "name": str(function.get("name") or "tool"),
            "content": _INTERRUPTED_CALL_NOTICE,
        })
        answered.add(str(call_id))
    return result


def _agent_allowed(base: Any, workspace: Path | None, agent_name: str | None) -> Any:
    """Narrow the allowlist to the ``tools`` an agent definition declares.

    ``skill`` and ``read_file`` stay available so the role can load its
    playbooks; an agent without a ``tools`` field inherits ``base``.
    """
    if not agent_name or workspace is None:
        return base
    from navin.agent.project_agents import find_project_agent

    try:
        definition = find_project_agent(Path(workspace), agent_name)
    except Exception:  # noqa: BLE001
        return base
    if definition is None or not definition.tools:
        return base
    own = frozenset(definition.tools) | {"skill", "read_file"}
    if base is None:
        return own
    return frozenset(set(base) & own)


def _member_allowed(base: Any, member: dict[str, Any] | None) -> Any:
    """Policy allowlist narrowed to a teammate's own tools."""
    if not member:
        return base
    from navin.collab.team import allowed_tools_for

    own = allowed_tools_for(member)
    if own is None:
        return base
    if base is None:
        return own
    return frozenset(set(base) & own)


def _running_events(calls: Any) -> list[dict[str, Any]]:
    """Tool events for a batch that has just started, from (name, args) pairs."""
    return [
        {"name": name, "status": "running", "detail": "", "args": _compact_args(args)}
        for name, args in calls
    ]


class _SubagentHook(AgentHook):
    """Hook for subagent execution - logs tool calls and updates status."""

    def __init__(
        self,
        task_id: str,
        status: SubagentStatus | None = None,
        on_progress: Callable[[SubagentStatus], None] | None = None,
        record_event: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__()
        self._reraise = True
        self._task_id = task_id
        self._status = status
        self._on_progress = on_progress
        self._completed_call_ids: set[str] = set()
        self._record_event = record_event

    def wants_streaming(self) -> bool:
        # Streaming is what keeps a slow model alive here. A non-streamed call
        # sends nothing until the whole answer is ready, so a long translation
        # tripped the provider's 120s read timeout on every retry and then the
        # 300s wall clock. A stream is watched for stalls between chunks
        # (NAVIN_STREAM_IDLE_TIMEOUT_S) and gets the longer streaming wall.
        return True

    async def before_iteration(self, context: AgentHookContext) -> None:
        # Providers may buffer reasoning and tool calls until the response is
        # complete. Show the request wait even when no delta has arrived yet.
        if self._status is not None:
            self._status.iteration = context.iteration
            self._enter_phase("awaiting_model")

    def _enter_phase(self, phase: str) -> None:
        """Move to a generation phase once per change, never per chunk."""
        assert self._status is not None
        self._status.error = None
        self._status.error_kind = None
        if self._status.phase == phase:
            return
        self._status.phase = phase
        if self._on_progress is not None:
            self._on_progress(self._status)

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        # The deltas themselves are not shown anywhere (nor forwarded): a
        # stream that produces text only means the subagent is alive, past any
        # earlier error, and writing. Without saying so the card sat on the
        # last tool ("Read en.json") for the minutes a long answer takes.
        if self._status is None or not delta:
            return
        self._enter_phase("responding")

    async def emit_reasoning(self, reasoning_content: str | None) -> None:
        # Same rule as the text deltas: the reasoning is never kept or passed
        # on, it only tells the card the model is thinking. Once text has
        # started, a late reasoning chunk must not flip the card back.
        if self._status is None or not reasoning_content:
            return
        if self._status.phase == "responding":
            return
        self._enter_phase("thinking")

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        self._completed_call_ids.clear()
        for tool_call in context.tool_calls:
            if self._record_event is not None:
                await self._record_event("tool_start", {
                    "id": tool_call.id, "name": tool_call.name,
                    "arguments": tool_call.arguments,
                })
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.debug(
                "Subagent [{}] executing: {} with arguments: {}",
                self._task_id, tool_call.name, args_str,
            )
        if self._status is not None and context.tool_calls:
            self._status.phase = "awaiting_tools"
            # A new batch of tools means the subagent got past whatever went
            # wrong before; a stale error would otherwise ride every frame.
            self._status.error = None
            self._status.error_kind = None
            self._status.tool_events = _running_events(
                (tc.name, tc.arguments) for tc in context.tool_calls
            )
            if self._on_progress is not None:
                self._on_progress(self._status)

    async def on_tool_done(
        self,
        context: AgentHookContext,
        tool_call: Any,
        result: Any,
        event: dict[str, Any],
    ) -> None:
        # A quick read must become visible while a slower sibling still runs.
        # Only compact action metadata is published, never the tool's output.
        if self._status is None or tool_call.id in self._completed_call_ids:
            return
        if self._record_event is not None:
            await self._record_event("tool_done", {
                "id": tool_call.id, "name": tool_call.name,
                "arguments": tool_call.arguments, "result": str(result),
                "status": event.get("status", "ok"),
            })
        completed = dict(event)
        completed["args"] = _compact_args(tool_call.arguments)
        for index, call in enumerate(context.tool_calls):
            if call.id == tool_call.id and index < len(self._status.tool_events):
                self._status.tool_events[index] = completed
                break
        self._completed_call_ids.add(tool_call.id)
        self._record_steps([completed])
        if self._on_progress is not None:
            self._on_progress(self._status)

    async def after_iteration(self, context: AgentHookContext) -> None:
        if self._status is None:
            return
        self._status.iteration = context.iteration
        calls = list(context.tool_calls or [])
        events: list[dict[str, Any]] = []
        for index, raw in enumerate(context.tool_events or []):
            event = dict(raw) if isinstance(raw, dict) else {}
            if "args" not in event and index < len(calls):
                event["args"] = _compact_args(calls[index].arguments)
            events.append(event)
        self._status.tool_events = events
        self._record_steps([
            event for index, event in enumerate(events)
            if index >= len(calls) or calls[index].id not in self._completed_call_ids
        ])
        self._status.usage = dict(context.usage)
        if context.error:
            self._status.error = str(context.error)
            response = context.response
            self._status.error_kind = (
                getattr(response, "error_kind", None) if response is not None else None
            )
            self._status.phase = "error"
        elif context.tool_events:
            self._status.error = None
            self._status.error_kind = None
            self._status.phase = "tools_completed"
        else:
            self._status.error = None
            self._status.error_kind = None
            self._status.phase = "final_response"
        if self._on_progress is not None:
            self._on_progress(self._status)

    def _record_steps(self, events: list[dict[str, Any]]) -> None:
        assert self._status is not None
        for event in events:
            state = str(event.get("status") or "ok")
            if state == "running":
                continue
            kind, _running, finished, _base, target = _describe_tool(
                str(event.get("name") or ""), event.get("args"),
            )
            self._status.steps.append({
                "kind": kind,
                "name": str(event.get("name") or ""),
                "verb": finished,
                "target": target,
                "status": "error" if state == "error" else "ok",
            })
        if len(self._status.steps) > _MAX_PROGRESS_STEPS:
            del self._status.steps[: len(self._status.steps) - _MAX_PROGRESS_STEPS]


class SubagentManager:
    """Manages background subagent execution."""

    def __init__(
        self,
        *,
        workspace: Path,
        bus: MessageBus,
        max_tool_result_chars: int,
        tool_result_clearing: ToolResultClearing | None = None,
        tools_config: ToolsConfig | None = None,
        restrict_to_workspace: bool = False,
        image_generation_provider_configs: dict[str, Any] | None = None,
        disabled_skills: list[str] | None = None,
        trust_workspace_harness_skills: bool = True,
        max_iterations: int | None = None,
        max_concurrent_subagents: int | None = None,
        max_total_concurrent_subagents: int | None = None,
        fail_on_tool_error: bool | None = None,
        llm_wall_timeout_for_session: Callable[[str | None], float | None] | None = None,
        record_edits: Callable[[str, dict[str, bytes | None]], None] | None = None,
        usage_hooks: list[AgentHook] | None = None,
        approval_gate: Any = None,
        choice_broker: Any = None,
    ):
        defaults = AgentDefaults()
        self.workspace = workspace
        self.bus = bus
        # Where a subagent's questions go when it is spawned outside a turn (a
        # Multitask prompt, a resume after restart): the turn's own gate and
        # broker are preferred when a turn is running, these are the fallback.
        # Without them such a subagent could never ask, and its question ended
        # up buried in its result text, so the run read as "did nothing".
        self._approval_gate = approval_gate
        self._choice_broker = choice_broker
        self.tools_config = tools_config or ToolsConfig()
        self.max_tool_result_chars = max_tool_result_chars
        self.tool_result_clearing = tool_result_clearing or ToolResultClearing()
        self.restrict_to_workspace = restrict_to_workspace
        self.image_generation_provider_configs = dict(image_generation_provider_configs or {})
        self.disabled_skills = set(disabled_skills or [])
        self.trust_workspace_harness_skills = trust_workspace_harness_skills
        self.max_iterations = (
            max_iterations
            if max_iterations is not None
            else defaults.max_tool_iterations
        )
        self.max_concurrent_subagents = (
            max_concurrent_subagents
            if max_concurrent_subagents is not None
            else defaults.max_concurrent_subagents
        )
        self.fail_on_tool_error = (
            fail_on_tool_error
            if fail_on_tool_error is not None
            else defaults.fail_on_tool_error
        )
        self.max_total_concurrent_subagents = max_total_concurrent_subagents
        self._drain_cursor = 0
        self.runner = AgentRunner()
        self._usage_hooks = list(usage_hooks or [])
        self._llm_wall_timeout_for_session = llm_wall_timeout_for_session
        self._record_edits = record_edits
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._task_statuses: dict[str, SubagentStatus] = {}
        self._agent_sessions: dict[str, str] = {}
        self._agent_mailboxes: dict[str, deque[dict[str, Any]]] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}
        # Delegation tree: level of each agent and who spawned it (nested spawn).
        self._spawn_depths: dict[str, int] = {}
        self._spawn_parents: dict[str, str] = {}
        # Mid-run notes from subagents to the parent turn, per conversation.
        self._parent_mailboxes: dict[str, deque[dict[str, Any]]] = {}
        # Spawns accepted past the limit. Kept in memory only: a running
        # subagent does not survive a gateway restart either, and reviving work
        # that never started while losing work half-done would be the wrong way
        # round.
        self._queued: dict[str, deque[_QueuedSpawn]] = {}
        # Multitask client keys: a timeout retry must not start a second
        # subagent for the same queued prompt.
        self._client_keys: dict[str, str] = {}
        self._task_client_slots: dict[str, str] = {}
        self._client_key_details: dict[str, str] = {}
        # How deep one conversation's queue may get before spawning is refused
        # again. Waiting work is cheap but not free, and without a wall a model
        # looping on spawn would enqueue until the process ran out of memory.
        self.max_queued_subagents = MAX_QUEUED_SUBAGENTS
        # Captured when the first subagent starts: request_drain() is called
        # from the license-sync thread and needs a way back onto this loop.
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        # Outcomes are kept per session: one busy conversation fanning out
        # dozens of subagents must not evict another conversation's history,
        # and a wide fan-out (25+) must stay fully retrievable via
        # spawn(action="results") even when the mid-turn injection cycles
        # could not carry every announcement. This dict is a cache over the
        # files in _outcomes_dir(), hydrated per session on first touch, so the
        # same guarantee holds across a gateway restart.
        self._outcomes: dict[str, deque[SubagentOutcome]] = {}
        self._outcome_lock = RLock()
        self._pending_outcomes: list[tuple[_OutcomeArgs, asyncio.Future[None]]] = []
        self._outcome_flush_scheduled = False
        self._journal_instance: SubagentJournal | None = None
        self._recovered_messages: dict[str, list[dict[str, Any]]] = {}
        self._recovered_final_results: dict[str, str] = {}
        self._admission_lock = asyncio.Lock()
        self._reserved_slots: dict[str, int] = {}
        self._session_generations: dict[str, int] = {}
        self._pending_journal_writes: list[Any] = []
        self._recovered_validation: dict[str, dict] = {}
        # Collaboration Mode: teammate profile per running task, the team's
        # live conversation per session (replayed on reconnect), and the
        # assignment count per (session, mission, teammate) for round limits.
        self._task_members: dict[str, dict[str, Any]] = {}
        self._team_feed: dict[str, deque[dict[str, Any]]] = {}
        self._collab_rounds: dict[tuple[str, str, str], tuple[int, float]] = {}
        self._collab_budget: dict[str, int] = {}
        # Anti-loop counters: messages sent per teammate run, and per pair of
        # teammates per conversation (a debate has an end).
        self._sent_counts: dict[str, int] = {}
        self._pair_counts: dict[tuple[str, str, str], int] = {}
        # Fire-and-forget tasks (terminal progress puts, re-announcements):
        # the loop only keeps weak references, so they are held here.
        self._background_tasks: set[asyncio.Future[Any]] = set()

    @property
    def journal(self) -> SubagentJournal:
        if self._journal_instance is None:
            self._journal_instance = SubagentJournal(_outcomes_dir() / "execution.sqlite3")
        return self._journal_instance

    def spawn_depth(self, task_id: str | None) -> int:
        """Delegation level of an agent (1 = spawned by the user's turn)."""
        if not task_id:
            return 0
        return self._spawn_depths.get(task_id, 1)

    def _forget(self, task_id: str) -> deque[dict[str, Any]]:
        """Drop every per-task record; return the unread mailbox.

        One place for every ending (finished, refused, cancelled while queued,
        failed to start), so no table keeps a ghost entry for a dead task.
        A teammate's debate counters with others end with its run.
        """
        member = getattr(self, "_task_members", {}).get(task_id)
        session = getattr(self, "_agent_sessions", {}).get(task_id)
        pairs = getattr(self, "_pair_counts", None)
        if member is not None and session is not None and isinstance(pairs, dict):
            key = self._member_key(task_id)
            for pair in [p for p in pairs if p[0] == session and key in p[1:]]:
                del pairs[pair]
        for name in ("_running_tasks", "_task_statuses", "_agent_sessions", "_task_members",
                     "_spawn_depths", "_spawn_parents", "_collab_budget", "_sent_counts"):
            table = getattr(self, name, None)
            if isinstance(table, dict):
                table.pop(task_id, None)
        self.forget_client_spawn(task_id)
        mailboxes = getattr(self, "_agent_mailboxes", None)
        unread = mailboxes.pop(task_id, None) if isinstance(mailboxes, dict) else None
        return unread or deque()

    def _keep_task(self, task: asyncio.Future[Any]) -> None:
        tasks = getattr(self, "_background_tasks", None)
        if tasks is None:
            tasks = self._background_tasks = set()
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    def _journal_io(self, method: str, *args: Any) -> Any:
        return getattr(self.journal, method)(*args)

    async def _journal_event(self, task_id: str, kind: str, payload: dict) -> None:
        session = self._agent_sessions.get(task_id)
        if session is not None:
            await self.flush_journal()
            await asyncio.get_running_loop().run_in_executor(
                _journal_executor(), self._journal_io, "append", session, task_id, kind, payload,
            )

    async def flush_journal(self) -> None:
        """Wait for earlier ordered writes before acknowledging a user action."""
        pending, self._pending_journal_writes = self._pending_journal_writes, []
        if pending:
            # return_exceptions: an earlier fire-and-forget write that failed
            # (e.g. "database is locked") must not resurface here and abort
            # the caller, which may be about to deliver a result to the parent.
            outcomes = await asyncio.shield(asyncio.gather(
                *(asyncio.wrap_future(future) for future in pending), return_exceptions=True,
            ))
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    logger.warning("Subagent journal write failed: {}", outcome)
        await asyncio.get_running_loop().run_in_executor(_journal_executor(), lambda: None)

    def _submit_journal(self, method: str, *args: Any) -> None:
        self._pending_journal_writes.append(_journal_executor().submit(self._journal_io, method, *args))

    def _build_hook(
        self,
        task_id: str,
        status: SubagentStatus | None,
        on_progress: Callable[[SubagentStatus], None] | None,
    ) -> AgentHook:
        """The subagent's own hook, plus whatever accounts for what it spends.

        A subagent drives its own ``AgentRunner``, so the loop's hook chain
        never reaches it. Its tokens are billed like any other turn's and used
        to be recorded nowhere at all, which is what let the usage table sit
        still through an afternoon of subagent work.
        """
        hook: AgentHook = _SubagentHook(
            task_id, status, on_progress=on_progress,
            record_event=lambda kind, payload: self._journal_event(task_id, kind, payload),
        )
        if not self._usage_hooks:
            return hook
        return CompositeHook([hook, *self._usage_hooks])

    def _subagent_tools_config(self) -> ToolsConfig:
        """Build a ToolsConfig scoped for subagent use.

        The tool set is narrowed by ``Tool._scopes``, not by dropping config
        fields: an omitted field falls back to its schema default, which
        silently re-enables a capability the operator turned off.
        """
        return self.tools_config.model_copy(
            update={"restrict_to_workspace": self.restrict_to_workspace},
        )

    def _build_tools(
        self,
        workspace: Path | None = None,
        tools_config: ToolsConfig | None = None,
        member_tools: frozenset[str] | None = None,
    ) -> ToolRegistry:
        """Build an isolated subagent tool registry via ToolLoader."""
        root = self.workspace if workspace is None else workspace
        registry = ToolRegistry()
        cfg = tools_config if tools_config is not None else self._subagent_tools_config()
        ctx = ToolContext(
            config=cfg,
            subagent_manager=self,
            workspace=str(root.resolve()),
            file_state_store=FileStates(),
            image_generation_provider_configs=self.image_generation_provider_configs,
            workspace_sandbox=workspace_sandbox_status(
                restrict_to_workspace=cfg.restrict_to_workspace,
                workspace=root,
            ),
        )
        ToolLoader().load(ctx, registry, scope="subagent")
        if member_tools:
            # Desk tools (seo, scrape) stay off plain subagents; a teammate
            # whose job lists them gets them.
            extra = ToolRegistry()
            ToolLoader().load(ctx, extra, scope="teammate")
            for name in extra.tool_names:
                if name in member_tools and not registry.has(name):
                    registry.register(extra.get(name))
        return registry

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        agent: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        origin_message_id: str | None = None,
        temperature: float | None = None,
        workspace_scope: WorkspaceScope | None = None,
        isolate: bool = False,
        scope_paths: list[str] | None = None,
        parent_metadata: dict[str, Any] | None = None,
        client_key: str | None = None,
        *,
        runtime: LLMRuntime,
        parent_task_id: str | None = None,
        depth: int = 1,
        member: dict[str, Any] | None = None,
        budget_tokens: int = 0,
        assigned_by: str = "parent",
        workspace_policy_spec: dict[str, Any] | None = None,
    ) -> str:
        """Spawn a subagent, or queue it when the machine is already full."""
        self._event_loop = asyncio.get_running_loop()
        self._ensure_watchdog()
        replayed = self.replay_client_spawn(session_key, client_key)
        if replayed:
            return replayed
        if temperature is not None:
            runtime = runtime.with_generation_overrides(temperature=temperature)
        task_id = str(uuid.uuid4())[:8]
        if workspace_policy_spec:
            if not isolate:
                return (
                    f"{SPAWN_REFUSED_PREFIX} subagent: a workspace policy needs isolate=true."
                )
            from navin.agent import workspace_policy

            try:
                workspace_policy.set_policy(
                    task_id, workspace_policy.policy_from_dict(workspace_policy_spec),
                )
            except (TypeError, ValueError) as exc:
                logger.warning("Invalid workspace policy for [{}]: {}", task_id, exc)
                return f"{SPAWN_REFUSED_PREFIX} subagent: invalid workspace policy ({exc})."
        agent_name = (agent or "").strip() or None
        display_label = label or agent_name or task[:30] + ("..." if len(task) > 30 else "")
        origin = {"channel": origin_channel, "chat_id": origin_chat_id, "session_key": session_key}
        # Captured at spawn time, on purpose: this is the policy of the turn
        # that delegated the work. Delegation must not be an escape hatch from
        # the parent's verify gate or the module denylist.
        inherited_policy = current_turn_policy() or TurnPolicy()

        status = SubagentStatus(
            task_id=task_id,
            label=display_label,
            task_description=task,
            started_at=time.monotonic(),
            model=getattr(runtime, "model", None),
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
        )
        self._task_statuses[task_id] = status
        self._spawn_depths[task_id] = max(1, int(depth))
        if parent_task_id:
            self._spawn_parents[task_id] = parent_task_id
        self._agent_sessions[task_id] = session_key or f"{origin_channel}:{origin_chat_id}"
        self._agent_mailboxes[task_id] = deque()
        if member:
            self._task_members[task_id] = dict(member)
            if budget_tokens > 0:
                self._collab_budget[task_id] = int(budget_tokens)

        pending = _QueuedSpawn(
            task_id=task_id,
            task=task,
            label=display_label,
            agent_name=agent_name,
            origin=origin,
            runtime=runtime,
            origin_message_id=origin_message_id,
            workspace_scope=workspace_scope,
            isolate=isolate,
            scope_paths=scope_paths,
            parent_metadata=parent_metadata,
            policy=inherited_policy,
            status=status,
            session_key=session_key,
            admission_generation=self._session_generations.get(session_key or "", 0),
            member=dict(member) if member else None,
            approval_gate=current_approval_gate() or self._approval_gate,
            choice_broker=current_choice_broker() or self._choice_broker,
        )

        # Count this session's subagents, not every session's: the limit exists
        # to keep one turn's fan-in readable, and a global count let a subagent
        # in one conversation block spawning in every other one.
        try:
            accepted, started = await self._admit_spawn(pending)
        except BaseException as error:
            status.phase = "error"
            status.error = "cancelled" if isinstance(error, asyncio.CancelledError) else str(error)
            status.error_kind = "cancelled" if isinstance(error, asyncio.CancelledError) else "other"
            self._publish_progress(status, force=True, done=True)
            self._forget(task_id)
            raise
        if member and accepted:
            self.post_team_feed(session_key or f"{origin_channel}:{origin_chat_id}", "assign",
                                assigned_by, task, target=task_id, task_id=task_id,
                                status="started" if started else "queued")
        elif accepted and parent_task_id and parent_task_id in self._task_members:
            # A teammate delegates a slice of its work to a helper subagent.
            self.post_team_feed(session_key or f"{origin_channel}:{origin_chat_id}", "assign",
                                parent_task_id, task, target=task_id, task_id=task_id,
                                status="started" if started else "queued")
        if started:
            self._publish_progress(status, force=True)
            logger.info("Spawned subagent [{}]: {}", task_id, display_label)
            detail = (
                f"Subagent [{display_label}] started (id: {task_id}). "
                "I'll notify you when it completes."
            )
            self.bind_client_spawn(session_key, client_key, task_id, detail)
            return detail

        queue = self._queued.get(session_key or "", ())
        if not accepted:
            # The refusal used to be the backpressure. Queuing removed it, so a
            # model looping on spawn could enqueue without end, each entry
            # holding a runtime, a scope and a status. This is the new wall, set
            # far past any real fan-out so only a runaway ever meets it.
            self._forget(task_id)
            if status.error_kind == "cancelled":
                return f"{SPAWN_REFUSED_PREFIX} subagent: this conversation was stopped during admission."
            logger.warning(
                "Refused subagent [{}]: queue for {} is full ({})",
                task_id, session_key or "-", self.max_queued_subagents,
            )
            return (
                f"{SPAWN_REFUSED_PREFIX} subagent: {self.max_concurrent_subagents} are "
                f"running for this conversation and {len(queue)} more are already "
                "queued. Their results arrive on their own, so wait for them "
                "instead of spawning more."
            )

        self._publish_progress(status, force=True)
        ahead = len(self._queued.get(session_key or "", ()))
        logger.info("Queued subagent [{}]: {} (position {})", task_id, display_label, ahead)
        detail = (
            f"Subagent [{display_label}] queued (id: {task_id}), position {ahead}: "
            "the current conversation or runtime execution capacity is occupied. "
            "This task has not started; do not report it as running in parallel. "
            "It starts on its own as soon as a slot frees up, and its result "
            "arrives like any other. Do not retry this call."
        )
        self.bind_client_spawn(session_key, client_key, task_id, detail)
        return detail

    async def _admit_spawn(self, pending: _QueuedSpawn) -> tuple[bool, bool]:
        """Reserve admission across the durable write's await boundary."""
        async with self._admission_lock:
            key = pending.session_key or ""
            if pending.admission_generation != self._session_generations.get(key, 0):
                pending.status.error_kind = "cancelled"
                return False, False
            started = self._has_free_slot(pending.session_key)
            if not started:
                queue = self._queued.setdefault(pending.session_key or "", deque())
                if len(queue) >= self.max_queued_subagents:
                    return False, False
                pending.status.phase = "queued"
            if started:
                self._reserved_slots[key] = self._reserved_slots.get(key, 0) + 1
            try:
                write = asyncio.create_task(self._persist_spawn(pending))
                try:
                    await asyncio.shield(write)
                except asyncio.CancelledError:
                    # The thread may already have committed acceptance. Finish
                    # that write before publishing its terminal cancellation.
                    await asyncio.shield(write)
                    raise
            finally:
                if started:
                    self._reserved_slots[key] -= 1
            if pending.admission_generation != self._session_generations.get(key, 0):
                pending.status.phase = "error"
                pending.status.error = "cancelled"
                pending.status.error_kind = "cancelled"
                self._publish_progress(pending.status, force=True, done=True)
                await self._journal_event(pending.task_id, "result", {
                    "role": "assistant", "content": "Cancelled before execution.", "status": "error",
                })
                return False, False
            if started:
                self._start(pending)
            else:
                # A completion callback can drain and remove the old deque
                # while the durable acceptance write is awaiting its thread.
                self._queued.setdefault(key, deque()).append(pending)
                self._drain_queue(pending.session_key)
                started = pending.task_id in self._running_tasks
            return True, started

    async def _persist_spawn(self, pending: _QueuedSpawn) -> None:
        descriptor = {
            "task": pending.task, "label": pending.label, "agent": pending.agent_name,
            "origin": pending.origin, "origin_message_id": pending.origin_message_id,
            "isolate": pending.isolate, "scope_paths": pending.scope_paths,
            "workspace": str(pending.workspace_scope.project_path
                             if pending.workspace_scope else self.workspace),
            "restrict_to_workspace": bool(
                pending.workspace_scope.restrict_to_workspace
                if pending.workspace_scope else self.restrict_to_workspace
            ),
            "generation": (asdict(pending.runtime.generation)
                           if isinstance(pending.runtime, LLMRuntime) else None),
            "policy": {
                "requires_verify_before_done": pending.policy.requires_verify_before_done,
                "validate_code_changes": pending.policy.validate_code_changes,
                "locked_denied_tools": sorted(pending.policy.locked_denied_tools),
                "allowed_tools": sorted(pending.policy.allowed_tools)
                if pending.policy.allowed_tools is not None else None,
            },
            # A Collaboration teammate resumes as itself after a restart.
            "member": pending.member,
        }
        await asyncio.get_running_loop().run_in_executor(
            _journal_executor(), self._journal_io, "create",
            self._agent_sessions[pending.task_id], pending.task_id,
            self._journal_metadata(pending.status), descriptor,
        )

    def _journal_metadata(self, status: SubagentStatus, *, done: bool = False) -> dict:
        return {"task_id": status.task_id, "label": status.label,
                "task_description": status.task_description,
                "model": status.model if isinstance(status.model, str) else None,
                "phase": status.phase, "iteration": status.iteration,
                "done": done or status.phase in {"done", "error"},
                "error": status.error, "status_line": self._status_line(status),
                "steps": list(status.steps), "tokens": self._total_tokens(status)}

    def _stored_metadata(self, record: dict, *, alive: bool | None = None) -> dict:
        metadata = dict(record["metadata"])
        if metadata.get("task_id") in (getattr(self, "_task_statuses", None) or {}):
            # Running in this very process: no OS probe can know better, and a
            # wrong "owner gone" turned a working agent into "interrupted",
            # which closed its card in the journal while it still worked.
            alive = True
        if alive is None:
            alive = owner_alive(record["owner_pid"], record.get("owner_identity", ""))
        abandoned = not metadata.get("done") and not alive
        cancelled = metadata.get("done") and str(metadata.get("error") or "").lower() in {"cancelled", "canceled"}
        interrupted = abandoned or cancelled
        if abandoned:
            metadata.update(phase="interrupted", status_line="Interrupted by restart", done=False)
        isolated = record["descriptor"].get("isolate", False)
        # An isolated run can continue where it stopped while its checkout is
        # still on disk; without it the work has to be reviewed and relaunched.
        if isolated and _saved_checkout(record.get("checkpoint")) is not None:
            isolated = False
        # A run cut during a tool batch resumes too: the pending calls get an
        # "effect unknown" result (see _resume_after_interrupted_batch) and
        # the model checks the state itself. Left aside, such an agent was
        # re-evaluated at every restart and never came back.
        metadata["recoverable"] = bool(interrupted and not isolated)
        failed = metadata.get("done") and metadata.get("phase") == "error"
        metadata["requires_relaunch"] = bool(
            (interrupted and isolated) or (failed and not metadata["recoverable"])
        )
        return metadata

    def list_agents(
        self, session_key: str, *, offset: int = 0, limit: int = 200,
        workspaces: Collection[str] | None = None, since: float | None = None,
    ) -> list[dict]:
        owners: dict[tuple[int, str], bool] = {}
        result = []
        rows = self.journal.list(session_key, offset, limit, workspaces=workspaces, since=since)
        for row in rows:
            identity = row["owner_pid"], row.get("owner_identity", "")
            if identity not in owners:
                owners[identity] = owner_alive(*identity)
            result.append(self._stored_metadata(row, alive=owners[identity]))
        return result

    def agent_history(self, session_key: str, task_id: str, *, after: int = 0, limit: int = 200) -> dict:
        record = self.journal.get(session_key, task_id)
        if record is None:
            return {"agent": None, "events": [], "next_cursor": after, "has_more": False}
        return {"agent": self._stored_metadata(record),
                **self.journal.history(session_key, task_id, after, limit)}

    async def recover_agent(
        self, session_key: str, task_id: str, *, runtime: LLMRuntime,
        workspace_scope: WorkspaceScope | None = None,
    ) -> str:
        """Resume abandoned execution only at a completed, durable boundary."""
        admission_generation = self._session_generations.get(session_key, 0)
        record = await asyncio.get_running_loop().run_in_executor(
            _journal_executor(), self._journal_io, "get", session_key, task_id,
        )
        if record is None:
            return "Cannot resume: agent not found in this conversation."
        if task_id in self._task_statuses:
            return "Cannot resume: agent is still active or finishing."
        metadata = self._stored_metadata(record)
        descriptor = record["descriptor"]
        resume_checkout = None
        if descriptor["isolate"]:
            resume_checkout = _saved_checkout(record.get("checkpoint"))
            if resume_checkout is None:
                return "Cannot resume: isolated workspace needs explicit review and relaunch."
        if metadata["requires_relaunch"]:
            return "Cannot resume: this run needs review; inspect its effects and explicitly relaunch."
        if not metadata["recoverable"]:
            return "Cannot resume: agent is active or already finished."
        saved_model = metadata.get("model")
        if saved_model and runtime.model != saved_model:
            return "Cannot resume: select the original model before continuing this agent."
        if descriptor.get("generation"):
            from navin.providers.base import GenerationSettings
            runtime = replace(runtime, generation=GenerationSettings(**descriptor["generation"]))
        root = workspace_scope.project_path if workspace_scope else self.workspace
        if Path(descriptor["workspace"]).resolve() != root.resolve():
            return "Cannot resume: the original workspace must be selected."
        if descriptor.get("restrict_to_workspace") and not (
            workspace_scope.restrict_to_workspace if workspace_scope else self.restrict_to_workspace
        ):
            return "Cannot resume: the original workspace restriction must remain enabled."
        saved_policy = descriptor["policy"]
        policy = TurnPolicy(
            requires_verify_before_done=saved_policy["requires_verify_before_done"],
            validate_code_changes=saved_policy["validate_code_changes"],
            locked_denied_tools=frozenset(saved_policy["locked_denied_tools"]),
            allowed_tools=(frozenset(saved_policy["allowed_tools"])
                           if saved_policy["allowed_tools"] is not None else None),
        )
        current = current_turn_policy() or TurnPolicy()
        policy = replace(
            policy,
            locked_denied_tools=policy.locked_denied_tools | current.locked_denied_tools,
            requires_verify_before_done=policy.requires_verify_before_done or current.requires_verify_before_done,
            validate_code_changes=policy.validate_code_changes or current.validate_code_changes,
            allowed_tools=(current.allowed_tools if policy.allowed_tools is None else policy.allowed_tools
                           if current.allowed_tools is None else policy.allowed_tools & current.allowed_tools),
        )
        status = SubagentStatus(task_id=task_id, label=descriptor["label"],
                                task_description=descriptor["task"], started_at=time.monotonic(),
                                model=runtime.model, origin_channel=descriptor["origin"]["channel"],
                                origin_chat_id=descriptor["origin"]["chat_id"])
        inbox = await asyncio.get_running_loop().run_in_executor(
            _journal_executor(), self._journal_io, "pending_messages", session_key, task_id,
        )
        messages = await asyncio.get_running_loop().run_in_executor(
            _journal_executor(), self._journal_io, "messages", session_key, task_id,
        )
        checkpoint = record.get("checkpoint") or {}
        resumed_after_batch = False
        if checkpoint.get("phase") == "awaiting_tools":
            continued = _resume_after_interrupted_batch(messages, checkpoint)
            resumed_after_batch = continued != messages
            messages = continued
        pending = _QueuedSpawn(
            task_id=task_id, task=descriptor["task"], label=descriptor["label"],
            agent_name=descriptor["agent"], origin=descriptor["origin"], runtime=runtime,
            origin_message_id=descriptor["origin_message_id"], workspace_scope=workspace_scope,
            isolate=resume_checkout is not None, scope_paths=descriptor["scope_paths"],
            parent_metadata=None,
            policy=policy, status=status, session_key=session_key,
            member=descriptor.get("member"),
            approval_gate=current_approval_gate() or self._approval_gate,
            choice_broker=current_choice_broker() or self._choice_broker,
            resume_checkout=resume_checkout,
        )
        if descriptor.get("member"):
            self._task_members[task_id] = dict(descriptor["member"])
        async with self._admission_lock:
            if admission_generation != self._session_generations.get(session_key, 0):
                return "Cannot resume: this conversation was stopped during recovery."
            started = self._has_free_slot(session_key)
            if not started and self.queued_count(session_key) >= self.max_queued_subagents:
                return "Cannot resume: the waiting queue is full."
            if started:
                self._reserved_slots[session_key] = self._reserved_slots.get(session_key, 0) + 1
            try:
                claim = asyncio.get_running_loop().run_in_executor(
                    _journal_executor(), self._journal_io, "claim", session_key, task_id,
                )
                try:
                    claimed = await asyncio.shield(claim)
                except asyncio.CancelledError:
                    claimed = await asyncio.shield(claim)
                    if claimed:
                        await asyncio.shield(asyncio.get_running_loop().run_in_executor(
                            _journal_executor(), self._journal_io, "abandon", session_key, task_id,
                        ))
                    raise
            finally:
                if started:
                    self._reserved_slots[session_key] -= 1
            if not claimed:
                return "Cannot resume: another runtime owns this agent."
            if admission_generation != self._session_generations.get(session_key, 0):
                await asyncio.get_running_loop().run_in_executor(
                    _journal_executor(), self._journal_io, "abandon", session_key, task_id,
                )
                return "Cannot resume: this conversation was stopped during recovery."
            self._agent_sessions[task_id] = session_key
            self._agent_mailboxes[task_id] = deque(inbox)
            self._task_statuses[task_id] = status
            self._recovered_messages[task_id] = messages
            self._recovered_validation[task_id] = checkpoint.get("validation_state") or {}
            if checkpoint.get("phase") == "final_response":
                self._recovered_final_results[task_id] = str(
                    (checkpoint.get("assistant_message") or {}).get("content") or "Task completed.",
                )
            self._event_loop = asyncio.get_running_loop()
            self._ensure_watchdog()
            if started:
                self._start(pending)
            else:
                status.phase = "queued"
                self._queued.setdefault(session_key, deque()).append(pending)
        self._publish_progress(status, force=True)
        if resumed_after_batch:
            return f"Subagent [{status.label}] resumed after restart (id: {task_id})."
        return f"Subagent [{status.label}] resumed (id: {task_id})."

    async def auto_recover(self, *, runtime: LLMRuntime, scope_for: Callable[[str], WorkspaceScope | None]) -> dict[str, int]:
        """After a restart, resume every agent left at a durable boundary.

        A run cut during a tool batch resumes with its pending calls marked
        "effect unknown". Only agents that truly need human review (isolated
        checkout gone, failed run) are left for an explicit relaunch; any
        other refusal (model, workspace, queue) is counted as skipped and
        logged with its reason. Auto-recovery uses ``recover_agent`` and its
        checks verbatim, exactly like the manual path.
        """
        candidates = await asyncio.get_running_loop().run_in_executor(
            _journal_executor(), self._journal_io, "unfinished",
        )
        counts = {"resumed": 0, "review": 0, "skipped": 0}
        for session_key, task_id in candidates:
            try:
                detail = await self.recover_agent(
                    session_key, task_id, runtime=runtime, workspace_scope=scope_for(session_key),
                )
            except Exception:  # one broken record must not stop the others from resuming
                logger.exception("Subagent auto-recovery failed for {}", task_id)
                counts["skipped"] += 1
                continue
            if not detail.startswith("Cannot"):
                counts["resumed"] += 1
            elif "relaunch" in detail:
                counts["review"] += 1
                logger.info("Subagent auto-recovery: {} left for review: {}", task_id, detail)
            else:
                counts["skipped"] += 1
                logger.info("Subagent auto-recovery: {} skipped: {}", task_id, detail)
        if candidates:
            logger.info(
                "Subagent auto-recovery: {resumed} resumed, {review} left for review, {skipped} skipped",
                **counts,
            )
        return counts

    async def relaunch_agent(
        self, session_key: str, task_id: str, *, runtime: LLMRuntime,
        workspace_scope: WorkspaceScope | None = None,
    ) -> str:
        """Explicitly start a new execution; prior effects are not rolled back."""
        admission_generation = self._session_generations.get(session_key, 0)
        record = await asyncio.get_running_loop().run_in_executor(
            _journal_executor(), self._journal_io, "get", session_key, task_id,
        )
        if record is None:
            return "Cannot relaunch: agent not found in this conversation."
        if task_id in self._task_statuses or (
            not record["metadata"].get("done") and owner_alive(
                record["owner_pid"], record.get("owner_identity", ""),
            )
        ):
            return "Cannot relaunch: agent is still active."
        descriptor = record["descriptor"]
        if descriptor.get("generation"):
            from navin.providers.base import GenerationSettings
            runtime = replace(runtime, generation=GenerationSettings(**descriptor["generation"]))
        root = workspace_scope.project_path if workspace_scope else self.workspace
        if Path(descriptor["workspace"]).resolve() != root.resolve():
            return "Cannot relaunch: the original workspace must be selected."
        if descriptor.get("restrict_to_workspace") and not (
            workspace_scope.restrict_to_workspace if workspace_scope else self.restrict_to_workspace
        ):
            return "Cannot relaunch: the original workspace restriction must remain enabled."
        from navin.agent.run_policy import bind_turn_policy, reset_turn_policy
        saved = descriptor["policy"]
        current = current_turn_policy() or TurnPolicy()
        saved_allowed = saved["allowed_tools"]
        allowed = current.allowed_tools
        if saved_allowed is not None:
            allowed = frozenset(saved_allowed) if allowed is None else allowed & frozenset(saved_allowed)
        token = bind_turn_policy(TurnPolicy(
            requires_verify_before_done=current.requires_verify_before_done or saved["requires_verify_before_done"],
            validate_code_changes=current.validate_code_changes or saved["validate_code_changes"],
            locked_denied_tools=current.locked_denied_tools | frozenset(saved["locked_denied_tools"]),
            allowed_tools=allowed,
        ))
        try:
            if admission_generation != self._session_generations.get(session_key, 0):
                return "Cannot relaunch: this conversation was stopped during recovery."
            return await self.spawn(
                descriptor["task"], label=descriptor["label"], agent=descriptor["agent"],
                origin_channel=descriptor["origin"]["channel"],
                origin_chat_id=descriptor["origin"]["chat_id"], session_key=session_key,
                workspace_scope=workspace_scope, isolate=descriptor["isolate"],
                scope_paths=descriptor["scope_paths"], runtime=runtime,
                member=descriptor.get("member"),
            )
        finally:
            reset_turn_policy(token)

    async def retry_agent(
        self, session_key: str, task_id: str, *, runtime: LLMRuntime,
        workspace_scope: WorkspaceScope | None = None,
    ) -> dict[str, Any]:
        """The one "Relaunch" of the journal: resume where it stopped when that is
        safe, otherwise start the same task again.

        Returns ``mode`` (recover or relaunch) and the ``task_id`` that now runs:
        the same one for a resume, a new one for a relaunch.
        """
        record = await asyncio.get_running_loop().run_in_executor(
            _journal_executor(), self._journal_io, "get", session_key, task_id,
        )
        if record is None:
            return {"ok": False, "error": "Cannot relaunch: agent not found in this conversation."}
        if task_id in self._task_statuses:
            return {"ok": False, "error": "Cannot relaunch: agent is still running. Cancel it first."}
        mode = "recover" if self._stored_metadata(record).get("recoverable") else "relaunch"
        method = self.recover_agent if mode == "recover" else self.relaunch_agent
        detail = await method(session_key, task_id, runtime=runtime, workspace_scope=workspace_scope)
        if not spawn_was_accepted(detail) or detail.startswith("Cannot"):
            return {"ok": False, "error": detail, "mode": mode}
        now_running = task_id if mode == "recover" else spawned_task_id(detail)
        return {"ok": True, "detail": detail, "mode": mode, "task_id": now_running}

    async def cancel_agent(self, session_key: str, task_id: str) -> dict[str, Any]:
        """Stop one agent of this conversation, queued or running; the others go on.

        The card gets its final "cancelled" frame from the same paths as /stop:
        here for a queued agent, from the run's own cancellation for a running one.
        """
        queue = self._queued.get(session_key)
        pending = next((item for item in queue or () if item.task_id == task_id), None)
        if queue is not None and pending is not None:
            queue.remove(pending)
            if not queue:
                self._queued.pop(session_key, None)
            journaled = task_id in self._agent_sessions
            pending.status.phase = "error"
            pending.status.error = "Cancelled"
            pending.status.error_kind = "cancelled"
            self._publish_progress(pending.status, force=True, done=True)
            self._forget(task_id)
            if journaled:
                await self.flush_journal()
                await asyncio.get_running_loop().run_in_executor(
                    _journal_executor(), self._journal_io, "append_many", [(session_key, task_id)], "result",
                    {"role": "assistant", "content": "Cancelled before execution.", "status": "error"},
                )
            return {"ok": True, "detail": f"Cancelled {pending.label} before it started."}
        task = self._running_tasks.get(task_id)
        if task is None or task_id not in self._session_tasks.get(session_key, ()):
            return {"ok": False, "error": "Cannot cancel: this agent is not running in this conversation."}
        if task.done():
            return {"ok": False, "error": "Cannot cancel: this agent already finished."}
        status = self._task_statuses.get(task_id)
        label = status.label if status is not None else task_id
        task.cancel()
        # Bounded like /stop: an agent that will not finish must not hold the loop.
        timeout_s = getattr(self, "_cancel_wait_s", _CANCEL_WAIT_S)
        _, still = await asyncio.wait({task}, timeout=timeout_s)
        if still:
            logger.warning("Subagent [{}] still stopping after {}s", task_id, timeout_s)
            return {"ok": True, "detail": f"Stopping {label}..."}
        return {"ok": True, "detail": f"Stopped {label}."}

    def _has_free_slot(self, session_key: str | None) -> bool:
        """Whether one more subagent may run right now for this conversation."""
        total = len(self._running_tasks) + sum(self._reserved_slots.values())
        if self.max_total_concurrent_subagents is not None and total >= self.max_total_concurrent_subagents:
            return False
        if not session_key:
            return len(self._running_tasks) + sum(self._reserved_slots.values()) < self.max_concurrent_subagents
        return self._started_count(session_key) + self._reserved_slots.get(session_key, 0) < self.max_concurrent_subagents

    def _start(self, pending: _QueuedSpawn) -> None:
        """Turn an accepted spawn into a running task."""
        task_id = pending.task_id
        session_key = pending.session_key
        if pending.status.phase == "queued":
            pending.status.phase = "initializing"
            # The clock starts when the work does, not when it was asked for:
            # a queued wait would otherwise be reported as time spent working.
            pending.status.started_at = time.monotonic()

        bg_task = asyncio.create_task(
            self._run_subagent(
                task_id,
                pending.task,
                pending.label,
                pending.origin,
                pending.status,
                pending.runtime,
                pending.origin_message_id,
                pending.workspace_scope,
                pending.isolate,
                pending.agent_name,
                pending.scope_paths,
                pending.parent_metadata,
                pending.policy,
                # Keyword and only when set: a plain subagent's call is unchanged.
                **({"member": pending.member} if pending.member else {}),
                **({"approval_gate": pending.approval_gate} if pending.approval_gate else {}),
                **({"choice_broker": pending.choice_broker} if pending.choice_broker else {}),
                **({"resume_checkout": pending.resume_checkout}
                   if pending.resume_checkout else {}),
            )
        )
        self._task_statuses[task_id] = pending.status
        self._running_tasks[task_id] = bg_task
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:
            grandparent = self._spawn_parents.get(task_id)
            unread = self._forget(task_id)
            # A nested child's report that this agent never read is not
            # dropped: it goes one level up, like an unrouted result would.
            reports = [m for m in unread if m.get("announce")]
            for message in reports:
                self._keep_task(asyncio.ensure_future(self._announce_result(
                    message["sender"], **message["announce"], spawner=grandparent, record=False,
                )))
            if len(unread) > len(reports):
                logger.warning("Subagent [{}] ended with {} unread team messages",
                               task_id, len(unread) - len(reports))
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]
            # The only place a slot is ever given back, for all three endings
            # (finished, failed, cancelled). Anything else would leave a queue
            # waiting on a subagent that is already gone.
            self._drain_all()

        bg_task.add_done_callback(_cleanup)

    def team_members(self, session_key: str) -> list[dict[str, Any]]:
        rows = []
        for task_id, status in self._task_statuses.items():
            if self._agent_sessions.get(task_id) != session_key:
                continue
            row = {"task_id": task_id, "label": status.label, "phase": status.phase,
                   "task": status.task_description}
            member = self._task_members.get(task_id)
            if member:
                row.update(name=member.get("name"), title=member.get("title"),
                           role=member.get("role"))
            rows.append(row)
        return rows

    def member_identity(self, task_id: str) -> dict[str, str] | None:
        """Teammate identity of a running task, None for a plain subagent."""
        from navin.collab.team import member_identity

        # getattr: progress is also published by managers built without __init__.
        member = getattr(self, "_task_members", {}).get(task_id)
        return member_identity(member) if member else None

    def resolve_team_target(self, session_key: str, target: str) -> str:
        """Task id for a recipient given as a task id, a teammate name or "lead".

        A name matches the teammate's live task in this conversation, the most
        recently started one when the same teammate has two.
        """
        key = (target or "").strip()
        lowered = key.lower().lstrip("@")
        if lowered in {"parent", "lead", "navin", "orchestrator"}:
            return "parent"
        if key in self._task_statuses:
            return key
        from navin.collab.team import find_member

        match = None
        for task_id, member in self._task_members.items():
            if self._agent_sessions.get(task_id) != session_key:
                continue
            status = self._task_statuses.get(task_id)
            if status is None or status.phase in {"done", "error"}:
                continue
            if find_member([member], lowered) is not None:
                if match is None or status.started_at >= self._task_statuses[match].started_at:
                    match = task_id
        return match or key

    def _feed_identity(self, task_id: str) -> dict[str, str]:
        if task_id == "parent":
            return {"id": "lead", "name": "Navin", "title": "Orchestrator", "avatar": "navin",
                    "color": "#64748b"}
        if task_id == "user":
            return {"id": "user", "name": "You", "title": "", "avatar": "user", "color": "#64748b"}
        identity = self.member_identity(task_id)
        if identity:
            return identity
        status = self._task_statuses.get(task_id)
        return {"id": task_id, "name": status.label if status else task_id, "title": "Subagent",
                "avatar": "round", "color": "#6b7280"}

    def post_team_feed(
        self,
        session_key: str,
        kind: str,
        sender: str,
        content: str,
        *,
        target: str | None = None,
        task_id: str | None = None,
        status: str | None = None,
        target_identity: dict[str, str] | None = None,
        force: bool = False,
    ) -> dict[str, Any] | None:
        """Add one exchange to the team conversation shown in the chat.

        Only exchanges that involve a Collaboration Mode teammate are posted
        (or ``force``: the lead's own team actions): plain subagents keep their
        quiet cards.
        """
        involved = [t for t in (sender, target, task_id) if t and t not in {"parent", "user"}]
        if not force and not any(t in self._task_members for t in involved):
            return None
        entry = {
            "entry_id": uuid.uuid4().hex[:12],
            "kind": kind,
            "sender": self._feed_identity(sender),
            "target": target_identity or (self._feed_identity(target) if target else None),
            "content": _clip(content.strip(), 4000),
            "task_id": task_id,
            "status": status,
            "ts": time.time(),
        }
        feed = self._feed_for(session_key)
        feed.append(entry)
        self._persist_feed_entry(session_key, entry)
        self._publish_team_entry(session_key, entry)
        self._notify_team_entry(session_key, entry)
        return entry

    def _notify_team_entry(self, session_key: str, entry: dict[str, Any]) -> None:
        """The moments worth a notification: a teammate finished, stopped, or asks the user."""
        name = entry["sender"].get("name") or "A teammate"
        target_id = (entry.get("target") or {}).get("id")
        if entry["kind"] == "result":
            if entry.get("task_id") not in self._task_members:
                return  # a helper's report reaches its teammate, not the user
            ok = entry.get("status") == "ok"
            title = f"{name} finished" if ok else f"{name} stopped"
            level = "success" if ok else "warning"
        elif entry["kind"] == "message" and target_id == "user":
            title, level = f"{name} has a question for you", "info"
        else:
            return
        channel, _, chat_id = session_key.partition(":")
        outbound = getattr(self.bus, "outbound", None)
        if channel not in _LIVE_PROGRESS_CHANNELS or not chat_id or outbound is None:
            return
        try:
            outbound.put_nowait(outbound_message_for_event(
                channel=channel, chat_id=chat_id,
                event=NotificationEvent(
                    title=title, level=level, detail=_clip(entry["content"], 240),
                    key=f"team:{entry['entry_id']}", source="session",
                ),
            ))
        except Exception:
            logger.debug("Team notification for {} dropped (outbound bus full)", session_key)

    def team_feed(self, session_key: str) -> list[dict[str, Any]]:
        return list(self._feed_for(session_key))

    @staticmethod
    def _feed_path(session_key: str) -> Path:
        digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:24]
        return _outcomes_dir() / "team-feed" / f"{digest}.jsonl"

    def _feed_for(self, session_key: str) -> deque[dict[str, Any]]:
        """The session's team journal, reloaded from disk after a restart."""
        feed = self._team_feed.get(session_key)
        if feed is not None:
            return feed
        feed = deque(maxlen=_TEAM_FEED_MAX)
        try:
            path = self._feed_path(session_key)
            if path.is_file():
                for line in path.read_text(encoding="utf-8").splitlines()[-_TEAM_FEED_MAX:]:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(item, dict) and item.get("entry_id"):
                        feed.append(item)
        except OSError:
            logger.debug("Team journal for {} could not be read", session_key)
        self._team_feed[session_key] = feed
        return feed

    def _persist_feed_entry(self, session_key: str, entry: dict[str, Any]) -> None:
        """Append one team-journal line, off the event loop.

        Sixty teammates posting their assignment and their result wrote 120
        lines synchronously on the loop (open, write, stat, sometimes a 2 MB
        rewrite): the chat froze for most of a second during a team launch.
        The journal writer is a single ordered thread, so lines land in the
        order they were posted; the compaction uses the in-memory window taken
        at post time, which is what the file is meant to mirror.
        """
        path = self._feed_path(session_key)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        window = list(self._team_feed.get(session_key, ()))
        self._submit_journal_fn(_append_feed_line, path, line, window, session_key)

    def _submit_journal_fn(self, fn: Callable[..., Any], *args: Any) -> None:
        """Queue ordered disk work on the journal thread; a failure is logged."""
        future = _journal_executor().submit(fn, *args)
        self._pending_journal_writes.append(future)

    def _publish_team_entry(self, session_key: str, entry: dict[str, Any]) -> None:
        channel, _, chat_id = session_key.partition(":")
        if channel not in _LIVE_PROGRESS_CHANNELS or not chat_id:
            return
        outbound = getattr(self.bus, "outbound", None)
        if outbound is None:
            return
        try:
            outbound.put_nowait(outbound_message_for_event(
                channel=channel, chat_id=chat_id, event=TeamMessageEvent(**entry),
            ))
        except Exception:
            logger.debug("Team message for {} dropped (outbound bus full)", session_key)

    def collab_round(self, session_key: str, mission: str, member_id: str) -> int:
        """Count one more assignment of *member_id* in *mission*; return it.

        A mission left idle for two hours starts over: the limit stops a loop,
        not a teammate the user calls again later in the same conversation.
        """
        key = (session_key, mission or "default", member_id)
        count, last = self._collab_rounds.get(key, (0, 0.0))
        now = time.monotonic()
        if now - last > _COLLAB_ROUND_IDLE_S:
            count = 0
        self._collab_rounds[key] = (count + 1, now)
        return count + 1

    def member_history(self, session_key: str, member_id: str, *, limit: int = 6) -> str:
        """What a teammate did earlier in this conversation, for continuity.

        Its last report and the latest exchanges it took part in, so a teammate
        assigned again picks up where it stopped instead of starting cold.
        """
        feed = list(self._feed_for(session_key))
        mine = [e for e in feed if member_id in {e["sender"].get("id"), (e.get("target") or {}).get("id")}]
        if not mine:
            return ""
        lines = ["# Continuity: your earlier work in this conversation"]
        last = next((e for e in reversed(mine) if e["kind"] == "result" and e["sender"].get("id") == member_id), None)
        if last:
            lines += ["", "Your last report:", _clip(last["content"], 1800)]
        exchanges = [e for e in mine if e["kind"] in {"message", "user"}][-limit:]
        if exchanges:
            lines += ["", "Recent exchanges:"]
            for e in exchanges:
                target = (e.get("target") or {}).get("name") or "?"
                intent = f" [{e['status']}]" if e.get("status") else ""
                lines.append(f"- {e['sender'].get('name')} -> {target}{intent}: {_clip(e['content'], 300)}")
        return "\n".join(lines)

    def _check_debate_limits(self, session_key: str, sender: str, target: str) -> None:
        """No endless ping-pong: a run and a pair of teammates have a cap.

        Collaboration teammates only: plain subagents keep the inbox limits.
        """
        if sender in {"parent", "user"} or sender not in self._task_members:
            return
        sent = self._sent_counts.get(sender, 0)
        if sent >= _TEAM_MAX_SENT_PER_RUN:
            raise ValueError(
                f"You already sent {sent} team messages in this run. Stop the exchange, "
                "decide on the evidence you have, and put open points in your final report."
            )
        a, b = self._member_key(sender), self._member_key(target)
        pair = (session_key, *sorted((a, b)))
        if self._pair_counts.get(pair, 0) >= _TEAM_MAX_PAIR_EXCHANGES:
            raise ValueError(
                f"This debate already took {_TEAM_MAX_PAIR_EXCHANGES} messages. Settle it on "
                "evidence now, or send the disagreement to the lead (target 'lead') to arbitrate."
            )

    def _member_key(self, task_id: str) -> str:
        if task_id in {"parent", "user"}:
            return task_id
        member = self._task_members.get(task_id)
        return str(member.get("id")) if member else task_id

    def _count_message(self, session_key: str, sender: str, target: str) -> None:
        if sender in {"parent", "user"} or sender not in self._task_members:
            return
        self._sent_counts[sender] = self._sent_counts.get(sender, 0) + 1
        pair = (session_key, *sorted((self._member_key(sender), self._member_key(target))))
        self._pair_counts[pair] = self._pair_counts.get(pair, 0) + 1

    def send_team_message(
        self, session_key: str, sender: str, target: str, content: str, intent: str | None = None,
    ) -> str:
        """Queue one peer message without crossing a conversation boundary."""
        intent = intent if intent in TEAM_INTENTS else None
        if sender not in {"parent", "user"} and self._agent_sessions.get(sender) != session_key:
            raise ValueError("Sender is not an active agent in this conversation")
        if (target or "").strip().lower() in {"user", "human", "you"} and sender in self._task_members:
            # A teammate asks or tells the user something: shown in the team
            # journal, answered (if at all) through the Team view.
            if not content.strip() or len(content) > 8000:
                raise ValueError("A team message must contain 1 to 8000 characters")
            self._check_debate_limits(session_key, sender, "user")
            self._count_message(session_key, sender, "user")
            self.post_team_feed(session_key, "message", sender, content, target="user", status=intent)
            return ("Shown to the user in the team journal. If they answer, it reaches you as a "
                    "message from the user. Do not wait idle: carry on with what is not blocked.")
        if (target or "").strip().lower() in {"user", "human", "you"} and sender not in {"parent", "user"}:
            # A plain subagent has no team journal: its note for the user goes
            # to the lead, who relays it, instead of failing as an unknown agent.
            return self._send_to_parent(session_key, sender, ("[for the user] " + content) if content.strip() and len(content) <= 7985 else content, intent=intent)
        target = self.resolve_team_target(session_key, target)
        if target == sender:
            raise ValueError("You cannot send a team message to yourself")
        if target == "parent" and sender == "user":
            raise ValueError("Write to Navin in the chat composer")
        if target == "parent" and sender != "parent":
            self._check_debate_limits(session_key, sender, "parent")
            result = self._send_to_parent(session_key, sender, content, intent=intent)
            self._count_message(session_key, sender, "parent")
            return result
        status = self._task_statuses.get(target)
        if self._agent_sessions.get(target) != session_key or status is None:
            raise ValueError("Recipient is not an active agent in this conversation")
        if status.phase in {"done", "error"}:
            raise ValueError("Recipient has stopped; ask the parent to assign a new task")
        if not content.strip() or len(content) > 8000:
            raise ValueError("A team message must contain 1 to 8000 characters")
        mailbox = self._agent_mailboxes[target]
        if len(mailbox) >= 64:
            raise ValueError("Recipient inbox is full (64 messages); wait for it to read them")
        self._check_debate_limits(session_key, sender, target)
        message_id = uuid.uuid4().hex
        message = {"message_id": message_id, "sender": sender, "content": content}
        sender_member = self._task_members.get(sender)
        if sender_member:
            message["sender_name"] = sender_member.get("name") or sender
        elif sender == "user":
            message["sender_name"] = "the user"
        if intent:
            message["intent"] = intent
        mailbox.append(message)
        self._count_message(session_key, sender, target)
        self.post_team_feed(session_key, "user" if sender == "user" else "message", sender,
                            content, target=target, status=intent)
        self._submit_journal("append", session_key, target, "team_received", message)
        if sender not in {"parent", "user"}:
            self._submit_journal("append", session_key, sender, "team_sent", {**message, "target": target})
        for task_id, verb, other in ((sender, "Message sent to", target), (target, "Message queued from", sender)):
            current = self._task_statuses.get(task_id)
            if current is not None:
                current.steps.append({"kind": "message", "name": "team", "verb": verb,
                                      "target": f"{other}: {_clip(content, 140)}", "status": "ok"})
                del current.steps[:-_MAX_PROGRESS_STEPS]
                self._publish_progress(current, force=True)
        return f"Message {message_id} queued for {target}. Accepted, not yet read; delivered at the next agent boundary."

    def _send_to_parent(self, session_key: str, sender: str, content: str, intent: str | None = None) -> str:
        """Mid-run note from a subagent to the parent turn (read with team inbox)."""
        if not content.strip() or len(content) > 8000:
            raise ValueError("A team message must contain 1 to 8000 characters")
        mailbox = self._parent_mailboxes.setdefault(session_key, deque())
        if len(mailbox) >= 64:
            # Oldest note goes, not every future one: a long run must not lock
            # the parent inbox for the rest of the conversation.
            dropped = mailbox.popleft()
            logger.warning("Parent inbox for {} full; dropped oldest note {}",
                           session_key, dropped.get("message_id"))
        message_id = uuid.uuid4().hex
        message = {"message_id": message_id, "sender": sender, "content": content}
        sender_member = self._task_members.get(sender)
        if sender_member:
            message["sender_name"] = sender_member.get("name") or sender
        if intent:
            message["intent"] = intent
        mailbox.append(message)
        self.post_team_feed(session_key, "message", sender, content, target="parent", status=intent)
        self._submit_journal("append", session_key, sender, "team_sent", {**message, "target": "parent"})
        return f"Message {message_id} queued for the parent. It reads it with team inbox."

    def drain_parent_messages(self, session_key: str, limit: int = 64) -> list[dict[str, Any]]:
        mailbox = self._parent_mailboxes.get(session_key)
        return [mailbox.popleft() for _ in range(min(len(mailbox or ()), max(0, limit)))]

    def drain_team_messages(
        self, session_key: str, task_id: str, limit: int = 64, *, record_delivery: bool = True,
    ) -> list[dict[str, Any]]:
        if self._agent_sessions.get(task_id) != session_key:
            raise ValueError("Inbox is not in this conversation")
        mailbox = self._agent_mailboxes.get(task_id)
        messages = [mailbox.popleft() for _ in range(min(len(mailbox or ()), max(0, limit)))]
        if record_delivery:
            for message in messages:
                self._submit_journal("append", session_key, task_id, "team_delivered",
                                     {"message_id": message["message_id"]})
        return messages

    def _client_slot(self, session_key: str, client_key: str) -> str:
        return f"{session_key}\0{client_key}"

    def replay_client_spawn(
        self, session_key: str | None, client_key: str | None
    ) -> str | None:
        """Return the previous accept message when this client key is live."""
        keys = getattr(self, "_client_keys", None)
        statuses = getattr(self, "_task_statuses", None)
        if not session_key or not client_key or not isinstance(keys, dict):
            return None
        slot = self._client_slot(session_key, client_key.strip())
        task_id = keys.get(slot)
        if not task_id:
            return None
        if isinstance(statuses, dict) and task_id not in statuses:
            keys.pop(slot, None)
            slots = getattr(self, "_task_client_slots", None)
            if isinstance(slots, dict):
                slots.pop(task_id, None)
            return None
        stored = getattr(self, "_client_key_details", None)
        if isinstance(stored, dict) and stored.get(slot):
            return str(stored[slot])
        return f"Subagent started (id: {task_id})."

    def bind_client_spawn(
        self,
        session_key: str | None,
        client_key: str | None,
        task_id: str,
        detail: str,
    ) -> None:
        if not session_key or not client_key or not task_id:
            return
        keys = getattr(self, "_client_keys", None)
        if not isinstance(keys, dict):
            return
        slot = self._client_slot(session_key, client_key.strip())
        keys[slot] = task_id
        slots = getattr(self, "_task_client_slots", None)
        if isinstance(slots, dict):
            slots[task_id] = slot
        details = getattr(self, "_client_key_details", None)
        if details is None:
            self._client_key_details = {slot: detail}
        elif isinstance(details, dict):
            details[slot] = detail

    def forget_client_spawn(self, task_id: str) -> None:
        slots = getattr(self, "_task_client_slots", None)
        keys = getattr(self, "_client_keys", None)
        if not isinstance(slots, dict) or not isinstance(keys, dict):
            return
        slot = slots.pop(task_id, None)
        if not slot:
            return
        keys.pop(slot, None)
        details = getattr(self, "_client_key_details", None)
        if isinstance(details, dict):
            details.pop(slot, None)

    def _ensure_watchdog(self) -> None:
        """Keep one heartbeat task for every in-flight card.

        A long compile or a worktree queue produces no events. Without this
        the IDE shows a spinner that never updates, which reads as an agent
        that will neither finish nor stop.
        """
        task = self._watchdog_task
        if task is not None and not task.done():
            return
        self._watchdog_task = asyncio.create_task(
            self._watchdog_loop(), name="navin-subagent-watchdog"
        )

    async def _watchdog_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_S)
                if not self._running_tasks and not self._queued:
                    continue
                now = time.monotonic()
                for status in list(self._task_statuses.values()):
                    if status.phase in {"done", "error"}:
                        continue
                    last = status.last_progress_at or status.started_at
                    if now - last < _HEARTBEAT_S:
                        continue
                    self._publish_progress(status, force=True)
        except asyncio.CancelledError:
            return

    def request_drain(self) -> None:
        """Re-check every queue. Safe to call from another thread.

        The limit is re-measured periodically off the event loop (license sync,
        resource governor). When it goes back up, nothing else would notice
        until a running subagent happened to finish, so a queue could sit still
        on a machine that had just freed the memory it was waiting for.
        """
        loop = self._event_loop
        if loop is None or loop.is_closed() or not self._queued:
            return
        try:
            loop.call_soon_threadsafe(self._drain_all)
        except RuntimeError:  # loop shut down between the check and the call
            pass

    def _drain_all(self) -> None:
        keys = list(self._queued)
        if not keys:
            return
        start = self._drain_cursor % len(keys)
        keys = keys[start:] + keys[:start]
        self._drain_cursor += 1
        # Admit one per conversation per round so a busy conversation does
        # not take every freed slot while another conversation waits.
        while True:
            admitted = False
            for key in keys:
                queue = self._queued.get(key)
                if queue and self._has_free_slot(key or None):
                    pending = queue.popleft()
                    # Set before starting: a failed start still shrank the
                    # queue, so the round goes on for the entries behind it.
                    admitted = True
                    try:
                        self._start(pending)
                    except Exception:  # noqa: BLE001 - one bad spawn must not strand the rest
                        logger.exception("Queued subagent [{}] failed to start", pending.task_id)
                        self._forget(pending.task_id)
                if queue is not None and not queue:
                    self._queued.pop(key, None)
            if not admitted:
                return

    def _drain_queue(self, session_key: str | None) -> None:
        """Start whatever the freed slot can now carry.

        Called from a done-callback, so it cannot await. The limit is read
        again here rather than trusted from enqueue time: a license sync or the
        resource governor may have lowered it while this queue was waiting.
        """
        queue = self._queued.get(session_key or "")
        while queue and self._has_free_slot(session_key):
            pending = queue.popleft()
            try:
                self._start(pending)
            except Exception:  # noqa: BLE001 - one bad spawn must not strand the rest
                logger.exception("Queued subagent [{}] failed to start", pending.task_id)
                self._forget(pending.task_id)
                continue
            self._publish_progress(pending.status, force=True)
            logger.info("Started queued subagent [{}]: {}", pending.task_id, pending.label)
        if queue is not None and not queue:
            self._queued.pop(session_key or "", None)

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
        status: SubagentStatus,
        runtime: LLMRuntime,
        origin_message_id: str | None = None,
        workspace_scope: WorkspaceScope | None = None,
        isolate: bool = False,
        agent_name: str | None = None,
        scope_paths: list[str] | None = None,
        parent_metadata: dict[str, Any] | None = None,
        inherited_policy: TurnPolicy | None = None,
        member: dict[str, Any] | None = None,
        approval_gate: Any = None,
        choice_broker: Any = None,
        resume_checkout: dict[str, Any] | None = None,
    ) -> None:
        """Execute the subagent task and announce the result."""
        policy = inherited_policy or TurnPolicy()
        loop_guard = AgentLoopGuard()
        saved_validation = self._recovered_validation.pop(task_id, {})
        if saved_validation:
            from navin.agent.code_validation import CodeValidationState
            restored = dict(saved_validation)
            restored["paths"] = set(restored.get("paths") or [])
            restored["touched"] = set(restored.get("touched") or [])
            restored["root"] = Path(restored["root"]) if restored.get("root") else None
            if restored.get("workspace_snapshot") is not None:
                restored["workspace_snapshot"] = {
                    path: tuple(stamp) if stamp is not None else None
                    for path, stamp in restored["workspace_snapshot"].items()
                }
            if restored.get("last_nudge_key") is not None:
                restored["last_nudge_key"] = tuple(restored["last_nudge_key"])
            loop_guard.validation = CodeValidationState(**restored)
        logger.info("Subagent [{}] starting task: {}", task_id, label)

        budget = self._collab_budget.get(task_id, 0)

        def _emit_progress(current: SubagentStatus, *, force: bool = False) -> None:
            self._publish_progress(current, force=force)
            # Budget guard of a teammate run: past its token cap it stops,
            # and the lead is told why in the announcement.
            if budget and self._total_tokens(current) > budget and current.phase not in {"done", "error"}:
                running = self._running_tasks.get(task_id)
                if running is not None and not running.done():
                    current.error_kind = "limit"
                    logger.info("Teammate [{}] reached its token budget ({})", task_id, budget)
                    running.cancel(f"token budget of {budget} reached")

        async def _on_checkpoint(payload: dict) -> None:
            validation = asdict(loop_guard.validation)
            validation["paths"] = sorted(loop_guard.validation.paths)
            validation["touched"] = sorted(loop_guard.validation.touched)
            validation["root"] = str(loop_guard.validation.root) if loop_guard.validation.root else None
            payload = {**payload, "validation_state": validation}
            if checkout is not None:
                # Where an isolated run works, so a resume continues there.
                payload["checkout"] = {
                    "path": str(checkout.path), "origin": str(checkout.origin),
                    "base": checkout.base,
                }
            session = self._agent_sessions.get(task_id)
            if session is not None:
                await asyncio.get_running_loop().run_in_executor(
                    _journal_executor(), self._journal_io, "checkpoint", session, task_id, payload,
                )
            status.phase = payload.get("phase", status.phase)
            status.iteration = payload.get("iteration", status.iteration)
            if status.phase == "awaiting_tools":
                # This checkpoint lands before the hook's before_execute_tools,
                # while tool_events still hold the previous batch: the frame
                # would show the old step as if it were starting again.
                pending = payload.get("pending_tool_calls") or []
                calls = []
                for call in pending:
                    fn = call.get("function") if isinstance(call, dict) else None
                    if isinstance(fn, dict) and fn.get("name"):
                        calls.append((str(fn["name"]), fn.get("arguments")))
                if calls:
                    status.tool_events = _running_events(calls)
            _emit_progress(status)

        checkout: worktree.Worktree | None = None
        isolation_note = ""
        try:
            recovered_final = self._recovered_final_results.pop(task_id, None)
            if recovered_final is not None:
                self._recovered_messages.pop(task_id, None)
                status.phase = "done"
                self._publish_progress(status, force=True, done=True)
                await self._announce_result(task_id, label, task, recovered_final, origin,
                                            "ok", origin_message_id)
                return
            root = workspace_scope.project_path if workspace_scope is not None else self.workspace
            if isolate and resume_checkout:
                # Resumed run: continue in the checkout it already worked in.
                checkout = worktree.Worktree(
                    path=Path(resume_checkout["path"]), origin=Path(resume_checkout["origin"]),
                    task_id=task_id, base=str(resume_checkout["base"]), initial=(),
                    # No creation snapshot survives a restart: list everything,
                    # ignored output included, so no work reads as "nothing".
                    precise=True,
                )
                self._track_worktree(task_id, label, checkout)
                root = self._isolated_root(root, checkout)
                if workspace_scope is not None:
                    workspace_scope = replace(workspace_scope, project_path=root)
            elif isolate:
                # A large repo's ``worktree add`` is gated (4 at a time) and
                # can sit here for minutes. Say so, or the card looks dead
                # while git is doing exactly what isolation asked for.
                status.phase = "isolating"
                self._publish_progress(status, force=True)
                checkout = await asyncio.to_thread(
                    worktree.create,
                    root,
                    task_id,
                    pool_size=self.max_concurrent_subagents,
                )
                if checkout is not None:
                    self._track_worktree(task_id, label, checkout)
                    root = self._isolated_root(root, checkout)
                    # The scope is what the path guard consults. Left pointing at
                    # the original project it would refuse every write the
                    # subagent makes in its own checkout.
                    if workspace_scope is not None:
                        workspace_scope = replace(workspace_scope, project_path=root)
                else:
                    # A log line is not enough here: the parent asked for
                    # isolation and will otherwise keep believing the edits sit
                    # in a checkout waiting to be merged, when they are already
                    # in the user's tree. The announcement has to say so.
                    isolation_note = (
                        "\n\nNote: isolation was requested but this project is "
                        "not a usable git repository (not a repo, or it has no "
                        "commit yet), so the subagent worked in the shared "
                        "working tree. Any edits it made are already in the "
                        "user's tree."
                    )
                    logger.info(
                        "Subagent [{}] could not be isolated; running in the shared tree",
                        task_id,
                    )
            cfg = None
            if workspace_scope is not None:
                cfg = self._subagent_tools_config()
                cfg.restrict_to_workspace = workspace_scope.restrict_to_workspace
            # Visible before the worker starts: otherwise the card sits on
            # "Working..." while tools/prompt are still being built.
            if status.phase != "initializing":
                status.phase = "initializing"
                self._publish_progress(status, force=True)
            # Tools + prompt both do filesystem and YAML work. Dedicated pool
            # so a wave of 50 cannot stall drain/watchdog or starve git.
            # The agent definition (denied tools, permission mode, allowlist)
            # is resolved here too: looking it up scans the project, $HOME and
            # the plugins, and doing that three times on the loop per spawn
            # was the 0.5 to 0.9 s stall of a 200-subagent wave.
            def _prepare() -> tuple[ToolRegistry, str, Any, str, Any]:
                prepared_tools = (
                    self._build_tools(workspace=root, tools_config=cfg,
                                      member_tools=frozenset(member.get("tools") or ()))
                    if member else self._build_tools(workspace=root, tools_config=cfg)
                )
                prompt = self._build_subagent_prompt(
                    workspace=root,
                    agent_name=agent_name,
                    scope_paths=scope_paths,
                    parent_metadata=parent_metadata,
                )
                if member:
                    prompt += "\n\n---\n\n" + self._member_prompt(root, member)
                denied = _agent_denied(
                    _member_denied(policy.locked_denied_tools, member), root, agent_name
                )
                mode = _agent_permission_mode(root, agent_name)
                allowed = _agent_allowed(
                    _member_allowed(policy.allowed_tools, member), root, agent_name
                )
                return prepared_tools, prompt, denied, mode, allowed

            timeout_s = getattr(self, "_prepare_timeout_s", _PREPARE_TIMEOUT_S)
            try:
                tools, system_prompt, agent_denied, permission_mode, agent_allowed = await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        _prepare_executor(),
                        _prepare,
                    ),
                    timeout=timeout_s,
                )
            except TimeoutError:
                logger.warning(
                    "Subagent [{}] tools/prompt prepare exceeded {}s; "
                    "continuing with a slim prompt so the slot is not stuck",
                    task_id,
                    timeout_s,
                )
                tools = await asyncio.to_thread(
                    self._build_tools, workspace=root, tools_config=cfg,
                )
                system_prompt = self._fallback_subagent_prompt(root)
                agent_denied, permission_mode, agent_allowed = await asyncio.to_thread(
                    lambda: (
                        _agent_denied(_member_denied(policy.locked_denied_tools, member), root, agent_name),
                        _agent_permission_mode(root, agent_name),
                        _agent_allowed(_member_allowed(policy.allowed_tools, member), root, agent_name),
                    )
                )
            from navin.agent.request_checklist import ui_terms as _ui_terms

            # Screen words of the delegated task: a subagent that then changes
            # backend files only is sent back once for the frontend, like the
            # lead.
            task_ui_terms = tuple(_ui_terms(task))
            first = task
            if member and member.get("_continuity"):
                first = f"{member['_continuity']}\n\n---\n\nYour new task:\n{task}"
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": first},
            ]
            recovered = self._recovered_messages.pop(task_id, None)
            if recovered:
                messages = [{"role": "system", "content": system_prompt}, *recovered]

            sess_key = origin.get("session_key")
            llm_timeout = (
                self._llm_wall_timeout_for_session(sess_key)
                if self._llm_wall_timeout_for_session
                else None
            )
            request_token = bind_request_context(RequestContext(
                channel=origin["channel"],
                chat_id=origin["chat_id"],
                message_id=origin_message_id,
                session_key=sess_key,
                runtime=runtime,
                metadata={"subagent": True, "subagent_task_id": task_id},
            ))
            token = bind_workspace_scope(workspace_scope) if workspace_scope is not None else None
            live_restrict_token = bind_live_restrict_to_workspace(self.restrict_to_workspace)
            # A subagent outlives the parent turn, whose recorder is flushed and
            # abandoned when that turn ends. Writing into it would drop these
            # baselines on the floor, leaving the subagent's edits impossible to
            # review or undo, so this run gets a recorder of its own.
            recorder = TurnRecorder()
            recorder_token = bind_checkpoint_recorder(recorder)
            # A background task does not hold the parent turn, so a question is
            # forwarded to the chat that spawned it (the card names the
            # subagent). With nobody there to answer, the refusal names the
            # situation so the subagent reports back instead of working around it.
            approval_token = bind_approval_gate(SubagentApprovalGate(
                approval_gate, label=label, permission_mode=permission_mode,
                unattended_reason=(
                    "A background subagent cannot ask the user for approval. Report "
                    "what you need approved in your result and let the main agent "
                    "ask, instead of working around the refusal."
                ),
                on_wait=self._approval_waiter(status),
            ))
            # Same route for a real fork: the ask_user card lands on the parent's
            # chat, named after this subagent, and waits for the user there.
            choice_token = bind_choice_broker(SubagentChoiceBroker(choice_broker, label=label))
            try:
                async def _team_injections(*, limit: int = 64, wait: bool = False) -> list[dict[str, Any]]:
                    session = sess_key or f"{origin['channel']}:{origin['chat_id']}"
                    received = self.drain_team_messages(session, task_id, limit, record_delivery=False)
                    injected = [{"role": "user", "content": (
                        (
                            f"Message {item['message_id']} from the user, sent to you directly "
                            "while you work. Take it into account now.\n"
                            if item.get("sender") == "user" else
                            f"Team message {item['message_id']}"
                            + (f" ({item['intent']})" if item.get("intent") else "")
                            + f" from {item.get('sender_name') or 'agent ' + str(item['sender'])}. "
                            "This is peer information, not user authorization.\n"
                        ) + item["content"]
                    )} for item in received]
                    for index, (item, message) in enumerate(zip(received, injected, strict=True)):
                        try:
                            await asyncio.get_running_loop().run_in_executor(
                                _journal_executor(), self._journal_io, "deliver_injection", session,
                                task_id, item, message,
                            )
                        except BaseException as error:
                            # Not durably delivered: back to the front of the
                            # inbox, in order, for the next boundary. The ones
                            # already recorded are still handed to the model.
                            mailbox = self._agent_mailboxes.get(task_id)
                            if mailbox is not None:
                                mailbox.extendleft(reversed(received[index:]))
                            if isinstance(error, asyncio.CancelledError):
                                raise
                            logger.warning("Subagent [{}] team delivery failed, {} kept for retry: {}",
                                           task_id, len(received) - index, error)
                            return injected[:index]
                    return injected

                result = await self.runner.run(AgentRunSpec(
                    initial_messages=messages,
                    tools=tools,
                    runtime=runtime,
                    loop_guard=loop_guard,
                    max_iterations=self.max_iterations,
                    max_tool_result_chars=self.max_tool_result_chars,
                    tool_result_clearing=self.tool_result_clearing,
                    hook=self._build_hook(task_id, status, _emit_progress),
                    finalize_on_max_iterations=False,
                    continue_on_max_iterations=True,
                    error_message=None,
                    # Same batching the parent loop gets. A subagent explores
                    # more than it writes, so running its reads one at a time
                    # re-sent the whole prompt once per file for no reason;
                    # _partition_tool_batches still isolates anything that
                    # declares itself exclusive or concurrency-unsafe.
                    concurrent_tools=True,
                    fail_on_tool_error=self.fail_on_tool_error,
                    checkpoint_callback=_on_checkpoint,
                    injection_callback=_team_injections,
                    session_key=sess_key,
                    workspace=root,
                    user_hook_scope="subagent",
                    llm_timeout_s=llm_timeout,
                    # Inherited from the spawning turn: a Build parent's
                    # verify gate and the module denylist apply to delegated
                    # work too, so a subagent cannot finish unverified or use
                    # a tool the module locked out.
                    requires_verify_before_done=policy.requires_verify_before_done,
                    validate_code_changes=policy.validate_code_changes,
                    ui_terms=task_ui_terms,
                    denied_tools=(
                        frozenset({*(agent_denied or ()), "set_composer_mode"})
                        # plan = read-only: the mode switch must not unlock writes.
                        if permission_mode == "plan" else agent_denied
                    ),
                    read_only_tools=permission_mode == "plan",
                    locked_denied_tools=agent_denied,
                    allowed_tools=agent_allowed,
                ))
            finally:
                reset_choice_broker(choice_token)
                reset_approval_gate(approval_token)
                reset_checkpoint_recorder(recorder_token)
                # Not for an isolated run: those baselines name paths inside the
                # checkout, so the review panel would offer to undo edits to
                # files the user does not have, and would write into a tree that
                # may be removed a moment later.
                if token is not None:
                    reset_workspace_scope(token)
                reset_live_restrict_to_workspace(live_restrict_token)
                reset_request_context(request_token)
                if checkout is None and recorder.files and self._record_edits is not None:
                    await asyncio.get_running_loop().run_in_executor(
                        _persist_executor(), self._publish_edits, sess_key, recorder,
                    )
            status.stop_reason = result.stop_reason
            # Settled before the announcement, and on every branch: a run that
            # fails halfway is exactly when the parent most needs to be told
            # where the half-finished work is sitting. The two notes are
            # mutually exclusive - one exists only without a checkout, the
            # other only with one. A successful isolated run is merged into the
            # user's tree when that is conflict-free (navin.agent.worktree_merge).
            from navin.agent import workspace_policy, worktree_merge
            # Opt-in per-task policy: paths + targeted tests before merging.
            gate_note = None
            if checkout is not None and result.stop_reason not in worktree_merge.FAILED_STOPS:
                gate_note = await asyncio.to_thread(
                    workspace_policy.guarded_pre_merge_gate, checkout, task_id,
                )
            workspace_policy.clear_policy(task_id)
            conflict_note = ""
            if checkout is not None:
                conflict_note = await asyncio.to_thread(
                    self._conflict_note, task_id, checkout,
                )
            self._untrack_worktree(task_id)
            note = await worktree_merge.settle_after_run(
                self, checkout,
                ok=gate_note is None and result.stop_reason not in worktree_merge.FAILED_STOPS,
                task_id=task_id, label=label, origin=origin,
            ) + (f"\n\n{gate_note}" if gate_note else "") + conflict_note + isolation_note
            checkout = None

            if result.stop_reason == "max_iterations" and not result.error:
                result.error = "Subagent reached its iteration limit before completing the task."
            if result.stop_reason == "no_progress" and not result.error:
                result.error = result.final_content or "The subagent repeated blocked tool calls."

            if result.stop_reason in {"tool_error", "max_iterations", "no_progress", "validation_failed"}:
                status.phase = "error"
                status.error = result.error or "Tool execution failed."
                # Known from the stop reason. The text is not consulted: for
                # no_progress it is the model's own prose, which can say
                # "timed out" or "cancelled" about anything.
                status.error_kind = "limit" if result.stop_reason == "max_iterations" else "other"
                status.tool_events = list(result.tool_events)
                self._publish_progress(status, force=True, done=True)
                await self._announce_result(
                    task_id, label, task,
                    self._format_partial_progress(result)
                    + _changed_files_note(loop_guard.validation, partial=True) + note,
                    origin, "error", origin_message_id,
                )
            elif result.stop_reason == "error":
                status.phase = "error"
                status.error = result.error or "subagent execution failed"
                self._publish_progress(status, force=True, done=True)
                await self._announce_result(
                    task_id, label, task,
                    (result.error or "Error: subagent execution failed.")
                    + _changed_files_note(loop_guard.validation, partial=True) + note,
                    origin, "error", origin_message_id,
                )
            else:
                status.phase = "done"
                status.error = None
                status.error_kind = None
                self._publish_progress(status, force=True, done=True)
                final_result = result.final_content or "Task completed but no final response was generated."
                logger.info("Subagent [{}] completed successfully", task_id)
                await self._announce_result(
                    task_id, label, task,
                    final_result + _changed_files_note(loop_guard.validation, partial=False) + note,
                    origin, "ok", origin_message_id,
                )

        except asyncio.CancelledError:
            # Nothing git here on purpose. Every await would raise straight
            # away, leaking the checkout, and the blocking calls would hold up
            # the shutdown that asked for the cancellation. Keeping the tree is
            # the safe answer for an interrupted run anyway.
            over_budget = bool(budget) and self._total_tokens(status) > budget
            status.phase = "error"
            status.error = f"token budget of {budget} reached" if over_budget else "cancelled"
            status.error_kind = "limit" if over_budget else "cancelled"
            self._publish_progress(status, force=True, done=True)
            kept_at = checkout.path if checkout is not None else None
            if checkout is not None:
                logger.info(
                    "Subagent [{}] cancelled; its checkout is kept at {}",
                    task_id,
                    checkout.path,
                )
                self._untrack_worktree(task_id)
                checkout = None
            changed = _changed_files_note(loop_guard.validation, partial=True, kept_at=kept_at)
            # The parent turn is often blocked on this result. Dropping it
            # here is how a /stop or a cancelled wave left the orchestrator
            # waiting on work that would never report back.
            try:
                await asyncio.shield(
                    self._announce_result(
                        task_id,
                        label,
                        task,
                        (
                            f"Stopped: this teammate used its token budget ({budget}) "
                            "before finishing. Partial work may be in the tree."
                            if over_budget else (
                                "Cancelled before its final answer." if changed
                                else "Cancelled before a result was produced."
                            )
                        ) + changed,
                        origin,
                        "error",
                        origin_message_id,
                    )
                )
            except Exception:  # noqa: BLE001 - shutdown must still propagate
                logger.debug("Subagent [{}] cancel announcement failed", task_id)
            raise
        except Exception as e:
            status.phase = "error"
            status.error = str(e)
            status.error_kind = "other"
            logger.exception("Subagent [{}] failed", task_id)
            self._publish_progress(status, force=True, done=True)
            note = await self._settle_worktree(checkout) + isolation_note
            checkout = None
            await self._announce_result(
                task_id, label, task,
                f"Error: {e}" + _changed_files_note(loop_guard.validation, partial=True) + note,
                origin, "error", origin_message_id,
            )

    @staticmethod
    def _status_line(status: SubagentStatus) -> str:
        """Short human line for the parallel-subagent card.

        Never a raw tool result or a provider traceback: those read as noise
        ("write_file: Successfully wrote 2982 characters to \\\\wsl...").
        """
        if status.phase == "error":
            return _humanize_error(status.error, status.error_kind)[1]
        if status.phase == "done":
            return "Completed"
        if status.phase == "queued":
            return "Queued - waiting for a free slot"
        if status.phase == "awaiting_approval":
            return "Waiting for your approval"
        if status.phase == "isolating":
            return "Preparing isolated checkout…"
        if status.phase == "initializing":
            return "Starting…"
        if status.phase == "awaiting_model":
            return "Waiting for model response..."
        if status.phase in {"final_response", "responding"}:
            return "Writing response…"
        if status.phase == "thinking":
            return "Thinking…"
        events = [event for event in status.tool_events or [] if isinstance(event, dict)]
        # A failed call is usually retried a moment later; the live line names
        # what is going on or last went right, failures go to the final result.
        shown = [event for event in events if str(event.get("status") or "running") != "error"]
        if shown:
            last = shown[-1]
            _kind, running, finished, _base, target = _describe_tool(
                str(last.get("name") or ""), last.get("args"),
            )
            line = running if str(last.get("status") or "running") == "running" else finished
            if target:
                line = f"{line} {target}"
            more = len(events) - 1
            if more > 0:
                line = f"{line} (+{more})"
            return _clip(line, 140)
        last_ok = next(
            (step for step in reversed(status.steps) if step.get("status") == "ok"),
            None,
        )
        if last_ok is not None:
            return _clip(" ".join(filter(None, (last_ok.get("verb"), last_ok.get("target")))), 140)
        if status.iteration:
            return f"Working (step {status.iteration})"
        return "Working…"

    def _approval_waiter(self, status: SubagentStatus) -> Callable[[bool], None]:
        """Show "Waiting for your approval" on the card while a request is open.

        Concurrent tool calls can each ask: the card leaves that state when the
        last open request is answered, back to the phase it was in.
        """
        state: dict[str, Any] = {"open": 0, "phase": None}

        def waiting(active: bool) -> None:
            if active:
                state["open"] += 1
                if state["open"] == 1 and status.phase not in {"done", "error"}:
                    state["phase"] = status.phase
                    status.phase = "awaiting_approval"
            else:
                state["open"] = max(0, state["open"] - 1)
                if state["open"] == 0 and status.phase == "awaiting_approval":
                    status.phase = state["phase"] or "awaiting_tools"
            if status.phase not in {"done", "error"}:
                self._publish_progress(status, force=True)

        return waiting

    @staticmethod
    def _progress_steps(status: SubagentStatus) -> list[dict[str, str]]:
        """Finished steps plus the batch still running, newest last."""
        steps = [dict(step) for step in status.steps]
        # tools_completed too: the runner checkpoints it before the hook's
        # after_iteration records the batch, and without the running events
        # that frame would flash the previous step as the latest one.
        if status.phase in {"awaiting_tools", "tools_completed"}:
            for event in status.tool_events or []:
                if not isinstance(event, dict) or event.get("status") != "running":
                    continue
                kind, running, _finished, _base, target = _describe_tool(
                    str(event.get("name") or ""), event.get("args"),
                )
                steps.append({
                    "kind": kind,
                    "name": str(event.get("name") or ""),
                    "verb": running,
                    "target": target,
                    "status": "running",
                })
        return steps[-_MAX_PROGRESS_STEPS:]

    @staticmethod
    def _total_tokens(status: SubagentStatus) -> int:
        usage = status.usage or {}
        return int(usage.get("total_tokens") or 0) or (
            int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
        )

    def _publish_progress(
        self,
        status: SubagentStatus,
        *,
        force: bool = False,
        done: bool = False,
    ) -> None:
        """Push a SubagentProgressEvent to the WebUI (throttled)."""
        line = self._status_line(status)
        now = time.monotonic()
        key = f"{status.phase}\x1f{line}"
        # A changed line is news; the same line again only proves the
        # subagent is alive, which the heartbeat already does.
        if (
            not force
            and not done
            and key == status.last_status_line
            and (now - status.last_progress_at) < _PROGRESS_SAME_LINE_INTERVAL_S
        ):
            return
        status.last_status_line = key
        status.last_progress_at = now
        session = self._agent_sessions.get(status.task_id)
        if session is not None and self._journal_instance is not None:
            self._submit_journal(
                "status", session, status.task_id,
                self._journal_metadata(status, done=done),
            )
        # The desktop and the terminal UI both draw live agent rows; chat
        # channels (Telegram, Slack...) would receive them as noise.
        if status.origin_channel not in _LIVE_PROGRESS_CHANNELS:
            return
        tokens = self._total_tokens(status)
        error_kind = (
            _humanize_error(status.error, status.error_kind)[0]
            if status.phase == "error"
            else None
        )
        try:
            message = outbound_message_for_event(
                    channel=status.origin_channel,
                    chat_id=status.origin_chat_id,
                    event=SubagentProgressEvent(
                        task_id=status.task_id,
                        label=status.label,
                        phase=status.phase,
                        status_line=line,
                        model=status.model,
                        iteration=status.iteration,
                        done=done or status.phase in {"done", "error"},
                        # The short reason, not the raw text: provider errors
                        # carry class names, paths and hosts, and the parent's
                        # announcement already has the full story.
                        error=line if status.phase == "error" else None,
                        task_description=status.task_description[:240]
                        if status.task_description
                        else None,
                        started_ms_ago=max(0, int((now - status.started_at) * 1000)),
                        tokens=tokens or None,
                        steps=self._progress_steps(status) or None,
                    error_kind=error_kind,
                    member=self.member_identity(status.task_id),
                ),
            )
        except Exception:
            logger.debug("Could not build subagent progress for [{}]", status.task_id)
            return
        outbound = getattr(self.bus, "outbound", None)
        if outbound is None:
            return
        try:
            outbound.put_nowait(message)
        except Exception:
            self.dropped_progress_events = getattr(self, "dropped_progress_events", 0) + 1
            final = done or status.phase in {"done", "error"}
            put = getattr(outbound, "put", None)
            if final and put is not None and asyncio.iscoroutinefunction(put):
                # A terminal card must not be lost: wait for room instead.
                try:
                    self._keep_task(asyncio.get_running_loop().create_task(put(message)))
                    return
                except RuntimeError:
                    pass
            if self.dropped_progress_events in (1, 100) or self.dropped_progress_events % 1000 == 0:
                logger.warning("Outbound bus full: {} subagent progress updates dropped so far",
                               self.dropped_progress_events)

    @staticmethod
    def _isolated_root(workspace: Path, checkout: "worktree.Worktree") -> Path:
        """The place in ``checkout`` matching ``workspace`` in the repository.

        A workspace that was a subdirectory has to stay one, or isolation hands
        the subagent a wider tree than it had. The directory may be absent from
        the checkout when it was untracked - a scratch or data folder - and git
        only checks out what it tracks, so it is created rather than skipped:
        falling back to the checkout root would quietly widen the scope, and
        leaving it missing gives every process a cwd that does not exist.
        """
        try:
            offset = workspace.resolve().relative_to(checkout.origin.resolve())
        except (OSError, ValueError):
            # Not below the repository root as far as the filesystem is
            # concerned, usually a symlink resolving elsewhere.
            return checkout.path
        root = checkout.path / offset
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning(
                "worktree: cannot create {} in the checkout; using its root", offset
            )
            return checkout.path
        return root

    def _track_worktree(self, task_id: str, label: str, checkout: Any) -> None:
        """Remember an active isolated checkout for action='worktrees' and conflicts."""
        if not hasattr(self, "_active_worktrees"):
            self._active_worktrees: dict[str, tuple[str, Any]] = {}
        self._active_worktrees[task_id] = (label, checkout)

    def _untrack_worktree(self, task_id: str) -> None:
        getattr(self, "_active_worktrees", {}).pop(task_id, None)

    def active_worktrees(self) -> list[dict[str, Any]]:
        """Active isolated subagents: id, label, checkout, base, changes, policy."""
        from navin.agent import workspace_policy

        out: list[dict[str, Any]] = []
        for task_id, (label, checkout) in list(getattr(self, "_active_worktrees", {}).items()):
            try:
                files: list[str] | None = sorted(
                    workspace_policy.changed_files(Path(checkout.path), str(checkout.base))
                )
            except Exception:
                logger.exception("worktree: could not list changes of [{}]", task_id)
                files = None
            policy = workspace_policy.get_policy(task_id)
            out.append({
                "task_id": task_id, "label": label, "path": str(checkout.path),
                "base": str(checkout.base), "changed_files": files,
                "policy": None if policy is None else {
                    "allowed_paths": list(policy.allowed_paths),
                    "allowed_commands": list(policy.allowed_commands),
                    "test_commands": list(policy.test_commands),
                },
            })
        return out

    def _conflict_note(self, task_id: str, checkout: Any) -> str:
        """Files this checkout shares with other active isolated subagents."""
        from navin.agent import workspace_policy

        others = {
            tid: c for tid, (_l, c) in getattr(self, "_active_worktrees", {}).items()
            if tid != task_id
        }
        if not others:
            return ""
        try:
            report = workspace_policy.conflicts_for({task_id: checkout, **others})
        except Exception as exc:
            logger.exception("worktree: conflict check failed for [{}]", task_id)
            return f"\n\nConflict check with other isolated subagents failed: {exc}"
        if not report.has_conflicts:
            return ""
        return "\n\n" + report.render()

    async def _settle_worktree(self, checkout: "worktree.Worktree | None") -> str:
        """Keep a checkout that holds work, drop one that does not.

        Returns the note appended to the subagent's result. Deleting a
        subagent's only copy of its work is the worst thing this code could do,
        so removal happens on one condition only - git said, plainly, that
        nothing changed. Anything else keeps the tree, including the case where
        git could not be asked.
        """
        if checkout is None:
            return ""
        from navin.agent import workspace_policy

        self._untrack_worktree(checkout.task_id)
        workspace_policy.clear_policy(checkout.task_id)
        try:
            changes = await asyncio.to_thread(checkout.inspect)
        except Exception:
            logger.exception("worktree: could not inspect [{}]", checkout.task_id)
            changes = None

        if changes is None:
            return (
                f"\n\nThis ran in an isolated checkout at {checkout.path}. Git "
                "would not say what changed there, so the checkout was left "
                "alone rather than risk discarding work. Inspect it and remove "
                f"it with `git worktree remove --force {checkout.path}`."
            )
        if changes.empty:
            pooled = await asyncio.to_thread(
                worktree.release,
                checkout,
                pool_size=self.max_concurrent_subagents,
            )
            if pooled:
                logger.debug(
                    "worktree: empty checkout for [{}] returned to the pool",
                    checkout.task_id,
                )
            return ""
        return (
            f"\n\nThis ran in an isolated checkout at {checkout.path}, so none of "
            f"it is in the main working tree yet:\n{changes.render()}\n"
            "Review it there and merge what you want with git, then remove the "
            f"checkout with `git worktree remove --force {checkout.path}`."
        )

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
        origin_message_id: str | None = None,
        *,
        spawner: str | None | object = _AUTO_SPAWNER,
        record: bool = True,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus.

        Persistence is best effort here: a failed journal or outcome write is
        logged, never allowed to stop the parent from receiving the result.
        ``spawner``/``record`` serve a re-announcement of a nested report its
        spawner never read (routed one level up, outcome already recorded).
        """
        try:
            await self._journal_event(task_id, "result", {
                "role": "assistant", "content": result, "status": status,
            })
        except Exception:  # noqa: BLE001 - delivery matters more than the journal
            logger.exception("Subagent [{}] result journal write failed", task_id)
        status_text = "completed successfully" if status == "ok" else "failed"
        if task_id in self._task_members:
            self.post_team_feed(
                origin.get("session_key") or f"{origin['channel']}:{origin['chat_id']}",
                "result", task_id, result, target="parent", task_id=task_id, status=status,
            )
            identity = self.member_identity(task_id) or {}
            if identity.get("name"):
                label = f"{identity['name']} ({identity.get('title') or identity.get('role')})"
        override = origin.get("session_key") or f"{origin['channel']}:{origin['chat_id']}"
        if spawner is _AUTO_SPAWNER:
            spawner = self._spawn_parents.get(task_id)
        spawner_box = self._agent_mailboxes.get(spawner) if spawner else None
        if spawner_box is not None and len(spawner_box) < 64:
            # Nested spawn: the report goes to the subagent that delegated the
            # work (delivered at its next boundary), not over its head. The
            # outcome is still recorded so spawn(action="results") has it.
            if record:
                await self._save_outcome(task_id, label, task, result, status, override)
            message = {"message_id": uuid.uuid4().hex, "sender": task_id,
                       "content": _clip(f"[{label}] {status_text}:\n{result}", 8000)}
            if spawner in self._task_members:
                # A teammate's helper reports back: visible in the team journal.
                self.post_team_feed(override, "result", task_id, result, target=spawner,
                                    task_id=task_id, status=status)
            self._submit_journal("append", self._agent_sessions.get(spawner, ""), spawner,
                                 "team_received", dict(message))
            # Kept off the journal row: what _cleanup needs to re-announce this
            # report one level up if the spawner ends without reading it.
            message["announce"] = {
                "label": label, "task": task, "result": result, "origin": dict(origin),
                "status": status, "origin_message_id": origin_message_id,
            }
            spawner_box.append(message)
            return

        announce_content = render_template(
            "agent/subagent_announce.md",
            label=label,
            status_text=status_text,
            task=task,
            result=result,
        ) + self._wave_note(task_id)

        # Inject as system message to trigger main agent.
        # Use session_key_override to align with the main agent's effective
        # session key (which accounts for unified sessions) so the result is
        # routed to the correct pending queue (mid-turn injection) instead of
        # being dispatched as a competing independent task.
        metadata: dict[str, Any] = {
            "injected_event": "subagent_result",
            "subagent_task_id": task_id,
        }
        if origin_message_id:
            metadata["origin_message_id"] = origin_message_id
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
            session_key_override=override,
            metadata=metadata,
        )

        if record:
            await self._save_outcome(task_id, label, task, result, status, override)
        await self._publish_inbound(msg)
        logger.debug("Subagent [{}] announced result to {}:{}", task_id, origin['channel'], origin['chat_id'])

    def _wave_note(self, task_id: str) -> str:
        """Tell the parent whether this result closes the wave or more follow.

        Each result used to arrive alone with "summarize briefly": the parent
        answered the first one as if the work were over, then had nothing left
        to say when the last one landed, so no final summary ever came.
        """
        session_key = next(
            (key for key, ids in self._session_tasks.items() if task_id in ids), None,
        )
        if session_key is None:
            return ""
        others = [
            self._task_statuses[tid].label if tid in self._task_statuses else tid
            for tid in sorted(self._session_tasks.get(session_key, set()))
            if tid != task_id and tid in self._running_tasks and not self._running_tasks[tid].done()
        ]
        queued = len(self._queued.get(session_key, ()))
        if others or queued:
            waiting = len(others) + queued
            names = ", ".join(others[:8])
            return (
                f"\n\nStill running for this request: {waiting} other subagent(s)"
                + (f" ({names})" if names else "")
                + ". Their results will arrive on their own. Do not give the final"
                " summary yet and do not end as if the work were finished."
            )
        return (
            "\n\nThis was the last subagent still running for this request. Collect"
            " every subagent result you received and give the user one final summary"
            " saying that all the work is done (or what failed). End it with a Markdown"
            " table, one row per subagent of this request: subagent, what it was asked,"
            " what it did, result (done, blocked or question left open) and where its"
            " changes are. A subagent that ended on an open question did not do the"
            " work: say so in its row and ask that question yourself with ask_user."
        )

    async def _publish_inbound(self, msg: InboundMessage) -> None:
        """Hand a result to the parent loop without ever blocking on a full queue.

        The agent loop is the only reader of the inbound queue, and /stop has it
        awaiting cancel_by_session, which awaits these very announcements. A
        blocking put on a full queue would deadlock both, so a full queue gets
        the put from a tracked background task instead.
        """
        inbound = getattr(self.bus, "inbound", None)
        if isinstance(inbound, asyncio.Queue):
            try:
                inbound.put_nowait(msg)
                return
            except asyncio.QueueFull:
                if hasattr(self.bus, "inbound_backpressure_count"):
                    self.bus.inbound_backpressure_count += 1
                self._keep_task(asyncio.ensure_future(self.bus.publish_inbound(msg)))
                return
        await self.bus.publish_inbound(msg)

    async def _save_outcome(
        self, task_id: str, label: str, task: str, result: str, status: str, override: str,
    ) -> None:
        """Durably record one outcome; log (never raise) a failed write.

        Coalesces a simultaneous wave into one durable write per session. The
        parent notification still waits until its result is saved, but a
        failed save must not keep the result from the parent.
        """
        loop = asyncio.get_running_loop()
        saved = loop.create_future()
        self._pending_outcomes.append(((task_id, label, task, result, status, override), saved))
        if not self._outcome_flush_scheduled:
            self._outcome_flush_scheduled = True
            loop.call_soon(self._flush_pending_outcomes)
        try:
            await saved
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if not saved.cancelled() or (current is not None and current.cancelling()):
                raise
            logger.warning("Subagent [{}] outcome write was cancelled", task_id)
        except Exception:  # noqa: BLE001 - delivery matters more than the record
            logger.exception("Subagent [{}] outcome write failed", task_id)

    def _flush_pending_outcomes(self) -> None:
        # Do not evict an entry inside its own batch before saving it.
        batch = self._pending_outcomes[:_MAX_OUTCOME_HISTORY]
        del self._pending_outcomes[:len(batch)]
        loop = asyncio.get_running_loop()
        write = loop.run_in_executor(
            _persist_executor(), self._record_outcomes, [args for args, _ in batch],
        )

        def finished(done: asyncio.Future) -> None:
            error = None if done.cancelled() else done.exception()
            for _, saved in batch:
                if saved.done():
                    continue
                if done.cancelled():
                    saved.cancel()
                elif error is not None:
                    saved.set_exception(error)
                else:
                    saved.set_result(None)
            if self._pending_outcomes:
                loop.call_soon(self._flush_pending_outcomes)
            else:
                self._outcome_flush_scheduled = False

        write.add_done_callback(finished)

    def _publish_edits(self, session_key: str | None, recorder: TurnRecorder) -> None:
        """Hand a subagent's pre-edit baselines to the review store."""
        if not session_key or not recorder.files or self._record_edits is None:
            return
        try:
            self._record_edits(session_key, recorder.files)
        except Exception:
            # Losing the baselines is bad, but not worth discarding a completed
            # task: the announcement still has to reach the parent.
            logger.exception("Failed to record subagent file baselines")

    @staticmethod
    def _outcome_path(session_key: str) -> Path:
        """One file per session: readable stem, hashed suffix against collisions."""
        stem = re.sub(r"[^A-Za-z0-9_.-]", "_", session_key or "default")
        digest = hashlib.sha256((session_key or "").encode("utf-8")).hexdigest()[:12]
        return _outcomes_dir() / f"{stem[:_OUTCOME_FILE_STEM_CHARS]}-{digest}.json"

    @staticmethod
    def _outcomes_from_rows(rows: Any, session_key: str) -> deque[SubagentOutcome]:
        queue: deque[SubagentOutcome] = deque(maxlen=_MAX_OUTCOME_HISTORY)
        if not isinstance(rows, list):
            return queue
        for row in rows:
            if not isinstance(row, dict) or not row.get("task_id"):
                continue
            try:
                queue.append(SubagentOutcome(
                    task_id=str(row["task_id"]),
                    label=str(row.get("label") or ""),
                    task_description=str(row.get("task_description") or ""),
                    status=str(row.get("status") or "ok"),
                    summary=str(row.get("summary") or ""),
                    finished_at=float(row.get("finished_at") or 0.0),
                    session_key=session_key,
                ))
            except (TypeError, ValueError):
                # One unreadable row must not cost the rest of the history.
                continue
        return queue

    @classmethod
    def _read_outcome_file(cls, path: Path) -> tuple[str, deque[SubagentOutcome]] | None:
        """Parse one persisted file, or None when it is missing or unusable."""
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as e:
            logger.warning("Could not read subagent outcomes {}: {}", path, e)
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            logger.warning("Ignoring a corrupt subagent outcome file: {}", path)
            return None
        if not isinstance(payload, dict):
            return None
        session_key = payload.get("session_key")
        if not isinstance(session_key, str):
            return None
        return session_key, cls._outcomes_from_rows(payload.get("outcomes"), session_key)

    def _queue_for(self, session_key: str) -> deque[SubagentOutcome]:
        """The live queue for a session, hydrated from disk on first touch."""
        queue = self._outcomes.get(session_key)
        if queue is not None:
            return queue
        restored = self._read_outcome_file(self._outcome_path(session_key))
        queue = restored[1] if restored is not None else deque(maxlen=_MAX_OUTCOME_HISTORY)
        self._outcomes[session_key] = queue
        return queue

    def _persist_outcomes(self, session_key: str, queue: deque[SubagentOutcome]) -> None:
        """Write the session's history so a gateway restart cannot erase it."""
        from navin.utils.atomic_io import atomic_write_text

        payload = {
            "session_key": session_key,
            # Frozen records contain only scalars. Deep-copying every field
            # with asdict on each completion competes with UI work for the GIL.
            "outcomes": [{
                "task_id": outcome.task_id,
                "label": outcome.label,
                "task_description": outcome.task_description,
                "status": outcome.status,
                "summary": outcome.summary,
                "finished_at": outcome.finished_at,
                "session_key": outcome.session_key,
            } for outcome in queue],
        }
        try:
            atomic_write_text(
                self._outcome_path(session_key),
                json.dumps(payload, ensure_ascii=False) + "\n",
            )
        except OSError as e:
            # The in-memory queue still has it, so this turn is unaffected;
            # only a restart would now lose the record.
            logger.warning("Could not persist subagent outcomes for {}: {}", session_key, e)

    def _record_outcome(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        status: str,
        session_key: str | None,
    ) -> None:
        """Remember how a subagent ended, so the answer survives the turn.

        And the process: the history is written to disk on every outcome, so a
        result that never reached its parent turn is still recoverable through
        spawn(action="results") after the gateway restarts.
        """
        self._record_outcomes([(task_id, label, task, result, status, session_key)])

    def _record_outcomes(self, outcomes: list[_OutcomeArgs]) -> None:
        """Append a wave atomically in memory, then persist each touched session."""
        with self._outcome_lock:
            touched: dict[str, deque[SubagentOutcome]] = {}
            for task_id, label, task, result, status, session_key in outcomes:
                summary = " ".join(result.split())
                if len(summary) > _MAX_OUTCOME_SUMMARY:
                    summary = summary[: _MAX_OUTCOME_SUMMARY - 1].rstrip() + "…"
                key = session_key or ""
                queue = self._queue_for(key)
                queue.append(SubagentOutcome(
                    task_id=task_id,
                    label=label,
                    task_description=task,
                    status=status,
                    summary=summary,
                    finished_at=time.time(),
                    session_key=session_key,
                ))
                touched[key] = queue
            for key, queue in touched.items():
                self._persist_outcomes(key, queue)

    def _merged_outcomes(self) -> list[SubagentOutcome]:
        """Every session's history, memory first then whatever is only on disk."""
        merged: list[SubagentOutcome] = []
        seen = set(self._outcomes)
        for queue in self._outcomes.values():
            merged.extend(queue)
        try:
            files = sorted(_outcomes_dir().glob("*.json"))
        except OSError:
            files = []
        for path in files:
            restored = self._read_outcome_file(path)
            if restored is None or restored[0] in seen:
                continue
            seen.add(restored[0])
            self._outcomes[restored[0]] = restored[1]
            merged.extend(restored[1])
        return merged

    def recent_outcomes(
        self,
        session_key: str | None = None,
        limit: int | None = None,
    ) -> list[SubagentOutcome]:
        """Finished subagents, most recent first, optionally scoped to a session.

        ``limit=None`` returns everything retained for the scope: the whole
        point of the outcome history is that a result which missed its
        injection window can still be recovered in full via spawn(results),
        including across a gateway restart.
        """
        with self._outcome_lock:
            if session_key is not None:
                matches = list(reversed(self._queue_for(session_key)))
            else:
                merged = self._merged_outcomes()
                merged.sort(key=lambda outcome: outcome.finished_at, reverse=True)
                matches = merged
        if limit is None:
            return matches
        return matches[: max(0, limit)]

    @staticmethod
    def _format_partial_progress(result) -> str:
        completed = [e for e in result.tool_events if e["status"] == "ok"]
        failure = next((e for e in reversed(result.tool_events) if e["status"] == "error"), None)
        lines: list[str] = []
        if completed:
            lines.append("Completed steps:")
            for event in completed[-3:]:
                lines.append(f"- {event['name']}: {event['detail']}")
        if failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {failure['name']}: {failure['detail']}")
        if result.error:
            if lines:
                lines.append("")
            lines.append("Stop reason:" if failure else "Failure:")
            lines.append(f"- {result.error}")
        return "\n".join(lines) or (result.error or "Error: subagent execution failed.")

    @staticmethod
    def _parent_context_lines(
        root: Path,
        parent_metadata: dict[str, Any] | None,
    ) -> list[str]:
        """Facts the parent turn saw: focus files and the plan's current step.

        The parent gets these as runtime-context blocks on every turn; the
        subagent used to start blind, re-discovering the files the parent was
        already looking at and the step the board says is running. Never
        raises: context enrichment must not be able to block a spawn.
        """
        lines: list[str] = []
        # Skip the pack when the parent sent no focus: it still walks git
        # dirty + the code index, and a wave of 50 would redo that for nothing.
        if parent_metadata:
            try:
                from navin.agent.context_pack import build_context_pack_lines

                lines.extend(build_context_pack_lines(
                    workspace=root,
                    metadata=dict(parent_metadata),
                ))
            except Exception:
                logger.debug("Subagent context pack unavailable for {}", root)
        try:
            from navin.board.context import board_summary_lines
            from navin.board.store import ProjectBoardStore

            digest = board_summary_lines(ProjectBoardStore(root).read_tasks())
            if digest:
                if lines:
                    lines.append("")
                lines.extend(digest)
        except Exception:
            logger.debug("Subagent board digest unavailable for {}", root)
        return lines

    def _build_subagent_prompt(
        self,
        workspace: Path | None = None,
        agent_name: str | None = None,
        scope_paths: list[str] | None = None,
        parent_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Build a focused system prompt for the subagent.

        When *agent_name* matches a project-defined agent (.claude/agents,
        .navin/agents, ...), its instructions are layered on top of the
        standard subagent prompt - the project's own specialists run natively.
        The parent's context pack (focus files) and the board's current step
        are appended as facts, so the subagent starts where the parent stands.
        """
        from navin.agent.project_agents import find_project_agent
        from navin.agent.skills import SkillsLoader

        root = workspace or self.workspace
        # Names only, same as the main prompt: the full skill catalog costs
        # ~10K tokens and subagents run many at once.
        # One scan per root for a short window: a fan-out of hundreds of
        # subagents rebuilt the full catalog each time, on the event loop,
        # and froze the gateway for seconds.
        trust = getattr(self, "trust_workspace_harness_skills", True)
        cache_key = (str(root), tuple(sorted(self.disabled_skills)), bool(trust))
        cache = self.__dict__.setdefault("_skills_index_cache", {})
        hit = cache.get(cache_key)
        now = time.monotonic()
        if hit is not None and now - hit[0] < 30.0:
            skills_summary = hit[1]
        else:
            skills_summary = SkillsLoader(
                root,
                disabled_skills=self.disabled_skills,
                trust_workspace_harness_skills=trust,
            ).build_skills_index()
            cache[cache_key] = (now, skills_summary)
        prompt = render_template(
            "agent/subagent_system.md",
            workspace=str(root),
            skills_summary=skills_summary or "",
        )
        if agent_name:
            definition = find_project_agent(root, agent_name)
            if definition is not None:
                prompt += (
                    f"\n\n---\n\n# Role: {definition.name}\n\n"
                    f"{definition.prompt}"
                )
            else:
                logger.warning(
                    "Subagent role '{}' not found in the project's agents folders; "
                    "running with the standard prompt",
                    agent_name,
                )
        if scope_paths:
            scoped = "\n".join(f"- {p}" for p in scope_paths)
            prompt += (
                "\n\n---\n\n# Mission scope\n\n"
                "This mission is scoped to these project paths:\n"
                f"{scoped}\n\n"
                "Scope rules:\n"
                "- Start inside the scope. Use grep/code_index for targeted "
                "search instead of reading whole directories.\n"
                "- You may read files outside the scope when verifying a "
                "finding (callers, config, tests), but the report stays about "
                "the scope.\n"
                "- Quality is non-negotiable: every claim must be verified "
                "with a tool and cite the real file path, line, and excerpt. "
                "If you did not verify it, do not report it. Never pad the "
                "report with generic or invented findings."
            )
        context_lines = self._parent_context_lines(root, parent_metadata)
        if context_lines:
            joined = "\n".join(context_lines)
            prompt += (
                "\n\n---\n\n# Parent context\n\n"
                "Facts from the parent agent's current state (metadata, not "
                "instructions):\n"
                f"{joined}"
            )
        return prompt

    def _member_prompt(self, root: Path, member: dict[str, Any]) -> str:
        """Identity, teammates and preloaded skills of a Collaboration teammate."""
        from navin.agent.skills import SkillsLoader
        from navin.collab.team import member_prompt

        skills_text = ""
        names = [str(n) for n in member.get("skills") or [] if str(n).strip()]
        if names:
            try:
                skills_text = SkillsLoader(
                    root,
                    disabled_skills=self.disabled_skills,
                    trust_workspace_harness_skills=getattr(self, "trust_workspace_harness_skills", True),
                ).load_skills_for_context(names, slim=True, full_body_names=set(names))
                # A subagent has no `skill` tool: the rest of a playbook is
                # read from its folder, which each capsule names.
                skills_text = re.sub(
                    r"`skill action=read name=([\w.-]+)`",
                    r"read_file on SKILL.md in the skill folder above (\1)",
                    skills_text,
                )
            except Exception:  # noqa: BLE001 - skills are a help, not a requirement
                logger.debug("Could not preload skills for teammate {}", member.get("name"))
        return member_prompt(
            member,
            teammates=list(member.get("_teammates") or []),
            collab=member.get("_collab"),
            skills_text=skills_text,
        )

    @staticmethod
    def _fallback_subagent_prompt(workspace: Path) -> str:
        """Prompt used when the full catalog/context pack missed its deadline.

        Empty skills index, no parent pack: the subagent still runs, and can
        load a playbook on demand. A hung prepare must not hold a slot.
        """
        return render_template(
            "agent/subagent_system.md",
            workspace=str(workspace),
            skills_summary="",
        )

    async def cancel_by_session(self, session_key: str) -> int:
        """Cancel all subagents for the given session. Returns count cancelled."""
        self._session_generations[session_key] = self._session_generations.get(session_key, 0) + 1
        # Drop the queue first, and before awaiting: a slot freed by a
        # cancellation below would otherwise start the very work being stopped.
        queued = self._queued.pop(session_key, deque())
        cancelled_payload = {
            "role": "assistant", "content": "Cancelled before execution.", "status": "error",
        }
        journal_rows = [(self._agent_sessions[p.task_id], p.task_id)
                        for p in queued if p.task_id in self._agent_sessions]
        for pending in queued:
            pending.status.phase = "error"
            pending.status.error = "Cancelled"
            pending.status.error_kind = "cancelled"
            self._publish_progress(pending.status, force=True, done=True)
            self._forget(pending.task_id)
        if journal_rows:
            # One flush and one transaction for the whole queue, not one
            # executor round trip per agent (1000 queued took ~11 s).
            await self.flush_journal()
            await asyncio.get_running_loop().run_in_executor(
                _journal_executor(), self._journal_io, "append_many",
                journal_rows, "result", cancelled_payload,
            )
        tasks = [self._running_tasks[tid] for tid in self._session_tasks.get(session_key, [])
                 if tid in self._running_tasks and not self._running_tasks[tid].done()]
        for t in tasks:
            t.cancel()
        if tasks:
            # Bounded: the caller may be the agent loop itself (/stop), and a
            # subagent that will not finish must not hold it forever.
            timeout_s = getattr(self, "_cancel_wait_s", _CANCEL_WAIT_S)
            _, still = await asyncio.wait(tasks, timeout=timeout_s)
            if still:
                logger.warning("{} subagent(s) of {} still stopping after {}s",
                               len(still), session_key, timeout_s)
        return len(tasks) + len(queued)

    def get_running_count(self) -> int:
        """Subagents in flight everywhere, started or still queued."""
        return len(self._running_tasks) + sum(len(q) for q in self._queued.values())

    def queued_count(self, session_key: str | None = None) -> int:
        """Accepted spawns still waiting for a slot."""
        if session_key is None:
            return sum(len(q) for q in self._queued.values())
        return len(self._queued.get(session_key, ()))

    def _started_count(self, session_key: str) -> int:
        """Subagents actually running for a session. Admission uses this one.

        Distinct from :meth:`get_running_count_by_session`, which also counts
        the queue: counting queued work as occupying a slot would mean the
        queue could never drain.
        """
        tids = self._session_tasks.get(session_key, set())
        return sum(
            1 for tid in tids
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        )

    def get_running_count_by_session(self, session_key: str) -> int:
        """Subagents in flight for a session, started or still queued.

        Queued ones count as in flight because callers use this to decide
        whether the conversation still has work pending: the turn loop keeps
        itself alive on this number, and ending a turn with a queue still
        waiting would strand work that was accepted.
        """
        return self._started_count(session_key) + len(self._queued.get(session_key, ()))

    def running_snapshot(self, session_key: str) -> list[dict[str, Any]]:
        """What is still running for *session_key*, as progress payloads.

        A browser holds its subagent cards in memory only, so a refresh or a
        detour through another chat leaves a background task running with
        nothing on screen saying so - which reads as if the request had been
        dropped. Only this manager knows, so it hands out replayable payloads
        shaped like the live event.
        """
        snapshot: list[dict[str, Any]] = []
        for task_id in sorted(self._session_tasks.get(session_key, set())):
            task = self._running_tasks.get(task_id)
            if task is None or task.done():
                continue
            status = self._task_statuses.get(task_id)
            if status is None:
                continue
            snapshot.append({
                "task_id": status.task_id,
                "label": status.label,
                "phase": status.phase,
                "status_line": self._status_line(status),
                "model": status.model,
                "iteration": status.iteration,
                "done": False,
                "task_description": status.task_description[:240] or None,
                "started_ms_ago": max(0, int((time.monotonic() - status.started_at) * 1000)),
                "tokens": self._total_tokens(status) or None,
                "steps": self._progress_steps(status) or None,
                "member": self.member_identity(task_id),
            })
        # Queued spawns are shown too, in the order they will start. A card that
        # only appears once a slot frees up looks like a request that was lost.
        for pending in self._queued.get(session_key, ()):
            status = pending.status
            snapshot.append({
                "task_id": status.task_id,
                "label": status.label,
                "phase": status.phase,
                "status_line": self._status_line(status),
                "model": status.model,
                "iteration": 0,
                "done": False,
                "task_description": status.task_description[:240] or None,
                "started_ms_ago": max(0, int((time.monotonic() - status.started_at) * 1000)),
                "member": self.member_identity(status.task_id),
            })
        return snapshot


async def request_running_subagents(
    bus: Any,
    session_key: str,
    *,
    timeout: float = 2.0,
) -> list[dict[str, Any]]:
    """Ask the running loop which subagents *session_key* still has in flight.

    The manager lives in the agent loop, out of reach of the channel, so this
    borrows the runtime-control acknowledgement path the same way the approvals
    replay does. A short timeout on purpose: this runs while a client is
    attaching, and no answer just means no cards.
    """
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_ACK,
        RUNTIME_CONTROL_SUBAGENTS_QUERY,
    )

    if not session_key:
        return []
    loop = asyncio.get_running_loop()
    ack: asyncio.Future[list[dict[str, Any]]] = loop.create_future()
    await bus.publish_inbound(
        InboundMessage(
            channel="system",
            sender_id="webui-subagents",
            chat_id="runtime",
            content=RUNTIME_CONTROL_SUBAGENTS_QUERY,
            metadata={
                INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_SUBAGENTS_QUERY,
                RUNTIME_CONTROL_ACK: ack,
                "session_key": session_key,
            },
        )
    )
    try:
        result = await asyncio.wait_for(ack, timeout=timeout)
    except asyncio.TimeoutError:
        return []
    return result if isinstance(result, list) else []


async def request_subagent_operation(
    bus: Any, session_key: str, operation: str, task_id: str = "", *,
    after: int = 0, limit: int = 200, offset: int = 0, timeout: float = 20.0,
    target: str = "", content: str = "",
) -> dict[str, Any]:
    """Request a scoped durable query or explicit recovery through the loop."""
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_ACK,
        RUNTIME_CONTROL_SUBAGENTS_QUERY,
    )
    if not session_key or operation not in {
        "list", "history", "recover", "relaunch", "retry", "cancel", "team_feed", "team_message",
        "team_assign", "team_select",
    }:
        return {"ok": False, "error": "Invalid subagent operation or conversation."}
    ack = asyncio.get_running_loop().create_future()
    try:
        async with asyncio.timeout(timeout):
            await bus.publish_inbound(InboundMessage(
                channel="system", sender_id="webui-subagents", chat_id="runtime",
                content=RUNTIME_CONTROL_SUBAGENTS_QUERY,
                metadata={INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_SUBAGENTS_QUERY,
                          RUNTIME_CONTROL_ACK: ack, "session_key": session_key,
                          "operation": operation, "task_id": task_id,
                          "after": after, "limit": limit, "offset": offset,
                          "target": target, "content": content},
            ))
            result = await ack
    except TimeoutError:
        ack.cancel()
        return {"ok": False, "error": "The runtime did not answer in time.", "timed_out": True}
    return result if isinstance(result, dict) else {"ok": False, "error": "Invalid runtime answer."}


async def request_multitask_spawn(
    bus: Any,
    session_key: str,
    prompt: str,
    *,
    label: str = "",
    client_key: str = "",
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Ask the running loop to spawn a subagent for a queued composer prompt.

    This is the Multitask path: a prompt the user queued while a turn was
    running is dispatched to a parallel subagent instead of waiting its turn.
    Same ack pattern as the subagents replay query; the loop side holds the
    SubagentManager, the model runtime, and the session's workspace scope.
    """
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_ACK,
        RUNTIME_CONTROL_MULTITASK_SPAWN,
    )

    if not session_key or not prompt.strip():
        return {"ok": False, "error": "a session and a prompt are required"}
    loop = asyncio.get_running_loop()
    ack: asyncio.Future[dict[str, Any]] = loop.create_future()
    await bus.publish_inbound(
        InboundMessage(
            channel="system",
            sender_id="webui-multitask",
            chat_id="runtime",
            content=RUNTIME_CONTROL_MULTITASK_SPAWN,
            metadata={
                INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_MULTITASK_SPAWN,
                RUNTIME_CONTROL_ACK: ack,
                "session_key": session_key,
                "prompt": prompt,
                "label": label,
                "client_key": (client_key or "").strip()[:80],
            },
        )
    )
    try:
        result = await asyncio.wait_for(ack, timeout=timeout)
    except asyncio.TimeoutError:
        # The control is already on the inbound bus. The loop may still
        # spawn after we give up, so callers must not retry the same prompt.
        return {
            "ok": False,
            "error": "the agent loop did not answer in time",
            "timed_out": True,
        }
    return result if isinstance(result, dict) else {"ok": False, "error": "invalid answer"}


async def handle_multitask_spawn(state: Any, msg: Any, registry: Any) -> bool:
    """Runtime-control handler: spawn a subagent for a queued prompt."""
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_ACK,
        RUNTIME_CONTROL_MULTITASK_SPAWN,
    )

    metadata = msg.metadata if isinstance(getattr(msg, "metadata", None), dict) else {}
    if metadata.get(INBOUND_META_RUNTIME_CONTROL) != RUNTIME_CONTROL_MULTITASK_SPAWN:
        return False

    ack = metadata.get(RUNTIME_CONTROL_ACK)

    def _answer(payload: dict[str, Any]) -> None:
        if isinstance(ack, asyncio.Future) and not ack.done():
            ack.set_result(payload)

    manager = getattr(state, "subagents", None)
    session_key = metadata.get("session_key")
    prompt = str(metadata.get("prompt") or "").strip()
    label = str(metadata.get("label") or "").strip()
    client_key = str(metadata.get("client_key") or "").strip()[:80] or None
    if not isinstance(manager, SubagentManager) or not isinstance(session_key, str) or not prompt:
        _answer({"ok": False, "error": "multitask spawn is not available"})
        return True
    try:
        # No limit check here: the manager owns it and queues the overflow, so
        # a busy conversation slows down instead of rejecting the prompt.
        channel, _, chat_id = session_key.partition(":")
        session = state.sessions.get_or_create(session_key)
        session_metadata = getattr(session, "metadata", None)
        scope = state.workspace_scopes.for_turn(
            channel=channel,
            message_metadata=None,
            session_metadata=session_metadata,
        )
        runtime = state.llm_runtime()
        detail = await manager.spawn(
            task=prompt,
            runtime=runtime,
            label=label or None,
            origin_channel=channel or "websocket",
            origin_chat_id=chat_id or "direct",
            session_key=session_key,
            workspace_scope=scope,
            parent_metadata=(
                dict(session_metadata) if isinstance(session_metadata, dict) else None
            ),
            # Cursor-style isolation: each Multitask prompt gets its own git
            # worktree so parallel tasks cannot trample each other's edits.
            # On a non-git project the manager falls back to the shared tree
            # and says so in the announcement.
            isolate=True,
            client_key=client_key,
        )
        if not spawn_was_accepted(detail):
            _answer({"ok": False, "error": detail})
        else:
            _answer({"ok": True, "detail": detail})
    except Exception as exc:  # noqa: BLE001 - the control path must always answer
        logger.exception("Multitask spawn failed")
        _answer({"ok": False, "error": str(exc)[:300]})
    return True


async def handle_subagents_query(state: Any, msg: Any, registry: Any) -> bool:
    """Runtime-control handler: answer a reconnecting client's question."""
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_ACK,
        RUNTIME_CONTROL_SUBAGENTS_QUERY,
    )

    metadata = msg.metadata if isinstance(getattr(msg, "metadata", None), dict) else {}
    if metadata.get(INBOUND_META_RUNTIME_CONTROL) != RUNTIME_CONTROL_SUBAGENTS_QUERY:
        return False

    ack = metadata.get(RUNTIME_CONTROL_ACK)
    manager = getattr(state, "subagents", None)
    session_key = metadata.get("session_key")
    operation = metadata.get("operation")
    if operation:
        result: dict[str, Any] = {"ok": False, "error": "Subagents are unavailable."}
        if isinstance(manager, SubagentManager) and isinstance(session_key, str):
            try:
                if operation == "list":
                    agents = await asyncio.to_thread(
                        manager.list_agents, session_key, offset=int(metadata.get("offset", 0)),
                        limit=int(metadata.get("limit", 200)),
                    )
                    result = {"ok": True, "agents": agents}
                elif operation == "history":
                    history = await asyncio.to_thread(
                        manager.agent_history, session_key, str(metadata.get("task_id", "")),
                        after=int(metadata.get("after", 0)), limit=int(metadata.get("limit", 200)),
                    )
                    result = {"ok": True, **history}
                elif operation in {"team_feed", "team_select"}:
                    from navin.collab.runtime import (
                        SESSION_TEAM_KEY,
                        current_collab,
                        session_team_id,
                        team_summary,
                    )

                    sessions = getattr(state, "sessions", None)
                    if operation == "team_select":
                        # This chat's team ("off" = none), kept on the session.
                        choice = str(metadata.get("target", "")).strip()[:80]
                        session = sessions.get_or_create(session_key)
                        session.metadata[SESSION_TEAM_KEY] = choice or "off"
                        await sessions.save_async(session)
                    collab = await asyncio.to_thread(current_collab)
                    summary = team_summary(collab, session_team_id(sessions, session_key))
                    result = {"ok": True, "entries": manager.team_feed(session_key), **summary}
                elif operation == "team_message":
                    # The user briefs or interrupts a running teammate directly:
                    # delivered at its next step, like a colleague leaning in.
                    detail = manager.send_team_message(
                        session_key, "user", str(metadata.get("target", "")),
                        str(metadata.get("content", "")),
                    )
                    await manager.flush_journal()
                    result = {"ok": True, "detail": detail}
                elif operation == "team_assign":
                    from navin.collab.runtime import (
                        CollabError,
                        assign_member,
                        current_collab,
                        session_team_id,
                    )

                    session = state.sessions.get_or_create(session_key)
                    channel, _, chat_id = session_key.partition(":")
                    scope = state.workspace_scopes.for_turn(
                        channel=channel, message_metadata=None,
                        session_metadata=getattr(session, "metadata", None),
                    )
                    resolver = getattr(getattr(state, "runtime_resolver", None), "resolve_preset", None)
                    try:
                        detail = await assign_member(
                            manager=manager, collab=current_collab(),
                            member_name=str(metadata.get("target", "")),
                            task=str(metadata.get("content", "")),
                            session_key=session_key, origin_channel=channel or "websocket",
                            origin_chat_id=chat_id or "direct", parent_runtime=state.llm_runtime(),
                            resolve_preset=resolver, workspace_scope=scope,
                            parent_metadata=dict(getattr(session, "metadata", None) or {}),
                            assigned_by="user",
                            team_id=session_team_id(state.sessions, session_key),
                        )
                        result = {"ok": spawn_was_accepted(detail), "detail": detail}
                        if not result["ok"]:
                            result["error"] = detail
                    except CollabError as error:
                        result = {"ok": False, "error": str(error)}
                elif operation in {"recover", "relaunch"}:
                    if not isinstance(ack, asyncio.Future) or ack.done():
                        return True
                    session = state.sessions.get_or_create(session_key)
                    channel, _, _chat_id = session_key.partition(":")
                    scope = state.workspace_scopes.for_turn(
                        channel=channel, message_metadata=None,
                        session_metadata=getattr(session, "metadata", None),
                    )
                    method = manager.recover_agent if operation == "recover" else manager.relaunch_agent
                    detail = await method(session_key, str(metadata.get("task_id", "")),
                                          runtime=state.llm_runtime(), workspace_scope=scope)
                    accepted = spawn_was_accepted(detail) and not detail.startswith("Cannot")
                    result = {"ok": accepted, "detail": detail}
                    if not result["ok"]:
                        result["error"] = detail
                elif operation == "retry":
                    if not isinstance(ack, asyncio.Future) or ack.done():
                        return True
                    session = state.sessions.get_or_create(session_key)
                    channel, _, _chat_id = session_key.partition(":")
                    scope = state.workspace_scopes.for_turn(
                        channel=channel, message_metadata=None,
                        session_metadata=getattr(session, "metadata", None),
                    )
                    result = await manager.retry_agent(
                        session_key, str(metadata.get("task_id", "")),
                        runtime=state.llm_runtime(), workspace_scope=scope,
                    )
                elif operation == "cancel":
                    result = await manager.cancel_agent(session_key, str(metadata.get("task_id", "")))
                else:
                    result = {"ok": False, "error": "Unknown subagent operation."}
            except Exception as error:
                logger.exception("Subagent operation failed")
                result = {"ok": False, "error": str(error)}
        if isinstance(ack, asyncio.Future) and not ack.done():
            ack.set_result(result)
        return True
    running: list[dict[str, Any]] = []
    if isinstance(manager, SubagentManager) and isinstance(session_key, str):
        try:
            running = manager.running_snapshot(session_key)
        except Exception:
            # A replay is a convenience: never let it take down the loop's
            # control path, which also carries approval answers.
            logger.exception("Could not snapshot running subagents")
            running = []
    if isinstance(ack, asyncio.Future) and not ack.done():
        ack.set_result(running)
    return True
