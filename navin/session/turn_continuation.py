# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Internal turn continuation helpers.

This module keeps budget-boundary continuation policy out of ``AgentLoop``.
The loop calls a small set of helpers; those helpers decide whether an internal
continuation is allowed and, when it is, queue the next turn directly.
"""

from __future__ import annotations

import dataclasses
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
        if ctx.stop_reason == "max_iterations":
            _park_board_after_hard_stop(ctx)
        return False
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
    metadata = _internal_continuation_metadata(
        ctx.msg.metadata,
        kind=kind,
        run_started_at=getattr(ctx, "visible_run_started_at", None),
    )
    if kind == _GOAL_CONTINUATION_KIND:
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
        content = _turn_budget_continuation_prompt(rounds)

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
    if stop_reason != "max_iterations" or not pending_queue_available:
        return None
    if _goal_continuation_available(
        session_metadata,
        message_metadata=message_metadata,
    ):
        return _GOAL_CONTINUATION_KIND
    if _board_continuation_available(session_key=session_key):
        return _BOARD_CONTINUATION_KIND
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


def _board_continuation_available(
    *,
    session_key: str | None = None,
) -> bool:
    """True when this chat still has focused board work mid-build."""
    from navin.board import session_focus

    return session_focus.touched(session_key)


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
