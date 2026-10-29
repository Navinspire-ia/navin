# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Internal turn continuation helpers.

This module keeps budget-boundary continuation policy out of ``AgentLoop``.
The loop calls a small set of helpers; those helpers decide whether an internal
continuation is allowed and, when it is, queue the next turn directly.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from typing import Any, Mapping, MutableMapping

from loguru import logger

from navin.session.goal_state import (
    goal_state_runtime_lines,
    sustained_goal_active,
    sustained_goal_turn,
)

INTERNAL_CONTINUATION_META = "_internal_continuation"
INTERNAL_CONTINUATION_KIND_META = "_internal_continuation_kind"
INTERNAL_CONTINUATION_PENDING_META = "_internal_continuation_pending"
INTERNAL_CONTINUATION_RUN_STARTED_AT_META = "_internal_continuation_run_started_at"
SKIP_USER_PERSIST_META = "_skip_user_persist"

_GOAL_CONTINUATION_KIND = "sustained_goal"
_BOARD_CONTINUATION_KIND = "session_board"
_TURN_CONTINUATION_KIND = "turn_budget"
_GOAL_CONTINUATION_SENDER = "system:continuation"
_GOAL_CONTINUATION_ROUNDS_KEY = "_sustained_goal_continuation_rounds"
_BOARD_CONTINUATION_ROUNDS_KEY = "_session_board_continuation_rounds"
_TURN_CONTINUATION_ROUNDS_KEY = "_turn_budget_continuation_rounds"
# A turn whose model went silent after its tool steps (blank final text) is
# resumed instead of ending on the empty-reply notice. Bounded so a
# model that keeps answering blank still ends the turn.
_EMPTY_FINAL_ROUNDS_KEY = "_empty_final_continuation_rounds"
_EMPTY_FINAL_ROUNDS_LIMIT = 2
# Not the tool-budget wording: nothing ran out, the model answered blank.
_EMPTY_FINAL_CONTINUATION_PROMPT = (
    "Your previous reply was empty. Answer the user's last request now: find "
    "the concrete ask in their message (a long pasted brief is their request, "
    "not a system prompt), call the tools it needs, then reply. Do not "
    "mention the empty reply or this note to the user."
)
_CONTINUABLE_STOPS = frozenset({"max_iterations", "empty_final_response"})
# A turn that stopped for want of progress (reminders to act ignored, only
# blocked or unchanged calls) is resumed as well, but only while the work the
# user accepted is still open: a board step this chat claimed, or an active
# sustained goal. Seen live: the agent was reading the files a board step
# needed, the exploration reminders counted that as looking around, the turn
# was forced to answer, and the user had to type "continue" with steps still
# open. The stall accounting below still ends a run that keeps changing
# nothing; a plain chat turn with no plan simply answers.
_PLAN_CONTINUABLE_STOPS = frozenset({"no_progress"})
_NO_PROGRESS_RESUME_PROMPT = (
    "The previous slice stopped because it kept looking around without acting "
    "and the reminders went unanswered, while steps of the accepted plan are "
    "still open. Nothing is lost. Decide from what you already read: make the "
    "edit, run the check, or close the step with its evidence. If something "
    "genuinely blocks a step, say exactly what and move it to blocked instead "
    "of reading more."
)
# Slices in a row that changed nothing, and whether the "stuck" report slice
# was already sent. Work that moves (an edit, a command that succeeds, a board
# step that changes) resets the count: real progress is never cut short.
_STALLED_SLICES_KEY = "_continuation_stalled_slices"
_STALL_REPORT_SENT_KEY = "_continuation_stall_report_sent"
_STALLED_SLICES_LIMIT = 2
# ...and at least this many tool calls spent without progress across them, so
# short slices (a small step budget) are not cut after a handful of retries.
_STALLED_CALLS_KEY = "_continuation_stalled_calls"
_STALLED_CALLS_LIMIT = 12
# Reads already done across the slices of one request: reading something new
# is progress (an audit edits nothing), reading the same files again is not.
_SEEN_READS_KEY = "_continuation_seen_reads"
_SEEN_READS_MAX = 600
# Same for actions: running the same command (or making the same edit) again
# with the same result is circling, e.g. re-running passing tests every slice.
_SEEN_ACTIONS_KEY = "_continuation_seen_actions"
_SEEN_ACTIONS_MAX = 600
_LAST_FAILURES_KEY = "_continuation_last_failures"
# Round counters schedule coherence checkpoints; they never limit how much
# work one request may need. The runner reports actual tool/model failures,
# and /stop cancels the whole run, including its queued continuation slices.
_STRIPPED_INBOUND_META_KEYS = {
    INTERNAL_CONTINUATION_PENDING_META,
    "goal_requested",
    "original_command",
}


