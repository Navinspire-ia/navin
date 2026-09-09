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

    def test_round_cap_only_bounds_a_single_prompt(self) -> None:
        metadata = {tc._TURN_CONTINUATION_ROUNDS_KEY: tc._MAX_TURN_CONTINUATION_ROUNDS}
        self.assertIsNone(
            tc._continuation_kind(
                stop_reason="max_iterations",
                pending_queue_available=True,
                session_metadata=metadata,
                session_key="websocket:chat-1",
            )
        )
        # A fresh user message resets the budget: autonomy resumes.
        tc.reset_budget_continuation_rounds(metadata)
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

    async def test_a_round_that_changed_nothing_is_not_resumed(self) -> None:
        """Twenty-four steps of reading and searching is circling, not work.

        Re-queuing such a round would run the same reads again, and the round
        caps would let that repeat for hours. The transcript is delivered.
        """
        ctx = self._ctx()
        ctx.tools_used = ["read_file", "grep", "find_files", "read_file"]
        self.assertFalse(await tc.maybe_continue_turn(ctx))
        self.assertFalse(ctx.suppress_response)
        self.assertTrue(ctx.pending_queue.empty())
        self.assertTrue(
            tc.should_stream_budget_response(
                stop_reason="max_iterations",
                pending_queue_available=True,
                session_metadata={},
                tools_used=ctx.tools_used,
            )
        )

    async def test_a_round_with_an_edit_or_a_command_resumes(self) -> None:
        for progress in (["read_file", "apply_patch"], ["exec"], ["board"], ["git"]):
            session_focus.clear()
            ctx = self._ctx()
            ctx.tools_used = progress
            self.assertTrue(await tc.maybe_continue_turn(ctx), progress)

    def test_untracked_tool_use_is_not_held_against_the_turn(self) -> None:
        self.assertTrue(tc.turn_made_progress(None))
        self.assertFalse(tc.turn_made_progress([]))

    async def test_exhausted_rounds_stop_the_chain(self) -> None:
        ctx = self._ctx(
            session_metadata={
                tc._TURN_CONTINUATION_ROUNDS_KEY: tc._MAX_TURN_CONTINUATION_ROUNDS,
            }
        )
        self.assertFalse(await tc.maybe_continue_turn(ctx))
        self.assertFalse(ctx.suppress_response)

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
