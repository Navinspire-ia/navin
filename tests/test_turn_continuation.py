# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A max-iterations stop must never strand a task mid-run.

Regression tests for the "agent freezes at max iterations" hard stop:
- board continuations were silently dropped by the dispatch loop because the
  stale-goal check treated EVERY internal continuation as a goal slice;
- turns without a goal or board had no continuation policy at all, so any
  long task simply died with a "max iterations" message.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from navin.board import session_focus
from navin.bus.events import InboundMessage
from navin.session import turn_continuation as tc
from navin.session.goal_state import GOAL_STATE_KEY


def _msg(content: str = "build the CRM", metadata: dict | None = None) -> InboundMessage:
    return InboundMessage(
        channel="websocket",
        sender_id="user",
        chat_id="chat-1",
        content=content,
        metadata=dict(metadata or {}),
    )


class ContinuationKindTest(unittest.TestCase):
    def tearDown(self) -> None:
        session_focus.clear()

    def test_any_max_iterations_turn_falls_back_to_turn_budget(self) -> None:
        kind = tc._continuation_kind(
            stop_reason="max_iterations",
            pending_queue_available=True,
            session_metadata={},
            session_key="websocket:chat-1",
        )
        self.assertEqual(kind, "turn_budget")

    def test_board_kind_wins_when_this_chat_touched_the_board(self) -> None:
        session_focus.remember("websocket:chat-1", ["task-1"])
        kind = tc._continuation_kind(
            stop_reason="max_iterations",
            pending_queue_available=True,
            session_metadata={},
            session_key="websocket:chat-1",
        )
        self.assertEqual(kind, "session_board")

    def test_other_stop_reasons_never_continue(self) -> None:
        self.assertIsNone(
            tc._continuation_kind(
                stop_reason="completed",
                pending_queue_available=True,
                session_metadata={},
                session_key="websocket:chat-1",
            )
        )

    def test_old_round_counters_never_strand_unfinished_work(self) -> None:
        metadata = {
            tc._TURN_CONTINUATION_ROUNDS_KEY: 2_000,
            tc._BOARD_CONTINUATION_ROUNDS_KEY: 2_000,
            tc._GOAL_CONTINUATION_ROUNDS_KEY: 2_000,
        }
        for kind in ("turn_budget", "session_board", "sustained_goal"):
            with self.subTest(kind=kind):
                if kind == "session_board":
                    session_focus.remember("websocket:chat-1", ["task-1"])
                if kind == "sustained_goal":
                    metadata[GOAL_STATE_KEY] = {
                        "status": "active", "objective": "Translate all 169 locale files",
                    }
                kwargs = {
                    "pending_queue_available": True,
                    "session_metadata": metadata,
                    "session_key": "websocket:chat-1",
                }
                self.assertEqual(
                    tc._continuation_kind(stop_reason="max_iterations", **kwargs), kind,
                )
                self.assertFalse(tc.should_finalize_on_max_iterations(**kwargs))
                self.assertFalse(tc.should_stream_budget_response(
                    stop_reason="max_iterations", **kwargs,
                ))

    def test_new_user_request_resets_checkpoint_counters(self) -> None:
        metadata = {tc._TURN_CONTINUATION_ROUNDS_KEY: 20}
        tc.reset_budget_continuation_rounds(metadata)
        self.assertNotIn(tc._TURN_CONTINUATION_ROUNDS_KEY, metadata)
        self.assertEqual(
            tc._continuation_kind(
                stop_reason="max_iterations",
                pending_queue_available=True,
                session_metadata=metadata,
                session_key="websocket:chat-1",
            ),
            "turn_budget",
        )

    def test_budget_response_is_suppressed_while_continuation_possible(self) -> None:
        self.assertFalse(
            tc.should_stream_budget_response(
                stop_reason="max_iterations",
                pending_queue_available=True,
                session_metadata={},
                session_key="websocket:chat-1",
            )
        )


