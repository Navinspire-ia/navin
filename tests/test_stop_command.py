# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""/stop must terminate the whole run: task, queued slices, and sustained goal.

Regression tests for the "stop pretends to stop, then the work resumes" bug:
- an active sustained goal survived /stop and was re-continued by the runner
  on the next turn;
- a cancelled turn re-published its pending-queue leftovers (goal
  continuation slices, injected follow-ups) to the bus, restarting the work.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace

from navin.agent.loop import AgentLoop
from navin.bus.events import InboundMessage
from navin.bus.runtime_events import GoalStateChanged, ensure_runtime_event_publisher
from navin.command.builtin import cmd_stop
from navin.session.goal_state import GOAL_STATE_KEY, sustained_goal_active


class _FakeSessions:
    def __init__(self, metadata: dict | None = None) -> None:
        self.session = SimpleNamespace(metadata=dict(metadata or {}))
        self.saved = 0

    def get_or_create(self, key: str):
        return self.session

    def save(self, session) -> None:
        self.saved += 1


def _msg(content: str = "/stop") -> InboundMessage:
    return InboundMessage(
        channel="websocket", sender_id="user", chat_id="chat-1", content=content,
    )


def _stub_loop(sessions: _FakeSessions) -> SimpleNamespace:
    stub = SimpleNamespace(sessions=sessions)
    stub._runtime_events = lambda: ensure_runtime_event_publisher(stub)
    return stub


class CancelSustainedGoalTest(unittest.IsolatedAsyncioTestCase):
    async def test_active_goal_is_cancelled(self) -> None:
        sessions = _FakeSessions({
            GOAL_STATE_KEY: {"status": "active", "objective": "audit the cluster"},
        })

        cancelled = await AgentLoop._cancel_sustained_goal(
            _stub_loop(sessions), "websocket:chat-1", _msg(),
        )

        self.assertTrue(cancelled)
        goal = sessions.session.metadata[GOAL_STATE_KEY]
        self.assertEqual(goal["status"], "cancelled")
        self.assertEqual(goal["objective"], "audit the cluster")
        self.assertIn("ended_at", goal)
        self.assertEqual(sessions.saved, 1)
        self.assertFalse(sustained_goal_active(sessions.session.metadata))

    async def test_goal_state_event_reaches_subscribers(self) -> None:
        sessions = _FakeSessions({GOAL_STATE_KEY: {"status": "active", "objective": "x"}})
        stub = _stub_loop(sessions)
        events: list[GoalStateChanged] = []
        # Pre-create the publisher so the subscription lands on the same bus.
        ensure_runtime_event_publisher(stub).bus.subscribe(events.append, GoalStateChanged)

        self.assertTrue(
            await AgentLoop._cancel_sustained_goal(stub, "websocket:chat-1", _msg())
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0].session_metadata[GOAL_STATE_KEY]["status"], "cancelled",
        )
        self.assertEqual(events[0].context.chat_id, "chat-1")

    async def test_no_goal_returns_false_without_saving(self) -> None:
        sessions = _FakeSessions()
        self.assertFalse(
            await AgentLoop._cancel_sustained_goal(
                _stub_loop(sessions), "websocket:chat-1", _msg(),
            )
        )
        self.assertEqual(sessions.saved, 0)

    async def test_finished_goal_is_left_untouched(self) -> None:
        sessions = _FakeSessions({GOAL_STATE_KEY: {"status": "completed", "objective": "x"}})
        self.assertFalse(
            await AgentLoop._cancel_sustained_goal(
                _stub_loop(sessions), "websocket:chat-1", _msg(),
            )
        )
        self.assertEqual(sessions.session.metadata[GOAL_STATE_KEY]["status"], "completed")
        self.assertEqual(sessions.saved, 0)


class _FakeStopLoop:
    """Just enough loop surface for cmd_stop."""

    def __init__(self, *, cancelled_tasks: int, goal_cancelled: bool) -> None:
        self._cancelled_tasks = cancelled_tasks
        self._goal_cancelled = goal_cancelled
        self._pending_queues: dict[str, asyncio.Queue] = {}
        self.goal_cancel_calls: list[str] = []

    async def _cancel_active_tasks(self, key: str) -> int:
        return self._cancelled_tasks

    async def _cancel_sustained_goal(self, key: str, msg) -> bool:
        self.goal_cancel_calls.append(key)
        return self._goal_cancelled


def _stop_ctx(loop: _FakeStopLoop) -> SimpleNamespace:
    msg = _msg()
    return SimpleNamespace(loop=loop, msg=msg, key=msg.session_key, raw="/stop", session=None)