def internal_continuation_inbound(metadata: Mapping[str, Any] | None) -> bool:
    """True for an inbound message created by an internal continuation policy."""
    return bool(metadata and metadata.get(INTERNAL_CONTINUATION_META) is True)


def ends_with_user_question(final_content: str | None) -> bool:
    """True when the turn's final message ends by asking the user something.

    A message whose last line ends with a question mark is the model handing
    control back to the user ("Tu veux que je commence par lequel ?"). Any
    internal continuation scheduled on top of it would contradict the model's
    own decision to wait and burn slices doing nothing.
    """
    if not isinstance(final_content, str):
        return False
    lines = [line.strip() for line in final_content.strip().splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return False
    last = lines[-1].rstrip("*_`) \u00bb\u201d\"'")
    return last.endswith(("?", "؟"))


def is_goal_continuation_inbound(metadata: Mapping[str, Any] | None) -> bool:
    """True only for continuation slices owned by a sustained goal.

    Board and turn-budget continuations must NOT be dropped when no goal is
    active - they have no goal to begin with. Legacy messages without a kind
    predate board continuations and were always goal slices.
    """
    if not internal_continuation_inbound(metadata):
        return False
    kind = (metadata or {}).get(INTERNAL_CONTINUATION_KIND_META)
    return kind in (None, "", _GOAL_CONTINUATION_KIND)


def internal_continuation_pending(metadata: Mapping[str, Any] | None) -> bool:
    """True when the current turn scheduled an invisible continuation slice."""
    return bool(metadata and metadata.get(INTERNAL_CONTINUATION_PENDING_META) is True)


def internal_continuation_run_started_at(metadata: Mapping[str, Any] | None) -> float | None:
    """Return the user-visible run start propagated across continuation slices."""
    if not metadata:
        return None
    value = metadata.get(INTERNAL_CONTINUATION_RUN_STARTED_AT_META)
    if not isinstance(value, int | float):
        return None
    started_at = float(value)
    return started_at if started_at > 0 else None


def should_persist_user_message(metadata: Mapping[str, Any] | None) -> bool:
    """Return whether this inbound message should be persisted as user input."""
    if metadata and metadata.get(SKIP_USER_PERSIST_META) is True:
        return False
    return not internal_continuation_inbound(metadata)


def should_stream_budget_response(
    *,
    stop_reason: str,
    pending_queue_available: bool,
    session_metadata: Mapping[str, Any] | None,
    message_metadata: Mapping[str, Any] | None = None,
    session_key: str | None = None,
    final_content: str | None = None,
) -> bool:
    """Return whether the budget-boundary response should be sent to the user."""
    if stop_reason != "max_iterations":
        return True
    # A real question is delivered, never hidden by a continuation. A slice
    # of successful reads or custom tools may be essential work, so tool
    # names alone must not turn a scheduling boundary into a hard stop.
    if ends_with_user_question(final_content):
        return True
    return should_finalize_on_max_iterations(
        pending_queue_available=pending_queue_available,
        session_metadata=session_metadata,
        message_metadata=message_metadata,
        session_key=session_key,
    )


def should_finalize_on_max_iterations(
    *,
    pending_queue_available: bool,
    session_metadata: Mapping[str, Any] | None,
    message_metadata: Mapping[str, Any] | None = None,
    session_key: str | None = None,
) -> bool:
    """Return whether a max-iteration boundary should produce a final response.

    Every unfinished interactive task can continue internally. A slice should
    end without a no-tools finalization call; the eventual completed or blocked
    turn owns the user-visible response.
    """
    return not (
        _continuation_available(
            stop_reason="max_iterations",
            pending_queue_available=pending_queue_available,
            session_metadata=session_metadata,
            message_metadata=message_metadata,
            session_key=session_key,
        )
    )


async def maybe_continue_turn(ctx: Any) -> bool:
    """Queue an internal continuation for *ctx* when policy allows it."""
    if ctx.session is None or ctx.pending_queue is None:
        return False
    kind = _continuation_kind(
        stop_reason=ctx.stop_reason,
        pending_queue_available=True,
        session_metadata=ctx.session.metadata,
        message_metadata=ctx.msg.metadata,
        session_key=ctx.session_key,
    )
    if not kind:
        if ctx.stop_reason in _CONTINUABLE_STOPS or ctx.stop_reason in _PLAN_CONTINUABLE_STOPS:
            _park_board_after_hard_stop(ctx)
        return False
    if ctx.stop_reason == "empty_final_response":
        _increment_continuation_round(ctx.session.metadata, _EMPTY_FINAL_ROUNDS_KEY)
    # The turn ended by asking the user something (plan approval, a choice,
    # missing input). Auto-resuming here would swallow the question and spin
    # doing nothing while the user thinks the agent is working: deliver the
    # question, stop the plan spinner, and wait for the answer instead.
    if ends_with_user_question(getattr(ctx, "final_content", None)):
        logger.info(
            "Turn ended with a question to the user; delivering it instead of "
            "scheduling a {} continuation",
            kind,
        )
        _park_board_after_hard_stop(ctx)
        return False
    session_meta = ctx.session.metadata
    if session_meta.get(_STALL_REPORT_SENT_KEY):
        # The stuck report was the last slice: deliver it, do not resume.
        logger.info("Continuation stopped after the stuck report")
        session_meta.pop(_STALL_REPORT_SENT_KEY, None)
        session_meta.pop(_STALLED_SLICES_KEY, None)
        _park_board_after_hard_stop(ctx)
        return False
    report, stalled = record_slice(
        session_meta, list(ctx.all_messages or []), len(getattr(ctx, "initial_messages", []) or []),
    )
    metadata = _internal_continuation_metadata(
        ctx.msg.metadata,
        kind=kind,
        run_started_at=getattr(ctx, "visible_run_started_at", None),
    )
    if stalled:
        logger.warning(
            "{} slices in a row changed nothing; asking for a stuck report instead of resuming",
            session_meta.get(_STALLED_SLICES_KEY),
        )
        session_meta[_STALL_REPORT_SENT_KEY] = True
        content = _STALL_REPORT_PROMPT + _failures_note(report.failures)
        # No tool at all in that slice: the model can only write the report.
        from navin.command.modules import ALLOWED_TOOLS_METADATA_KEY

        metadata[ALLOWED_TOOLS_METADATA_KEY] = []
    elif kind == _GOAL_CONTINUATION_KIND:
        content = _goal_continuation_prompt(ctx.session.metadata)
        _increment_continuation_round(ctx.session.metadata, _GOAL_CONTINUATION_ROUNDS_KEY)
    elif kind == _BOARD_CONTINUATION_KIND:
        rounds = _increment_continuation_round(
            ctx.session.metadata, _BOARD_CONTINUATION_ROUNDS_KEY,
        )
        content = _board_continuation_prompt(rounds)
    else:
        rounds = _increment_continuation_round(
            ctx.session.metadata, _TURN_CONTINUATION_ROUNDS_KEY,
        )
        content = (
            _EMPTY_FINAL_CONTINUATION_PROMPT
            if ctx.stop_reason == "empty_final_response"
            else _turn_budget_continuation_prompt(rounds)
        )
    if (
        ctx.stop_reason == "empty_final_response"
        and kind != _TURN_CONTINUATION_KIND
        and not stalled
    ):
        content = f"{_EMPTY_FINAL_CONTINUATION_PROMPT}\n\n{content}"
    if ctx.stop_reason in _PLAN_CONTINUABLE_STOPS and not stalled:
        # Not a budget boundary: say what happened, then hand over the plan.
        content = f"{_NO_PROGRESS_RESUME_PROMPT}\n\n{content}"
    if not stalled:
        content += _failures_note(report.failures)

    messages = _strip_terminal_assistant(ctx.all_messages, ctx.final_content)

    logger.info("Turn budget reached; scheduling {} continuation", kind)
    ctx.msg.metadata[INTERNAL_CONTINUATION_PENDING_META] = True
    ctx.final_content = ""
    ctx.all_messages = messages
    ctx.suppress_response = True
    await ctx.pending_queue.put(
        dataclasses.replace(
            ctx.msg,
            sender_id=_GOAL_CONTINUATION_SENDER,
            content=content,
            media=[],
            metadata=metadata,
            session_key_override=ctx.session_key,
        )
    )
    return True


def prepare_save_boundary(ctx: Any) -> None:
    """Prepare continuation bookkeeping and the history append boundary."""
    if ctx.session is not None:
        clear_internal_continuation_state(ctx.session.metadata)

    ctx.save_skip = _save_skip_for_turn(
        message_metadata=ctx.msg.metadata,
        initial_message_count=len(ctx.initial_messages),
        history_count=len(ctx.history),
        user_persisted_early=ctx.user_persisted_early,
    )


def _continuation_kind(
    *,
    stop_reason: str,
    pending_queue_available: bool,
    session_metadata: Mapping[str, Any] | None,
    message_metadata: Mapping[str, Any] | None = None,
    session_key: str | None = None,
) -> str | None:
    plan_only = stop_reason in _PLAN_CONTINUABLE_STOPS
    if not pending_queue_available or (stop_reason not in _CONTINUABLE_STOPS and not plan_only):
        return None
    if stop_reason == "empty_final_response" and int(
        (session_metadata or {}).get(_EMPTY_FINAL_ROUNDS_KEY) or 0
    ) >= _EMPTY_FINAL_ROUNDS_LIMIT:
        return None
    if session_metadata and session_metadata.get(_STALL_REPORT_SENT_KEY):
        # The stuck report is the last slice: it must finish with an answer,
        # never schedule another one.
        return None
    if _goal_continuation_available(
        session_metadata,
        message_metadata=message_metadata,
    ):
        return _GOAL_CONTINUATION_KIND
    if _board_continuation_available(session_key=session_key, message_metadata=message_metadata):
        return _BOARD_CONTINUATION_KIND
    if plan_only:
        # No accepted plan behind this turn: the forced answer stands.
        return None
    return _TURN_CONTINUATION_KIND


def _continuation_available(
    *,
    stop_reason: str,
    pending_queue_available: bool,
    session_metadata: Mapping[str, Any] | None,
    message_metadata: Mapping[str, Any] | None = None,
    session_key: str | None = None,
) -> bool:
    return (
        _continuation_kind(
            stop_reason=stop_reason,
            pending_queue_available=pending_queue_available,
            session_metadata=session_metadata,
            message_metadata=message_metadata,
            session_key=session_key,
        )
        is not None
    )


def clear_internal_continuation_state(metadata: MutableMapping[str, Any]) -> None:
    """Reset policy bookkeeping once its owning runtime mode is inactive."""
    if not sustained_goal_active(metadata):
        reset_goal_continuation_rounds(metadata)


def reset_goal_continuation_rounds(metadata: MutableMapping[str, Any]) -> None:
    """Start a new goal with fresh checkpoint counters."""
    metadata.pop(_GOAL_CONTINUATION_ROUNDS_KEY, None)


def reset_budget_continuation_rounds(metadata: MutableMapping[str, Any]) -> None:
    """Restart checkpoint numbering for a new user-initiated turn."""
    metadata.pop(_BOARD_CONTINUATION_ROUNDS_KEY, None)
    metadata.pop(_TURN_CONTINUATION_ROUNDS_KEY, None)
    metadata.pop(_EMPTY_FINAL_ROUNDS_KEY, None)
    metadata.pop(_STALLED_SLICES_KEY, None)
    metadata.pop(_STALLED_CALLS_KEY, None)
    metadata.pop(_STALL_REPORT_SENT_KEY, None)
    metadata.pop(_SEEN_READS_KEY, None)
    metadata.pop(_SEEN_ACTIONS_KEY, None)
    metadata.pop(_LAST_FAILURES_KEY, None)


_EDIT_TOOLS = frozenset({"write_file", "edit_file", "apply_patch", "manage_files", "notebook_edit"})
_READ_ONLY_PROGRAMS = frozenset({
    "ls", "cat", "head", "tail", "wc", "grep", "rg", "find", "pwd", "echo", "tree", "stat",
    "file", "which", "type", "env", "printenv", "du", "df", "sed", "awk", "sort", "uniq", "less",
    "cd", "curl", "wget", "true", "test", "[", "sleep", "date", "whoami", "uname", "ps", "lsof",
    "ss", "netstat", "jq", "realpath", "dirname", "basename", "xargs", "tee", "nl", "cut", "tr",
})
# Board actions that move the plan. Everything else (list, get, next, claim,
# comment, log, ledger...) can be repeated every slice without changing a thing.
_BOARD_CHANGES = frozenset({"add", "create", "update", "move", "done", "complete", "split", "block", "replan"})
_REFUSAL = re.compile(
    r"validation pending|task remains open|user refused|refused this operation|not approved|"
    r"approval (?:timed out|denied)|blocked by",
    re.IGNORECASE,
)
_EXIT_CODE = re.compile(r"exit code:?\s*(-?\d+)", re.IGNORECASE)
_READ_ONLY_GIT = frozenset({"status", "log", "diff", "show", "branch", "remote", "rev-parse", "ls-files"})


@dataclasses.dataclass
class SliceReport:
    """What one continuation slice actually did, read from its tool results."""

    progressed: bool = False
    calls: int = 0
    # Something changed (edit, command that succeeded, board/goal move, app up),
    # as opposed to only reading things not read before.
    changed: bool = False
    failures: list[str] = dataclasses.field(default_factory=list)


def _read_only_command(command: str) -> bool:
    for segment in re.split(r"&&|\|\||[;|\n]", command):
        words = segment.strip().split()
        while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
            words.pop(0)
        if not words:
            continue
        program = words[0].rsplit("/", 1)[-1]
        if program == "git" and len(words) > 1 and words[1] in _READ_ONLY_GIT:
            continue
        if program not in _READ_ONLY_PROGRAMS:
            return False
    return True


def _tool_result_text(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return str(content or "")


_READ_TOOLS = frozenset({
    "read_file", "list_dir", "find_files", "grep", "search", "glob", "web_fetch", "web_search",
    "scrape", "file_search", "symbols", "lsp",
})


def _new_action(
    seen_actions: set[str] | None,
    name: str,
    args: dict[str, Any],
    result: str,
) -> bool:
    """True unless this exact call already ran with this exact result."""
    if seen_actions is None:
        return True
    payload = json.dumps(args, sort_keys=True, default=str) + "\0" + result[-2000:]
    signature = hashlib.sha1(f"{name}:{payload}".encode("utf-8", "replace")).hexdigest()[:12]
    if signature in seen_actions:
        return False
    seen_actions.add(signature)
    return True


def slice_report(
    all_messages: list[dict[str, Any]],
    initial_count: int,
    seen_reads: set[str] | None = None,
    seen_actions: set[str] | None = None,
) -> SliceReport:
    """Did this slice move anything, and which calls failed (to not repeat them)?

    Progress is a change in the world: a file edited, a command that ran and
    succeeded (not a read like ls/cat/git status), a board or goal step that
    moved, an app started. Reading and listing are not progress.
    """
    report = SliceReport()
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    for message in all_messages[initial_count:]:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                raw = function.get("arguments")
                try:
                    args = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
                except (ValueError, TypeError):
                    args = {}
                calls[str(call.get("id"))] = (str(function.get("name") or ""), args if isinstance(args, dict) else {})
            continue
        if message.get("role") != "tool":
            continue
        name, args = calls.get(str(message.get("tool_call_id")), (str(message.get("name") or ""), {}))
        report.calls += 1
        text = _tool_result_text(message)
        head = text.lstrip()[:400]
        # The exit code sits at the END of a long build log: take the last one.
        # File contents and search hits are not command output: a test file
        # that quotes "Exit code: 1" must not turn a read into a failure.
        codes = [] if name in _READ_TOOLS else _EXIT_CODE.findall(text)
        failed = (
            message.get("_tool_status") == "error"
            or head.startswith("Error")
            or bool(_REFUSAL.search(head))
            or (bool(codes) and codes[-1] != "0")
        )
        if failed:
            target = args.get("command") or args.get("cmd") or args.get("path") or args.get("url") or ""
            line = head.splitlines()[0][:220] if head else "failed"
            if codes and codes[-1] != "0" and not head.startswith("Error"):
                line = f"Exit code: {codes[-1]}"
            entry = f"{name}({str(target)[:120]}): {line}" if target else f"{name}: {line}"
            if entry not in report.failures:
                report.failures.append(entry)
            continue
        if name in _READ_TOOLS:
            # What was read, not how: another offset/limit of the same file is
            # not new information.
            target = next(
                (str(args[key]) for key in ("path", "file", "url", "query", "pattern", "glob", "symbol") if args.get(key)),
                json.dumps(args, sort_keys=True, default=str),
            )
            signature = hashlib.sha1(f"{name}:{target}".encode("utf-8", "replace")).hexdigest()[:12]
            if seen_reads is not None and signature not in seen_reads:
                seen_reads.add(signature)
                report.progressed = True
        elif name in _EDIT_TOOLS or name in {"start_app", "open_preview", "update_goal"}:
            if _new_action(seen_actions, name, args, text):
                report.progressed = report.changed = True
        elif name == "board" and str(args.get("action") or "") in _BOARD_CHANGES:
            report.progressed = report.changed = True
        elif name == "exec" and not _read_only_command(str(args.get("command") or args.get("cmd") or "")):
            if _new_action(seen_actions, name, args, text):
                report.progressed = report.changed = True
        elif name == "project" and str(args.get("action") or "") not in ("detect", "status"):
            report.progressed = report.changed = True
    report.failures = report.failures[-6:]
    return report


def _append_new(ordered: list[str], current: set[str]) -> list[str]:
    known = set(ordered)
    return ordered + sorted(item for item in current if item not in known)


def record_slice(
    state: MutableMapping[str, Any],
    all_messages: list[dict[str, Any]],
    initial_count: int,
) -> tuple[SliceReport, bool]:
    """Account one slice of work in ``state``; True when the run is stalled.

    Shared by budget continuations (state = session metadata) and by runs
    that never hand back to a dispatcher (subagents, one-shot CLI calls),
    which used to loop with no bound at all.
    """
    seen = state.get(_SEEN_READS_KEY)
    seen_list = list(seen) if isinstance(seen, list) else []
    seen_reads = set(seen_list)
    done = state.get(_SEEN_ACTIONS_KEY)
    done_list = list(done) if isinstance(done, list) else []
    seen_actions = set(done_list)
    report = slice_report(all_messages, initial_count, seen_reads, seen_actions)
    # Oldest first, newest last: trimming must drop the oldest entries, not
    # the alphabetically smallest ones.
    state[_SEEN_READS_KEY] = _append_new(seen_list, seen_reads)[-_SEEN_READS_MAX:]
    state[_SEEN_ACTIONS_KEY] = _append_new(done_list, seen_actions)[-_SEEN_ACTIONS_MAX:]
    previous_failures = state.get(_LAST_FAILURES_KEY) or []
    state[_LAST_FAILURES_KEY] = report.failures
    if (
        report.progressed
        and not report.changed
        and report.failures
        and set(report.failures) & set(previous_failures)
    ):
        # Reading around the same failing step again is circling, not progress:
        # new files read while the same error comes back change nothing.
        report.progressed = False
    if report.progressed:
        state.pop(_STALLED_SLICES_KEY, None)
        state.pop(_STALLED_CALLS_KEY, None)
    else:
        state[_STALLED_SLICES_KEY] = int(state.get(_STALLED_SLICES_KEY) or 0) + 1
        state[_STALLED_CALLS_KEY] = int(state.get(_STALLED_CALLS_KEY) or 0) + report.calls
    stalled = (
        int(state.get(_STALLED_SLICES_KEY) or 0) >= _STALLED_SLICES_LIMIT
        and int(state.get(_STALLED_CALLS_KEY) or 0) >= _STALLED_CALLS_LIMIT
    )
    return report, stalled


def _failures_note(failures: list[str]) -> str:
    if not failures:
        return ""
    lines = "\n".join(f"- {entry}" for entry in failures)
    return (
        "\n\nAlready tried in the previous slice and FAILED - do not repeat these "
        "calls as they are. Read the error, fix its cause (install the missing "
        "dependency, use the free port, the right script name, the right working "
        "directory) and run the corrected command directly; do not re-explore files "
        "you already read:\n" + lines
    )


_STALL_REPORT_PROMPT = (
    "Stop retrying: the last slices changed nothing and hit the same errors. "
    "Do not call any tool. Write the final message to the user now, in the "
    "language they wrote in: what you were doing, the exact command and error "
    "that blocks, the likely cause, and the one thing you need from them (a "
    "decision, a credential, a missing tool) to continue. Short and concrete."
)


def _save_skip_for_turn(
    *,
    message_metadata: Mapping[str, Any] | None,
    initial_message_count: int,
    history_count: int,
    user_persisted_early: bool,
) -> int:
    """Return the persisted-message append boundary for this turn."""
    if message_metadata and message_metadata.get(SKIP_USER_PERSIST_META) is True:
        return initial_message_count
    if internal_continuation_inbound(message_metadata):
        return initial_message_count
    # build_messages may merge the current message into a same-role history tail.
    # Runner-appended messages start at initial_message_count in either shape.
    has_standalone_current = initial_message_count > 1 + history_count
    if has_standalone_current and not user_persisted_early:
        return initial_message_count - 1
    return initial_message_count


def _goal_continuation_available(
    session_metadata: Mapping[str, Any] | None,
    *,
    message_metadata: Mapping[str, Any] | None = None,
) -> bool:
    if not sustained_goal_turn(session_metadata, message_metadata=message_metadata):
        return False
    return sustained_goal_active(session_metadata)


_CLOSED_TASK_STATUSES = frozenset({"done", "cancelled", "canceled", "archived"})


def _board_continuation_available(
    *,
    session_key: str | None = None,
    message_metadata: Mapping[str, Any] | None = None,
) -> bool:
    """True when this chat still has an open board task mid-build.

    "Touched the board once" is not enough: that list is never pruned, so any
    later, unrelated request in the chat got the build prompt (claim the step,
    start the server, re-read the plan) and spent its slices re-listing a
    board whose work was finished.
    """
    from navin.board import session_focus

    touched = session_focus.touched(session_key)
    if not touched:
        return False
    scope = (message_metadata or {}).get("workspace_scope")
    project = scope.get("project_path") if isinstance(scope, Mapping) else None
    if not project:
        return True  # cannot tell: keep the former behaviour
    try:
        from navin.board.store import ProjectBoardStore

        statuses = {task.get("id"): task.get("status") for task in ProjectBoardStore(project).read_tasks()}
    except Exception:
        return True
    return any(
        task_id in statuses and statuses[task_id] not in _CLOSED_TASK_STATUSES
        for task_id in touched
    )


def _increment_continuation_round(
    session_metadata: MutableMapping[str, Any],
    key: str,
) -> int:
    try:
        rounds = int(session_metadata.get(key) or 0)
    except (TypeError, ValueError):
        rounds = 0
    session_metadata[key] = rounds + 1
    return rounds + 1


def _increment_goal_continuation_round(session_metadata: MutableMapping[str, Any]) -> None:
    _increment_continuation_round(session_metadata, _GOAL_CONTINUATION_ROUNDS_KEY)


def _park_board_after_hard_stop(ctx: Any) -> None:
    """Stop the plan spinner when we cannot auto-continue any further."""
    try:
        from navin.board.cancel_focus import park_focused_active_tasks_to_todo_safe

        workspace = None
        request = getattr(ctx, "request_context", None)
        if request is not None:
            workspace = getattr(request, "workspace", None)
        park_focused_active_tasks_to_todo_safe(workspace, getattr(ctx, "session_key", None))
    except Exception:
        logger.debug("park board after hard stop failed", exc_info=True)


def _internal_continuation_metadata(
    message_metadata: Mapping[str, Any] | None,
    *,
    kind: str = _GOAL_CONTINUATION_KIND,
    run_started_at: float | None = None,
) -> dict[str, Any]:
    metadata = dict(message_metadata or {})
    metadata[INTERNAL_CONTINUATION_META] = True
    metadata[INTERNAL_CONTINUATION_KIND_META] = kind
    if run_started_at is not None:
        metadata[INTERNAL_CONTINUATION_RUN_STARTED_AT_META] = float(run_started_at)
    for key in _STRIPPED_INBOUND_META_KEYS:
        metadata.pop(key, None)
    return metadata


def _goal_continuation_prompt(metadata: Mapping[str, Any] | None) -> str:
    lines = goal_state_runtime_lines(metadata)
    if lines:
        goal = "\n".join(lines)
        return (
            "Continue the active sustained goal after the previous turn reached "
            "its tool-call budget.\n\n"
            f"{goal}\n\n"
            "Continue from the saved context. Do not mention the continuation "
            "boundary to the user. Use tools as needed, and call update_goal "
            "with action='complete' when the objective is truly finished."
        )
    return (
        "Continue the active sustained goal after the previous turn reached "
        "its tool-call budget. Continue from the saved context. Do not mention "
        "the continuation boundary to the user. Use tools as needed, and call "
        "update_goal with action='complete' when the objective is truly finished."
    )


# Every N auto-resumed rounds, force a coherence checkpoint: on very long
# builds the per-turn board digest keeps the agent moving, but nothing makes
# it step back and compare the work actually done against the plan. Drift
# compounds silently across dozens of rounds; a periodic full re-read is the
# cheapest correction point.
_BOARD_CHECKPOINT_EVERY = 8
_TURN_CHECKPOINT_EVERY = 6


def _board_continuation_prompt(round_number: int = 0) -> str:
    prompt = (
        "Continue the active build after the previous turn reached its "
        "tool-call budget. Resume the board checklist: claim the current "
        "ready step, implement it, add or adapt tests for its acceptance "
        "criteria, execute them after the last edit with verify/test_run, and move "
        "it done with evidence. For UI apps start the server and call "
        "open_preview when ready. Do not mention the continuation boundary "
        "to the user. If a task lacks acceptance criteria, fill them from "
        "the accepted user request and continue the authorized work without "
        "asking for plan approval again. Keep going until the focused plan "
        "is complete."
    )
    if round_number and round_number % _BOARD_CHECKPOINT_EVERY == 0:
        prompt += (
            f"\n\nCheckpoint (auto-resume round {round_number}): before "
            "resuming, call the board tool to re-read the full plan and the "
            "mission ledger. Compare what was actually built against each "
            "task's acceptance criteria; if a step drifted or grew beyond its "
            "scope, replan or split it now instead of pushing forward. Record "
            "honest evidence on anything you verify, then resume the ready "
            "step."
        )
    return prompt


def _turn_budget_continuation_prompt(round_number: int = 0) -> str:
    prompt = (
        "Continue the unfinished task after the previous turn reached its "
        "tool-call budget. Pick up exactly where you left off - no recap, "
        "no apology, do not restart completed work and do not mention the "
        "continuation boundary to the user. If the remaining work is done, "
        "verify it (tests, verify, preview when relevant) and deliver the "
        "final answer."
    )
    if round_number and round_number % _TURN_CHECKPOINT_EVERY == 0:
        prompt += (
            f"\n\nCheckpoint (auto-resume round {round_number}): this task "
            "has resumed several times. Step back for one moment: restate to "
            "yourself the original request and what remains, confirm you are "
            "not looping on the same failing approach, and if you are, change "
            "strategy or ask the user instead of retrying."
        )
    return prompt


def _strip_terminal_assistant(
    messages: list[dict[str, Any]],
    final_content: str | None,
) -> list[dict[str, Any]]:
    """Drop the synthetic max-iteration assistant message before saving history."""
    if not messages:
        return messages
    last = messages[-1]
    if last.get("role") != "assistant":
        return messages
    if final_content is None or last.get("content") != final_content:
        return messages
    if last.get("tool_calls"):
        return messages
    return messages[:-1]