class CheckpointPromptTest(unittest.TestCase):
    """Long auto-resumed runs periodically force a step-back, not just a push."""

    def test_board_prompt_is_plain_between_checkpoints(self) -> None:
        self.assertNotIn("Checkpoint", tc._board_continuation_prompt(1))
        self.assertNotIn(
            "Checkpoint", tc._board_continuation_prompt(tc._BOARD_CHECKPOINT_EVERY - 1)
        )

    def test_board_prompt_re_anchors_on_the_checkpoint_round(self) -> None:
        prompt = tc._board_continuation_prompt(tc._BOARD_CHECKPOINT_EVERY)
        self.assertIn("Checkpoint", prompt)
        self.assertIn("acceptance criteria", prompt)
        # Every multiple re-anchors again: drift compounds, so must correction.
        self.assertIn(
            "Checkpoint", tc._board_continuation_prompt(tc._BOARD_CHECKPOINT_EVERY * 2)
        )

    def test_turn_budget_prompt_re_anchors_on_the_checkpoint_round(self) -> None:
        self.assertNotIn("Checkpoint", tc._turn_budget_continuation_prompt(1))
        prompt = tc._turn_budget_continuation_prompt(tc._TURN_CHECKPOINT_EVERY)
        self.assertIn("Checkpoint", prompt)

    def test_round_zero_stays_plain_for_legacy_callers(self) -> None:
        self.assertNotIn("Checkpoint", tc._board_continuation_prompt())
        self.assertNotIn("Checkpoint", tc._turn_budget_continuation_prompt())


class GoalContinuationInboundTest(unittest.TestCase):
    def test_only_goal_slices_are_gated_on_goal_state(self) -> None:
        goal_meta = {
            tc.INTERNAL_CONTINUATION_META: True,
            tc.INTERNAL_CONTINUATION_KIND_META: "sustained_goal",
        }
        legacy_meta = {tc.INTERNAL_CONTINUATION_META: True}
        board_meta = {
            tc.INTERNAL_CONTINUATION_META: True,
            tc.INTERNAL_CONTINUATION_KIND_META: "session_board",
        }
        turn_meta = {
            tc.INTERNAL_CONTINUATION_META: True,
            tc.INTERNAL_CONTINUATION_KIND_META: "turn_budget",
        }
        self.assertTrue(tc.is_goal_continuation_inbound(goal_meta))
        self.assertTrue(tc.is_goal_continuation_inbound(legacy_meta))
        # Board / turn-budget slices have no goal: dropping them on "goal
        # inactive" was the mid-build hard stop.
        self.assertFalse(tc.is_goal_continuation_inbound(board_meta))
        self.assertFalse(tc.is_goal_continuation_inbound(turn_meta))
        self.assertFalse(tc.is_goal_continuation_inbound({}))
        self.assertFalse(tc.is_goal_continuation_inbound(None))


class MaybeContinueTurnTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        session_focus.clear()

    def _ctx(self, *, session_metadata: dict | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            msg=_msg(),
            session=SimpleNamespace(metadata=dict(session_metadata or {})),
            session_key="websocket:chat-1",
            pending_queue=asyncio.Queue(),
            stop_reason="max_iterations",
            final_content="I reached the maximum number of tool call iterations",
            all_messages=[{"role": "user", "content": "build the CRM"}],
            suppress_response=False,
            visible_run_started_at=None,
            request_context=None,
        )

    async def test_a_plain_turn_resumes_quietly(self) -> None:
        ctx = self._ctx()
        self.assertTrue(await tc.maybe_continue_turn(ctx))
        self.assertTrue(ctx.suppress_response)
        self.assertEqual(ctx.final_content, "")
        queued = ctx.pending_queue.get_nowait()
        self.assertTrue(tc.internal_continuation_inbound(queued.metadata))
        self.assertEqual(
            queued.metadata[tc.INTERNAL_CONTINUATION_KIND_META], "turn_budget"
        )
        self.assertEqual(ctx.session.metadata[tc._TURN_CONTINUATION_ROUNDS_KEY], 1)

    async def test_board_builds_use_the_board_prompt(self) -> None:
        session_focus.remember("websocket:chat-1", ["task-1"])
        ctx = self._ctx()
        self.assertTrue(await tc.maybe_continue_turn(ctx))
        queued = ctx.pending_queue.get_nowait()
        self.assertEqual(
            queued.metadata[tc.INTERNAL_CONTINUATION_KIND_META], "session_board"
        )
        self.assertIn("board checklist", queued.content)

    async def test_an_audit_continues_after_a_slice_of_reads(self) -> None:
        """Inspecting different locale files is necessary audit work."""
        ctx = self._ctx()
        ctx.tools_used = ["read_file", "grep", "find_files", "read_file"]
        self.assertTrue(await tc.maybe_continue_turn(ctx))
        self.assertTrue(ctx.suppress_response)
        self.assertEqual(ctx.pending_queue.qsize(), 1)
        self.assertFalse(
            tc.should_stream_budget_response(
                stop_reason="max_iterations",
                pending_queue_available=True,
                session_metadata={},
            )
        )

    async def test_a_round_with_an_edit_or_a_command_resumes(self) -> None:
        for progress in (["read_file", "apply_patch"], ["exec"], ["board"], ["git"]):
            session_focus.clear()
            ctx = self._ctx()
            ctx.tools_used = progress
            self.assertTrue(await tc.maybe_continue_turn(ctx), progress)

    async def test_a_long_running_plan_keeps_its_next_slice_and_history(self) -> None:
        session_focus.remember("websocket:chat-1", ["task-1"])
        ctx = self._ctx(
            session_metadata={
                tc._TURN_CONTINUATION_ROUNDS_KEY: 20,
                tc._BOARD_CONTINUATION_ROUNDS_KEY: 40,
            }
        )
        ctx.tools_used = ["apply_patch", "verify"]
        ctx.visible_run_started_at = 123.0
        ctx.all_messages.append({
            "role": "tool", "tool_call_id": "last-edit", "name": "apply_patch",
            "content": "Translated module 103 of 169",
        })
        ctx.all_messages.append({"role": "assistant", "content": ctx.final_content})
        self.assertTrue(await tc.maybe_continue_turn(ctx))
        self.assertTrue(ctx.suppress_response)
        queued = ctx.pending_queue.get_nowait()
        self.assertEqual(ctx.session.metadata[tc._BOARD_CONTINUATION_ROUNDS_KEY], 41)
        self.assertEqual(queued.metadata[tc.INTERNAL_CONTINUATION_RUN_STARTED_AT_META], 123.0)
        self.assertEqual(ctx.all_messages[-1]["tool_call_id"], "last-edit")
        self.assertFalse(tc.should_persist_user_message(queued.metadata))

    async def test_tool_errors_and_completed_work_do_not_resume(self) -> None:
        for reason in ("completed", "tool_error", "error", "no_progress"):
            with self.subTest(reason=reason):
                ctx = self._ctx()
                ctx.stop_reason = reason
                self.assertFalse(await tc.maybe_continue_turn(ctx))
                self.assertTrue(ctx.pending_queue.empty())

    async def test_custom_tools_are_not_mistaken_for_no_progress(self) -> None:
        ctx = self._ctx()
        ctx.tools_used = ["mcp__locales__translate"]
        self.assertTrue(await tc.maybe_continue_turn(ctx))

    async def test_a_final_question_waits_for_the_user(self) -> None:
        """A plan proposal ending in a question must never be auto-resumed.

        Regression: after an audit the agent proposed sprints and asked
        'Tu veux que je commence par lequel ?'; the board continuation kept
        spinning slices that did nothing while the user was expected to answer.
        """
        session_focus.remember("websocket:chat-1", ["task-1"])
        ctx = self._ctx()
        ctx.final_content = (
            "Plan suggéré : Sprint 1 = #2 + #1, Sprint 2 = #3 + #4.\n\n"
            "Tu veux que je commence par lequel ?"
        )
        self.assertFalse(await tc.maybe_continue_turn(ctx))
        self.assertFalse(ctx.suppress_response)
        self.assertTrue(ctx.pending_queue.empty())
        # The question itself is delivered, never swallowed by the budget gate.
        self.assertTrue(
            tc.should_stream_budget_response(
                stop_reason="max_iterations",
                pending_queue_available=True,
                session_metadata={},
                session_key="websocket:chat-1",
                final_content=ctx.final_content,
            )
        )


class EndsWithUserQuestionTest(unittest.TestCase):
    def test_detects_a_trailing_question(self) -> None:
        self.assertTrue(tc.ends_with_user_question("On fait quoi ?"))
        self.assertTrue(tc.ends_with_user_question("Choose:\n1. A\n2. B\nWhich one?"))
        self.assertTrue(tc.ends_with_user_question("**Shall I proceed?**"))

    def test_statements_and_empty_content_do_not_match(self) -> None:
        self.assertFalse(tc.ends_with_user_question("Done. All tests pass."))
        self.assertFalse(tc.ends_with_user_question(""))
        self.assertFalse(tc.ends_with_user_question(None))
        # A question buried mid-message is not the model handing back control.
        self.assertFalse(
            tc.ends_with_user_question("Why did it fail?\nBecause of X. Fixed.")
        )


if __name__ == "__main__":
    unittest.main()