class CmdStopTest(unittest.IsolatedAsyncioTestCase):
    async def test_stop_cancels_goal_even_with_running_tasks(self) -> None:
        loop = _FakeStopLoop(cancelled_tasks=2, goal_cancelled=True)
        out = await cmd_stop(_stop_ctx(loop))
        self.assertEqual(loop.goal_cancel_calls, ["websocket:chat-1"])
        self.assertIn("Stopped 2 task(s)", out.content)
        self.assertIn("goal was cancelled", out.content)

    async def test_stop_between_slices_still_cancels_goal(self) -> None:
        # No active asyncio task (continuation slice in flight on the bus),
        # but the goal must still die so nothing resumes it.
        loop = _FakeStopLoop(cancelled_tasks=0, goal_cancelled=True)
        out = await cmd_stop(_stop_ctx(loop))
        self.assertEqual(loop.goal_cancel_calls, ["websocket:chat-1"])
        self.assertEqual(out.content, "Stopped the active goal.")

    async def test_stop_without_anything_active(self) -> None:
        loop = _FakeStopLoop(cancelled_tasks=0, goal_cancelled=False)
        out = await cmd_stop(_stop_ctx(loop))
        self.assertEqual(
            out.content, "Nothing is running - everything is already stopped.",
        )

    async def test_stop_drains_pending_queue(self) -> None:
        loop = _FakeStopLoop(cancelled_tasks=0, goal_cancelled=False)
        queue: asyncio.Queue = asyncio.Queue()
        queue.put_nowait(_msg("queued follow-up"))
        loop._pending_queues["websocket:chat-1"] = queue
        out = await cmd_stop(_stop_ctx(loop))
        self.assertIn("Stopped 1 task(s)", out.content)
        self.assertTrue(queue.empty())
        self.assertNotIn("websocket:chat-1", loop._pending_queues)


class StaleContinuationAfterStopTest(unittest.IsolatedAsyncioTestCase):
    """A /stop that lands between slices must still kill bus continuations.

    Board and turn-budget continuations are republished to the bus at slice
    boundaries; in that window _cancel_active_tasks finds nothing, and before
    this fix the continuation silently restarted the stopped plan.
    """

    @staticmethod
    def _loop_stub() -> SimpleNamespace:
        return SimpleNamespace(
            _stop_requested_at={}, turn_recovery=SimpleNamespace(cancel=lambda key: None),
        )

    @staticmethod
    def _continuation_msg(run_started_at: float | None) -> InboundMessage:
        from navin.session import turn_continuation as tc

        metadata = {
            tc.INTERNAL_CONTINUATION_META: True,
            tc.INTERNAL_CONTINUATION_KIND_META: "session_board",
        }
        if run_started_at is not None:
            metadata[tc.INTERNAL_CONTINUATION_RUN_STARTED_AT_META] = run_started_at
        return InboundMessage(
            channel="websocket", sender_id="system:continuation",
            chat_id="chat-1", content="continue the build", metadata=metadata,
        )

    def test_board_continuation_of_a_stopped_run_is_stale(self) -> None:
        import time

        stub = self._loop_stub()
        run_start = time.time()
        AgentLoop.mark_stop_requested(stub, "websocket:chat-1")
        msg = self._continuation_msg(run_start)
        self.assertTrue(
            AgentLoop._is_stale_continuation(stub, msg, "websocket:chat-1")
        )

    def test_continuation_without_run_start_is_stale_after_stop(self) -> None:
        stub = self._loop_stub()
        AgentLoop.mark_stop_requested(stub, "websocket:chat-1")
        msg = self._continuation_msg(None)
        self.assertTrue(
            AgentLoop._is_stale_continuation(stub, msg, "websocket:chat-1")
        )

    def test_continuation_of_a_newer_run_survives_an_old_stop(self) -> None:
        import time

        stub = self._loop_stub()
        AgentLoop.mark_stop_requested(stub, "websocket:chat-1")
        msg = self._continuation_msg(time.time() + 5.0)
        self.assertFalse(
            AgentLoop._is_stale_continuation(stub, msg, "websocket:chat-1")
        )

    def test_genuine_message_clears_the_stop_marker(self) -> None:
        stub = self._loop_stub()
        AgentLoop.mark_stop_requested(stub, "websocket:chat-1")
        self.assertFalse(
            AgentLoop._is_stale_continuation(stub, _msg("new prompt"), "websocket:chat-1")
        )
        self.assertEqual(stub._stop_requested_at, {})

    def test_stop_without_marker_lets_continuations_through(self) -> None:
        stub = self._loop_stub()
        msg = self._continuation_msg(None)
        self.assertFalse(
            AgentLoop._is_stale_continuation(stub, msg, "websocket:chat-1")
        )

    async def test_cmd_stop_sets_the_marker(self) -> None:
        loop = _FakeStopLoop(cancelled_tasks=0, goal_cancelled=False)
        marked: list[str] = []
        loop.mark_stop_requested = marked.append
        await cmd_stop(_stop_ctx(loop))
        self.assertEqual(marked, ["websocket:chat-1"])


