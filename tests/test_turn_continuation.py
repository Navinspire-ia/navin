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
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from navin.agent.loop import AgentLoop
from navin.board import session_focus
from navin.bus.events import InboundMessage
from navin.bus.queue import MessageBus
from navin.providers.base import LLMProvider
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

    def test_resume_prompts_put_a_queued_user_message_first(self) -> None:
        # A prompt queued during the previous slice lands in history with no
        # reply; the resume prompt must not steer the agent past it.
        for prompt in (
            tc._board_continuation_prompt(),
            tc._turn_budget_continuation_prompt(),
            tc._goal_continuation_prompt(None),
        ):
            self.assertIn("user wrote a message after your last reply", prompt)


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

    async def test_a_blank_final_answer_resumes_instead_of_giving_up(self) -> None:
        """The 'couldn't produce a final answer' stop resumes, bounded."""
        ctx = self._ctx()
        ctx.stop_reason = "empty_final_response"
        ctx.final_content = "I completed the tool steps but couldn't produce a final answer."
        self.assertTrue(await tc.maybe_continue_turn(ctx))
        self.assertTrue(ctx.suppress_response)
        self.assertEqual(ctx.session.metadata[tc._EMPTY_FINAL_ROUNDS_KEY], 1)
        queued = ctx.pending_queue.get_nowait()
        # The resume says what happened (a blank reply), not "tool budget".
        self.assertIn("previous reply was empty", queued.content)
        self.assertNotIn("tool-call budget", queued.content)
        self.assertIn("not a system prompt", queued.content)
        ctx.session.metadata[tc._EMPTY_FINAL_ROUNDS_KEY] = tc._EMPTY_FINAL_ROUNDS_LIMIT
        ctx.suppress_response = False
        self.assertFalse(await tc.maybe_continue_turn(ctx))
        self.assertFalse(ctx.suppress_response)
        tc.reset_budget_continuation_rounds(ctx.session.metadata)
        self.assertNotIn(tc._EMPTY_FINAL_ROUNDS_KEY, ctx.session.metadata)

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

    async def test_a_no_progress_stop_resumes_while_the_plan_is_open(self) -> None:
        """Reminders ignored mid-plan end the slice, never the plan.

        Seen live: the agent was reading the files a board step needed, the
        exploration reminders counted that as looking around, the turn was
        forced to answer without tools, and the user had to type "continue"
        with steps still open on the board.
        """
        session_focus.remember("websocket:chat-1", ["task-1"])
        ctx = self._ctx()
        ctx.stop_reason = "no_progress"
        ctx.final_content = "I read the files; the remaining steps are still to do."
        ctx.all_messages.append({"role": "assistant", "content": ctx.final_content})
        self.assertTrue(await tc.maybe_continue_turn(ctx))
        self.assertTrue(ctx.suppress_response)
        self.assertEqual(ctx.final_content, "")
        queued = ctx.pending_queue.get_nowait()
        self.assertEqual(queued.metadata[tc.INTERNAL_CONTINUATION_KIND_META], "session_board")
        # The resume says what happened (no action), not "tool budget".
        self.assertIn("without acting", queued.content)
        self.assertIn("board checklist", queued.content)
        # The forced answer is dropped: the next slice continues the work.
        self.assertEqual(ctx.all_messages[-1]["role"], "user")

    async def test_a_no_progress_stop_with_a_goal_resumes_the_goal(self) -> None:
        """An active sustained goal is accepted work too: it resumes, as a goal slice."""
        ctx = self._ctx(session_metadata={GOAL_STATE_KEY: {"status": "active", "objective": "ship it"}})
        ctx.stop_reason = "no_progress"
        self.assertTrue(await tc.maybe_continue_turn(ctx))
        self.assertTrue(ctx.suppress_response)
        queued = ctx.pending_queue.get_nowait()
        self.assertEqual(queued.metadata[tc.INTERNAL_CONTINUATION_KIND_META], "sustained_goal")
        self.assertIn("without acting", queued.content)
        self.assertIn("sustained goal", queued.content)

    async def test_a_no_progress_stop_without_a_plan_answers(self) -> None:
        """No board step, no goal: the forced answer is the end of the turn."""
        ctx = self._ctx()
        ctx.stop_reason = "no_progress"
        self.assertFalse(await tc.maybe_continue_turn(ctx))
        self.assertFalse(ctx.suppress_response)
        self.assertTrue(ctx.pending_queue.empty())
        self.assertIsNone(
            tc._continuation_kind(
                stop_reason="no_progress",
                pending_queue_available=True,
                session_metadata={},
                session_key="websocket:chat-1",
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


class SliceProgressTest(unittest.IsolatedAsyncioTestCase):
    """What counts as progress decides between resuming a task and calling it stuck."""

    @staticmethod
    def _slice(*calls: tuple[str, dict, str]) -> list[dict]:
        rows: list[dict] = []
        for n, (name, args, result) in enumerate(calls):
            rows.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": f"c{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}},
            ]})
            rows.append({"role": "tool", "tool_call_id": f"c{n}", "name": name, "content": result})
        return rows

    def test_the_same_board_move_again_is_not_progress(self) -> None:
        # Moving the same task back and forth used to count as progress at
        # every slice, so a circling run was resumed without end.
        state: dict = {tc._EMPTY_FINAL_ROUNDS_KEY: 2}
        move = ("board", {"action": "move", "task_id": "t-1", "status": "in_progress"}, "Task moved: t-1")
        report, _ = tc.record_slice(state, self._slice(move), 0)
        self.assertTrue(report.progressed)
        # Real progress ends the run of blank replies counted so far.
        self.assertNotIn(tc._EMPTY_FINAL_ROUNDS_KEY, state)
        report, _ = tc.record_slice(state, self._slice(move), 0)
        self.assertFalse(report.progressed)

    def test_a_new_green_check_is_progress_and_its_rerun_is_not(self) -> None:
        # A QA slice of green checks was taken for a slice that did nothing;
        # and a rerun differing only by its duration was taken for new work.
        state: dict = {}
        first = ("test_run", {"target": "tests/test_a.py"}, "pytest: 12 passed (3210 ms) in 3.21s")
        report, _ = tc.record_slice(state, self._slice(first), 0)
        self.assertTrue(report.progressed)
        again = ("test_run", {"target": "tests/test_a.py"}, "pytest: 12 passed (3402 ms) in 3.40s")
        report, _ = tc.record_slice(state, self._slice(again), 0)
        self.assertFalse(report.progressed)

    async def test_the_stuck_report_is_delivered_once_then_work_can_resume(self) -> None:
        # The flag of the stuck report used to stay set until the user's next
        # message: no background result could resume the plan after it.
        meta = {tc._STALL_REPORT_SENT_KEY: True, tc._STALLED_SLICES_KEY: 3, tc._STALLED_CALLS_KEY: 20}
        ctx = SimpleNamespace(
            session=SimpleNamespace(key="cli:stuck", metadata=meta),
            pending_queue=asyncio.Queue(),
            stop_reason="completed",
            msg=InboundMessage(channel="cli", sender_id="user", chat_id="stuck", content="go"),
            session_key="cli:stuck",
            final_content="I am stuck on the build.",
            all_messages=[],
            initial_messages=[],
            request_context=None,
            keep_plan_active=True,
        )
        self.assertFalse(await tc.maybe_continue_turn(ctx))
        self.assertEqual(meta, {})


class _QuietProvider(LLMProvider):
    """Never called: the agent run itself is stubbed in these tests."""

    def get_default_model(self) -> str:
        return "test-system-turn"

    async def chat(self, **kwargs):  # pragma: no cover - a call would be a test bug
        raise AssertionError("the model must not be called here")


class SystemTurnContinuationTest(unittest.IsolatedAsyncioTestCase):
    """A turn woken by a background result keeps working past its step budget.

    Seen live (2026-11-07): after a background command finished, the turn that
    read its result ran fourteen steps, reached the budget, and stopped there
    with no answer and no resume. The user saw the agent halt "after a few
    operations" and had to type "continue" after every finished command.
    """

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.loop = AgentLoop(provider=_QuietProvider(), workspace=Path(folder.name), bus=MessageBus())
        self.loop._mcp_servers = {}
        self.key = "websocket:system-turn-chat"
        self.queue: asyncio.Queue = asyncio.Queue()

    def _finished_command(self) -> InboundMessage:
        return InboundMessage(
            channel="system",
            sender_id="exec",
            chat_id=self.key,
            content="[Background command finished] session abc succeeded (exit code 0) after 9s.",
            session_key_override=self.key,
            metadata={"injected_event": "exec_finished", "exec_session_id": "abc"},
        )

    async def _run(self, msg: InboundMessage, stop_reason: str, final: str | None):
        async def run_agent_loop(messages, **kwargs):
            done = [*messages, {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "exec", "arguments": "{}"}},
            ]}, {"role": "tool", "tool_call_id": "c1", "name": "exec", "content": "Exit code: 0"}]
            if final:
                done.append({"role": "assistant", "content": final})
            return final, ["exec"], done, stop_reason, False

        with patch.object(self.loop, "_run_agent_loop", run_agent_loop):
            return await self.loop._process_system_message(
                msg, runtime=self.loop.runtime_for_inbound(msg),
                session_key=self.key, pending_queue=self.queue,
            )

    async def test_a_budget_stop_after_a_finished_command_queues_the_next_slice(self) -> None:
        msg = self._finished_command()
        response = await self._run(msg, "max_iterations", None)
        # No "Background task completed." placeholder: the next slice answers.
        self.assertIsNone(response)
        self.assertTrue(tc.internal_continuation_pending(msg.metadata))
        queued = self.queue.get_nowait()
        self.assertTrue(tc.internal_continuation_inbound(queued.metadata))
        self.assertEqual(queued.metadata[tc.INTERNAL_CONTINUATION_KIND_META], "turn_budget")
        # The next slice is a plain slice of the chat, not the command event again.
        self.assertNotIn("injected_event", queued.metadata)
        self.assertNotIn("exec_session_id", queued.metadata)
        # Nor a replay of a recovered request: carried on, its token dropped
        # the slice in silence once another record was written.
        from navin.session.turn_recovery import RECOVERY_ID_META

        self.assertIn(RECOVERY_ID_META, tc._STRIPPED_INBOUND_META_KEYS)
        self.assertEqual((queued.channel, queued.chat_id), ("websocket", "system-turn-chat"))
        self.assertEqual(queued.session_key_override, self.key)
        self.assertIn("Continue the unfinished task", queued.content)
        # The work of the slice is kept in the history for the next slice.
        history = self.loop.sessions.get_or_create(self.key).messages
        self.assertTrue(any(row.get("role") == "tool" for row in history))

    async def test_a_finished_system_turn_still_answers(self) -> None:
        msg = self._finished_command()
        response = await self._run(msg, "completed", "The build is green.")
        self.assertIsNotNone(response)
        self.assertEqual(response.content, "The build is green.")
        self.assertTrue(self.queue.empty())
        self.assertFalse(tc.internal_continuation_pending(msg.metadata))

    async def test_no_slice_takes_over_a_request_waiting_for_its_retry(self) -> None:
        # The user's request hit a provider error and waits for its retry: a
        # slice queued here would adopt its record, and the request would
        # never be retried.
        msg = self._finished_command()
        with patch.object(self.loop.turn_recovery, "waiting", return_value=True):
            await self._run(msg, "max_iterations", None)
        self.assertTrue(self.queue.empty())
        self.assertFalse(tc.internal_continuation_pending(msg.metadata))


if __name__ == "__main__":
    unittest.main()