class StopCancelsScopedPlanTest(unittest.IsolatedAsyncioTestCase):
    """/stop must cancel plan steps on the board of the chat's own project.

    The board lives under the session's scoped workspace; resolving the
    gateway default workspace instead is why /stop answered "No active task
    to stop." while the plan panel kept its spinner.
    """

    def setUp(self) -> None:
        import tempfile

        from navin.board import session_focus

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.project = root / "project"
        self.project.mkdir()
        self.default_workspace = root / "default-workspace"
        self.default_workspace.mkdir()
        self.webui_dir = root / "webui"
        with session_focus._guard:
            session_focus._focus.clear()
            session_focus._loaded = False
        self.addCleanup(self._reset_focus)

    @staticmethod
    def _reset_focus() -> None:
        from navin.board import session_focus

        with session_focus._guard:
            session_focus._focus.clear()
            session_focus._loaded = False

    def _seed_active_plan_step(self, key: str) -> str:
        from navin.board import session_focus
        from navin.board.store import ProjectBoardStore

        store = ProjectBoardStore(self.project)
        task = store.create_task(
            title="Fix: workspace-wide diagnostics",
            actor="agent",
            actor_type="agent",
            status="in_progress",
        )
        session_focus.remember(key, [task["id"]])
        return task["id"]

    def _ctx(self, loop) -> SimpleNamespace:
        msg = _msg()
        return SimpleNamespace(
            loop=loop,
            msg=msg,
            key=msg.session_key,
            raw="/stop",
            session=SimpleNamespace(metadata={}),
        )

    async def test_plan_steps_die_in_the_scoped_project(self) -> None:
        from unittest.mock import patch

        from navin.board.store import ProjectBoardStore

        loop = _FakeStopLoop(cancelled_tasks=0, goal_cancelled=False)
        loop.workspace = self.default_workspace
        loop.workspace_scopes = SimpleNamespace(
            for_message=lambda msg, meta: SimpleNamespace(
                project_path=self.project,
            ),
        )
        with patch("navin.config.paths.get_webui_dir", return_value=self.webui_dir):
            task_id = self._seed_active_plan_step("websocket:chat-1")
            out = await cmd_stop(self._ctx(loop))

        self.assertIn("Stopped the running plan.", out.content)
        tasks = {t["id"]: t for t in ProjectBoardStore(self.project).read_tasks()}
        self.assertEqual(tasks[task_id]["status"], "cancelled")

    async def test_without_scope_resolver_falls_back_to_loop_workspace(self) -> None:
        from unittest.mock import patch

        from navin.board.store import ProjectBoardStore

        loop = _FakeStopLoop(cancelled_tasks=0, goal_cancelled=False)
        loop.workspace = self.project  # board lives in the default workspace
        with patch("navin.config.paths.get_webui_dir", return_value=self.webui_dir):
            task_id = self._seed_active_plan_step("websocket:chat-1")
            out = await cmd_stop(self._ctx(loop))

        self.assertIn("plan step(s) cancelled", out.content)
        tasks = {t["id"]: t for t in ProjectBoardStore(self.project).read_tasks()}
        self.assertEqual(tasks[task_id]["status"], "cancelled")


class _FakeBus:
    def __init__(self) -> None:
        self.inbound: list[InboundMessage] = []
        self.outbound: list = []

    async def publish_inbound(self, msg: InboundMessage) -> None:
        self.inbound.append(msg)

    async def publish_outbound(self, msg) -> None:
        self.outbound.append(msg)


def _dispatch_stub(bus: _FakeBus, process_message) -> SimpleNamespace:
    """Minimal AgentLoop stand-in for exercising the real _dispatch."""
    stub = SimpleNamespace(
        bus=bus,
        sessions=_FakeSessions(),
        _session_locks={},
        _concurrency_gate=None,
        _pending_queues={},
        _automation_turn_coordinators=[],
        _process_message=process_message,
        _restore_runtime_checkpoint=lambda session: False,
        _clear_pending_user_turn=lambda session: None,
        turn_recovery=SimpleNamespace(
            matches=lambda msg, key: True, waiting=lambda key: False,
        ),
    )
    stub._effective_session_key = lambda msg: msg.session_key
    stub._runtime_events = lambda: ensure_runtime_event_publisher(stub)

    async def _noop_deferred(session_key: str) -> None:
        return None

    stub._publish_next_deferred_automation_turn = _noop_deferred
    return stub


class DispatchCancellationTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_turn_drops_pending_leftovers(self) -> None:
        bus = _FakeBus()
        started = asyncio.Event()

        async def fake_process(msg, on_stream=None, on_stream_end=None, pending_queue=None):
            pending_queue.put_nowait(_msg("continuation slice"))
            started.set()
            await asyncio.Event().wait()  # block until cancelled

        stub = _dispatch_stub(bus, fake_process)
        task = asyncio.create_task(AgentLoop._dispatch(stub, _msg("do the audit")))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(bus.inbound, [])  # nothing re-published: the run truly stops
        self.assertEqual(stub._pending_queues, {})

    async def test_normal_turn_still_republishes_leftovers(self) -> None:
        bus = _FakeBus()

        async def fake_process(msg, on_stream=None, on_stream_end=None, pending_queue=None):
            pending_queue.put_nowait(_msg("continuation slice"))
            return None

        stub = _dispatch_stub(bus, fake_process)
        await AgentLoop._dispatch(stub, _msg("do the audit"))

        self.assertEqual(len(bus.inbound), 1)
        self.assertEqual(bus.inbound[0].content, "continuation slice")
