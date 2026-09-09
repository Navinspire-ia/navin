# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Subagents must actually fan out, and nothing they produce may vanish quietly.

The machinery was already concurrent - asyncio tasks, no semaphore - but the
default limit of one made it behave as if it were not, and none of this path had
a single test. These cover the three ways the parallel story used to break: a
second spawn refused, one conversation's subagent blocking another's, and a
result that arrived but never reached the model.
"""

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from navin.agent.runner import AgentRunResult
from navin.agent.subagent import SubagentManager
from navin.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from navin.agent.tools.spawn import SpawnTool


class _Bus:
    """Collects the announcements a real bus would deliver to the parent."""

    def __init__(self) -> None:
        self.published: list = []

    async def publish_inbound(self, msg) -> None:
        self.published.append(msg)


class _GatedRunner:
    """A runner that parks every subagent until the test releases it.

    Concurrency is only observable while more than one subagent is mid-flight, so
    the runs have to be held open deliberately rather than allowed to finish.
    """

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.started = 0
        self.peak_concurrent = 0
        self._in_flight = 0

    async def run(self, spec) -> AgentRunResult:
        self.started += 1
        self._in_flight += 1
        self.peak_concurrent = max(self.peak_concurrent, self._in_flight)
        try:
            await self.gate.wait()
        finally:
            self._in_flight -= 1
        return AgentRunResult(final_content="done", messages=[])


class _CountingRunner:
    """A runner that completes on its own, tracking width and total.

    The gated runner cannot answer the question a wave asks - whether every
    one of them eventually ran - because it holds them all open by design.
    """

    def __init__(self) -> None:
        self.finished = 0
        self.peak_concurrent = 0
        self._in_flight = 0

    async def run(self, spec) -> AgentRunResult:
        self._in_flight += 1
        self.peak_concurrent = max(self.peak_concurrent, self._in_flight)
        try:
            await asyncio.sleep(0)
        finally:
            self._in_flight -= 1
            self.finished += 1
        return AgentRunResult(final_content="done", messages=[])


def _manager(
    workspace: Path,
    bus: _Bus,
    runner: _GatedRunner | _CountingRunner,
    record_edits=None,
) -> SubagentManager:
    manager = SubagentManager(
        workspace=workspace,
        bus=bus,
        max_tool_result_chars=4000,
        record_edits=record_edits,
    )
    manager.runner = runner
    return manager


class ParallelExecutionTest(unittest.IsolatedAsyncioTestCase):
    """The manager itself imposes no limit; several subagents run at once."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        self.bus = _Bus()
        self.runner = _GatedRunner()
        self.manager = _manager(self.workspace, self.bus, self.runner)

    async def _spawn(self, task: str, session_key: str = "cli:local") -> str:
        return await self.manager.spawn(
            task=task,
            runtime=mock.Mock(with_generation_overrides=lambda **_: mock.Mock()),
            origin_channel="cli",
            origin_chat_id="local",
            session_key=session_key,
        )

    async def _settle(self, expected_started: int = 1) -> None:
        """Wait until the spawned tasks actually reach the runner.

        Prompt building now runs in a thread (asyncio.to_thread), so plain
        event-loop yields are not enough: the wait has to be a real one.
        """
        deadline = asyncio.get_running_loop().time() + 15.0
        while self.runner.started < expected_started:
            if asyncio.get_running_loop().time() > deadline:
                self.fail(
                    f"only {self.runner.started}/{expected_started} subagent(s) "
                    "reached the runner within 15s"
                )
            await asyncio.sleep(0.01)

    async def test_three_subagents_are_in_flight_at_once(self) -> None:
        for index in range(3):
            await self._spawn(f"task {index}")
        await self._settle(expected_started=3)
        self.assertEqual(self.manager.get_running_count(), 3)
        self.assertEqual(self.runner.peak_concurrent, 3)
        self.runner.gate.set()
        await asyncio.gather(*list(self.manager._running_tasks.values()))

    async def test_spawn_returns_before_the_work_finishes(self) -> None:
        """A blocking spawn would make fan-out impossible however high the limit."""
        reply = await self._spawn("slow task")
        self.assertIn("started", reply)
        await self._settle()
        self.assertEqual(self.manager.get_running_count(), 1)
        self.runner.gate.set()
        await asyncio.gather(*list(self.manager._running_tasks.values()))

    async def test_running_counts_are_per_session(self) -> None:
        await self._spawn("a", session_key="cli:one")
        await self._spawn("b", session_key="cli:two")
        await self._settle(expected_started=2)
        self.assertEqual(self.manager.get_running_count_by_session("cli:one"), 1)
        self.assertEqual(self.manager.get_running_count_by_session("cli:two"), 1)
        self.assertEqual(self.manager.get_running_count(), 2)
        self.runner.gate.set()
        await asyncio.gather(*list(self.manager._running_tasks.values()))


class SpawnQueueTest(unittest.IsolatedAsyncioTestCase):
    """Past the limit a spawn waits its turn instead of being thrown away.

    Refusing was the old behaviour, and it made a busy conversation a dead end:
    the model was told no, had nothing to retry against, and the work simply
    never happened.
    """

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bus = _Bus()
        self.runner = _GatedRunner()
        self.manager = _manager(Path(self._tmp.name), self.bus, self.runner)
        self.manager.max_concurrent_subagents = 2

    async def asyncTearDown(self) -> None:
        # _release() used to gather only the tasks already running. A queued
        # spawn started in the done-callback then kept writing into the temp
        # workspace after the test returned, so cleanup hit "Directory not empty".
        watchdog = self.manager._watchdog_task
        if watchdog is not None and not watchdog.done():
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)
        self.manager._queued.clear()
        leftover = list(self.manager._running_tasks.values())
        for task in leftover:
            task.cancel()
        if leftover:
            await asyncio.gather(*leftover, return_exceptions=True)

    async def _spawn(self, task: str, session_key: str = "cli:local") -> str:
        return await self.manager.spawn(
            task=task,
            runtime=mock.Mock(with_generation_overrides=lambda **_: mock.Mock()),
            origin_channel="cli",
            origin_chat_id="local",
            session_key=session_key,
        )

    async def _wait_started(self, expected: int) -> None:
        deadline = asyncio.get_running_loop().time() + 15.0
        while self.runner.started < expected:
            if asyncio.get_running_loop().time() > deadline:
                self.fail(f"only {self.runner.started}/{expected} subagent(s) started")
            await asyncio.sleep(0.01)

    async def _fill(self, session_key: str = "cli:local") -> None:
        await self._spawn("a", session_key=session_key)
        await self._spawn("b", session_key=session_key)
        await self._wait_started(2)

    async def _release(self) -> None:
        """Finish every in-flight spawn, including ones the drain starts next."""
        self.runner.gate.set()
        deadline = asyncio.get_running_loop().time() + 15.0
        while self.manager.get_running_count() > 0:
            if asyncio.get_running_loop().time() > deadline:
                self.fail("subagents still in flight after release")
            tasks = list(self.manager._running_tasks.values())
            if tasks:
                await asyncio.gather(*tasks)
            else:
                await asyncio.sleep(0)

    async def test_a_spawn_past_the_limit_is_queued_not_refused(self) -> None:
        await self._fill()
        reply = await self._spawn("c")
        self.assertIn("queued", reply.lower())
        self.assertEqual(self.manager.queued_count("cli:local"), 1)
        self.assertEqual(self.runner.started, 2)
        await self._release()

    async def test_a_queued_spawn_starts_when_a_slot_frees_up(self) -> None:
        await self._fill()
        await self._spawn("c")
        self.runner.gate.set()
        await self._wait_started(3)
        self.assertEqual(self.manager.queued_count("cli:local"), 0)

    async def test_queued_work_counts_as_in_flight(self) -> None:
        """The turn loop stays alive on this count; ending early strands it."""
        await self._fill()
        await self._spawn("c")
        self.assertEqual(self.manager.get_running_count_by_session("cli:local"), 3)
        await self._release()

    async def test_a_queued_spawn_is_visible_to_a_reconnecting_client(self) -> None:
        await self._fill()
        await self._spawn("c")
        phases = [card["phase"] for card in self.manager.running_snapshot("cli:local")]
        self.assertEqual(phases.count("queued"), 1)
        self.assertEqual(len(phases), 3)
        await self._release()

    async def test_cancelling_a_session_drops_its_queue_too(self) -> None:
        """A slot freed by the cancellation must not start the cancelled work."""
        await self._fill()
        await self._spawn("c")
        cancelled = await self.manager.cancel_by_session("cli:local")
        self.assertEqual(cancelled, 3)
        await asyncio.sleep(0.05)
        self.assertEqual(self.manager.queued_count("cli:local"), 0)
        self.assertEqual(self.runner.started, 2)

    async def test_one_session_queue_does_not_hold_up_another(self) -> None:
        await self._fill(session_key="cli:one")
        queued = await self._spawn("c", session_key="cli:one")
        started = await self._spawn("d", session_key="cli:two")
        self.assertIn("queued", queued.lower())
        self.assertIn("started", started.lower())
        await self._release()

    async def test_the_queue_still_has_a_wall(self) -> None:
        """Refusing was the backpressure; queuing has to keep some of it.

        Without a wall, a model looping on spawn would enqueue until the
        process ran out of memory, each entry holding a runtime and a scope.
        """
        self.manager.max_queued_subagents = 3
        await self._fill()
        for _ in range(3):
            self.assertIn("queued", (await self._spawn("more")).lower())
        refused = await self._spawn("one too many")
        self.assertIn("Cannot spawn", refused)
        self.assertEqual(self.manager.queued_count("cli:local"), 3)
        # A refused spawn must leave nothing behind to leak or to display.
        self.assertEqual(len(self.manager._task_statuses), 5)
        await self._release()


class HundredsOfAgentsTest(unittest.IsolatedAsyncioTestCase):
    """A wave of hundreds has to run, drain and report back in full.

    The point of the whole exercise: not that a spawn is accepted, but that
    every single one eventually runs and its verdict is still retrievable
    afterwards. A wave that half-completes is worse than one that was refused.
    """

    WAVE = 300

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        store = Path(self._tmp.name) / "outcomes"
        store.mkdir()
        patcher = mock.patch(
            "navin.agent.subagent._outcomes_dir", return_value=store
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bus = _Bus()
        self.runner = _CountingRunner()
        self.manager = _manager(Path(self._tmp.name), self.bus, self.runner)
        self.manager.max_concurrent_subagents = 50

    async def _drain(self) -> None:
        deadline = asyncio.get_running_loop().time() + 60.0
        while self.manager.get_running_count() > 0:
            if asyncio.get_running_loop().time() > deadline:
                self.fail(
                    f"{self.manager.get_running_count()} subagent(s) still in "
                    f"flight after 60s ({self.runner.finished} finished)"
                )
            await asyncio.sleep(0.01)

    async def test_a_wave_of_three_hundred_all_run_and_all_report(self) -> None:
        for index in range(self.WAVE):
            reply = await self.manager.spawn(
                task=f"task {index}",
                runtime=mock.Mock(with_generation_overrides=lambda **_: mock.Mock()),
                origin_channel="cli",
                origin_chat_id="local",
                session_key="cli:local",
            )
            self.assertNotIn("Cannot spawn", reply)

        await self._drain()

        # Every one ran, none was dropped on the floor by the queue.
        self.assertEqual(self.runner.finished, self.WAVE)
        self.assertEqual(self.manager.queued_count("cli:local"), 0)
        self.assertEqual(self.manager.get_running_count(), 0)
        # Every one announced its result to the parent.
        self.assertEqual(len(self.bus.published), self.WAVE)
        # And the verdicts survive the statuses being dropped, which is what
        # spawn(action="results") reads when a turn could not carry them all.
        self.assertEqual(len(self.manager.recent_outcomes(session_key="cli:local")), self.WAVE)

    async def test_the_limit_is_never_exceeded_along_the_way(self) -> None:
        """Queuing must not be a way around the number the governor set."""
        for index in range(self.WAVE):
            await self.manager.spawn(
                task=f"task {index}",
                runtime=mock.Mock(with_generation_overrides=lambda **_: mock.Mock()),
                origin_channel="cli",
                origin_chat_id="local",
                session_key="cli:local",
            )
        await self._drain()
        self.assertLessEqual(self.runner.peak_concurrent, 50)
        self.assertGreater(self.runner.peak_concurrent, 1)


class PrepareNeverBlocksTest(unittest.IsolatedAsyncioTestCase):
    """A hung catalog/context pack must not pin a slot forever."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bus = _Bus()
        self.runner = _CountingRunner()
        self.manager = _manager(Path(self._tmp.name), self.bus, self.runner)
        self.manager._prepare_timeout_s = 0.05

    async def test_a_stuck_prompt_still_reaches_the_runner(self) -> None:
        def hang(*_args, **_kwargs) -> str:
            time.sleep(0.3)
            return "should-not-be-used"

        with mock.patch.object(SubagentManager, "_build_subagent_prompt", side_effect=hang):
            reply = await self.manager.spawn(
                task="do the thing",
                runtime=mock.Mock(with_generation_overrides=lambda **_: mock.Mock()),
                origin_channel="cli",
                origin_chat_id="local",
                session_key="cli:local",
            )
            self.assertNotIn("Cannot spawn", reply)
            deadline = asyncio.get_running_loop().time() + 3.0
            while self.runner.finished < 1:
                if asyncio.get_running_loop().time() > deadline:
                    self.fail(
                        f"stuck prepare never reached the runner "
                        f"({self.runner.finished} finished)"
                    )
                await asyncio.sleep(0.01)

        self.assertEqual(self.runner.finished, 1)
        self.assertEqual(self.manager.get_running_count(), 0)


class CancelAlwaysAnnouncesTest(unittest.IsolatedAsyncioTestCase):
    """A cancelled subagent must still tell the parent, or the turn waits forever.

    /stop and a drained session used to cancel the task and re-raise without
    publishing. The orchestrator then sat on the pending queue until the 300s
    timeout, with nothing on screen saying the work was dead.
    """

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        store = Path(self._tmp.name) / "outcomes"
        store.mkdir()
        patcher = mock.patch(
            "navin.agent.subagent._outcomes_dir", return_value=store
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bus = _Bus()
        self.runner = _GatedRunner()
        self.manager = _manager(Path(self._tmp.name), self.bus, self.runner)

    async def test_cancel_publishes_a_result_the_parent_can_drain(self) -> None:
        await self.manager.spawn(
            task="long job",
            runtime=mock.Mock(with_generation_overrides=lambda **_: mock.Mock()),
            origin_channel="cli",
            origin_chat_id="local",
            session_key="cli:local",
        )
        deadline = asyncio.get_running_loop().time() + 5.0
        while self.runner.started < 1:
            if asyncio.get_running_loop().time() > deadline:
                self.fail("subagent never reached the runner")
            await asyncio.sleep(0.01)

        cancelled = await self.manager.cancel_by_session("cli:local")
        self.assertEqual(cancelled, 1)
        self.assertEqual(len(self.bus.published), 1)
        self.assertIn("Cancelled", self.bus.published[0].content)
        outcomes = self.manager.recent_outcomes(session_key="cli:local")
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].status, "error")


class OutcomeHistoryTest(unittest.IsolatedAsyncioTestCase):
    """A finished subagent's fate has to outlive its status."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Path(self._tmp.name) / "outcomes"
        self.store.mkdir()
        patcher = mock.patch(
            "navin.agent.subagent._outcomes_dir", return_value=self.store
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bus = _Bus()
        self.runner = _GatedRunner()
        self.manager = _manager(Path(self._tmp.name), self.bus, self.runner)

    async def _run_one(self, task: str = "the task") -> None:
        await self.manager.spawn(
            task=task,
            runtime=mock.Mock(with_generation_overrides=lambda **_: mock.Mock()),
            origin_channel="cli",
            origin_chat_id="local",
            session_key="cli:local",
        )
        self.runner.gate.set()
        await asyncio.gather(*list(self.manager._running_tasks.values()))

    async def test_the_outcome_survives_the_status_being_dropped(self) -> None:
        await self._run_one()
        self.assertEqual(self.manager._task_statuses, {})
        outcomes = self.manager.recent_outcomes()
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].status, "ok")
        self.assertIn("done", outcomes[0].summary)

    async def test_outcomes_are_scoped_to_a_session(self) -> None:
        await self._run_one()
        self.assertEqual(len(self.manager.recent_outcomes(session_key="cli:local")), 1)
        self.assertEqual(self.manager.recent_outcomes(session_key="cli:other"), [])

    async def test_the_history_cannot_grow_without_bound(self) -> None:
        from navin.agent.subagent import _MAX_OUTCOME_HISTORY

        for index in range(_MAX_OUTCOME_HISTORY + 5):
            self.manager._record_outcome(
                f"id{index}", "label", "task", "result", "ok", "cli:local"
            )
        self.assertEqual(
            len(self.manager.recent_outcomes(session_key="cli:local")),
            _MAX_OUTCOME_HISTORY,
        )

    async def test_one_session_cannot_evict_another_sessions_outcomes(self) -> None:
        """P2-2: retention is per session, not one shared global deque."""
        from navin.agent.subagent import _MAX_OUTCOME_HISTORY

        self.manager._record_outcome("mine", "label", "task", "result", "ok", "cli:local")
        for index in range(_MAX_OUTCOME_HISTORY + 5):
            self.manager._record_outcome(
                f"other{index}", "label", "task", "result", "ok", "cli:busy"
            )
        mine = self.manager.recent_outcomes(session_key="cli:local")
        self.assertEqual([outcome.task_id for outcome in mine], ["mine"])

    async def test_a_wide_fanout_is_fully_retrievable(self) -> None:
        """P2-2: 25 finished subagents are all recoverable via spawn(results)."""
        for index in range(25):
            self.manager._record_outcome(
                f"wide{index}", f"label{index}", "task", "result", "ok", "cli:local"
            )
        outcomes = self.manager.recent_outcomes(session_key="cli:local")
        self.assertEqual(len(outcomes), 25)
        self.assertEqual(outcomes[0].task_id, "wide24")
        self.assertEqual(outcomes[-1].task_id, "wide0")

    async def test_a_long_result_is_summarised_not_stored_whole(self) -> None:
        from navin.agent.subagent import _MAX_OUTCOME_SUMMARY

        self.manager._record_outcome("id", "l", "t", "x" * 5000, "ok", "cli:local")
        self.assertLessEqual(len(self.manager.recent_outcomes()[0].summary), _MAX_OUTCOME_SUMMARY)

    async def test_a_failure_is_recorded_as_a_failure(self) -> None:
        self.manager._record_outcome("id", "l", "t", "Error: boom", "error", "cli:local")
        self.assertEqual(self.manager.recent_outcomes()[0].status, "error")


class OutcomeDurabilityTest(unittest.IsolatedAsyncioTestCase):
    """P2-2: a result that missed its turn must survive a gateway restart.

    The history used to live only in a deque, so a restart before the parent
    read spawn(action="results") destroyed the only record that the work had
    ever happened.
    """

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = self.root / "outcomes"
        self.store.mkdir()
        patcher = mock.patch(
            "navin.agent.subagent._outcomes_dir", return_value=self.store
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bus = _Bus()

    def _fresh_manager(self):
        """A manager with an empty cache, standing in for a restarted gateway."""
        return _manager(self.root, self.bus, _GatedRunner())

    async def test_outcomes_survive_a_restart(self) -> None:
        manager = self._fresh_manager()
        manager._record_outcome("t1", "audit", "audit the parser", "found 3", "ok", "cli:local")
        manager._record_outcome("t2", "bench", "bench it", "Error: boom", "error", "cli:local")

        reborn = self._fresh_manager()
        recovered = reborn.recent_outcomes(session_key="cli:local")
        self.assertEqual([o.task_id for o in recovered], ["t2", "t1"])
        self.assertEqual(recovered[0].status, "error")
        self.assertEqual(recovered[1].summary, "found 3")

    async def test_a_wide_fanout_survives_a_restart(self) -> None:
        manager = self._fresh_manager()
        for index in range(25):
            manager._record_outcome(
                f"wide{index}", f"label{index}", "task", "result", "ok", "cli:local"
            )
        recovered = self._fresh_manager().recent_outcomes(session_key="cli:local")
        self.assertEqual(len(recovered), 25)
        self.assertEqual(recovered[0].task_id, "wide24")

    async def test_restored_sessions_stay_isolated(self) -> None:
        manager = self._fresh_manager()
        manager._record_outcome("mine", "l", "t", "r", "ok", "cli:local")
        manager._record_outcome("theirs", "l", "t", "r", "ok", "cli:other")

        reborn = self._fresh_manager()
        self.assertEqual(
            [o.task_id for o in reborn.recent_outcomes(session_key="cli:local")],
            ["mine"],
        )
        self.assertEqual(
            [o.task_id for o in reborn.recent_outcomes(session_key="cli:other")],
            ["theirs"],
        )

    async def test_the_unscoped_view_also_reads_disk(self) -> None:
        manager = self._fresh_manager()
        manager._record_outcome("a", "l", "t", "r", "ok", "cli:one")
        manager._record_outcome("b", "l", "t", "r", "ok", "cli:two")

        merged = self._fresh_manager().recent_outcomes()
        self.assertEqual({o.task_id for o in merged}, {"a", "b"})

    async def test_persistence_keeps_the_retention_cap(self) -> None:
        from navin.agent.subagent import _MAX_OUTCOME_HISTORY

        manager = self._fresh_manager()
        for index in range(_MAX_OUTCOME_HISTORY + 5):
            manager._record_outcome(f"id{index}", "l", "t", "r", "ok", "cli:local")
        recovered = self._fresh_manager().recent_outcomes(session_key="cli:local")
        self.assertEqual(len(recovered), _MAX_OUTCOME_HISTORY)
        self.assertEqual(recovered[0].task_id, f"id{_MAX_OUTCOME_HISTORY + 4}")

    async def test_a_corrupt_file_is_ignored_not_fatal(self) -> None:
        manager = self._fresh_manager()
        manager._record_outcome("t1", "l", "t", "r", "ok", "cli:local")
        manager._outcome_path("cli:local").write_text("{not json", encoding="utf-8")

        reborn = self._fresh_manager()
        self.assertEqual(reborn.recent_outcomes(session_key="cli:local"), [])
        # And the session keeps working: a new outcome rewrites the file.
        reborn._record_outcome("t2", "l", "t", "r", "ok", "cli:local")
        self.assertEqual(
            [o.task_id for o in self._fresh_manager().recent_outcomes(session_key="cli:local")],
            ["t2"],
        )

    async def test_an_unwritable_store_does_not_break_the_turn(self) -> None:
        manager = self._fresh_manager()
        with mock.patch(
            "navin.utils.atomic_io.atomic_write_text", side_effect=OSError("read-only fs")
        ):
            manager._record_outcome("t1", "l", "t", "r", "ok", "cli:local")
        # In memory for this process, which is what the running turn needs.
        self.assertEqual(
            [o.task_id for o in manager.recent_outcomes(session_key="cli:local")], ["t1"]
        )


class SpawnToolLimitTest(unittest.IsolatedAsyncioTestCase):
    """The limit lives in the tool, and it must not reach across conversations."""

    def setUp(self) -> None:
        from navin.config.schema import AgentDefaults

        self.limit = AgentDefaults().max_concurrent_subagents
        self.manager = mock.Mock()
        self.manager.max_concurrent_subagents = self.limit
        self.manager.spawn = mock.AsyncMock(return_value="Subagent started (id: abcd1234)")
        self.tool = SpawnTool(manager=self.manager)

    def _bind(self, session_key: str = "cli:local"):
        token = bind_request_context(RequestContext(
            channel="cli",
            chat_id="local",
            session_key=session_key,
            runtime=mock.Mock(),
        ))
        self.addCleanup(reset_request_context, token)

    async def test_a_second_spawn_is_allowed_under_the_limit(self) -> None:
        self._bind()
        self.manager.get_running_count_by_session.return_value = 1
        out = await self.tool.execute(task="do a thing")
        self.assertIn("started", out)
        self.manager.spawn.assert_awaited_once()

    async def test_the_limit_is_measured_per_session(self) -> None:
        """A global count let one conversation's subagent block every other one."""
        self._bind()
        self.manager.get_running_count_by_session.return_value = 0
        self.manager.get_running_count.return_value = 99
        out = await self.tool.execute(task="do a thing")
        self.assertIn("started", out)

    async def test_the_tool_never_second_guesses_the_limit(self) -> None:
        """The manager owns the limit and queues past it; the tool just asks.

        Two copies of the check meant two answers to the same question, and the
        one here turned a full conversation into a dead end for the whole turn.
        """
        self._bind()
        self.manager.get_running_count_by_session.return_value = self.limit
        out = await self.tool.execute(task="do a thing")
        self.manager.spawn.assert_awaited_once()
        self.assertNotIn("Cannot spawn", out)

    async def test_starting_without_a_task_is_refused_clearly(self) -> None:
        self._bind()
        self.manager.get_running_count_by_session.return_value = 0
        out = await self.tool.execute()
        self.assertIn("needs a task", out)

    async def test_results_reports_finished_subagents(self) -> None:
        self._bind()
        self.manager.get_running_count_by_session.return_value = 0
        self.manager.recent_outcomes.return_value = [
            mock.Mock(task_id="ab12", label="research", status="ok", summary="found it"),
        ]
        out = await self.tool.execute(action="results")
        self.assertIn("ab12", out)
        self.assertIn("found it", out)

    async def test_results_distinguishes_a_failure(self) -> None:
        self._bind()
        self.manager.get_running_count_by_session.return_value = 0
        self.manager.recent_outcomes.return_value = [
            mock.Mock(task_id="ab12", label="build", status="error", summary="boom"),
        ]
        out = await self.tool.execute(action="results")
        self.assertIn("FAILED", out)

    async def test_results_says_so_when_work_is_still_running(self) -> None:
        self._bind()
        self.manager.recent_outcomes.return_value = []
        self.manager.get_running_count_by_session.return_value = 2
        out = await self.tool.execute(action="results")
        self.assertIn("2 still running", out)

    async def test_results_is_honest_about_an_empty_history(self) -> None:
        self._bind()
        self.manager.recent_outcomes.return_value = []
        self.manager.get_running_count_by_session.return_value = 0
        out = await self.tool.execute(action="results")
        self.assertIn("No subagent", out)

    async def test_an_unknown_model_preset_is_refused_clearly(self) -> None:
        """P2-3: a bad preset name fails the call, not the subagent later."""
        self._bind()
        self.manager.get_running_count_by_session.return_value = 0

        def _resolve(name: str):
            raise KeyError(f"unknown preset {name!r}")

        tool = SpawnTool(manager=self.manager, resolve_model_preset=_resolve)
        out = await tool.execute(task="do a thing", model_preset="nope")
        self.assertIn("could not be resolved", out)
        self.manager.spawn.assert_not_awaited()

    async def test_model_preset_without_resolver_is_refused(self) -> None:
        self._bind()
        self.manager.get_running_count_by_session.return_value = 0
        tool = SpawnTool(manager=self.manager, resolve_model_preset=None)
        out = await tool.execute(task="do a thing", model_preset="fast")
        self.assertIn("not available", out)
        self.manager.spawn.assert_not_awaited()

    async def test_results_lists_a_wide_fanout_in_full(self) -> None:
        """P2-2 acceptance: 25 finished subagents all show up, none dropped."""
        self._bind()
        self.manager.get_running_count_by_session.return_value = 0
        self.manager.recent_outcomes.return_value = [
            mock.Mock(
                task_id=f"wide{index}",
                label=f"job-{index}",
                status="ok",
                summary=f"result {index}",
            )
            for index in range(25)
        ]
        out = await self.tool.execute(action="results")
        self.assertIn("25 finished subagent(s)", out)
        for index in range(25):
            self.assertIn(f"wide{index}", out)


class SpawnModelPresetTest(unittest.IsolatedAsyncioTestCase):
    """P2-3 acceptance: a preset-spawned subagent runs on its own model."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bus = _Bus()
        self.runner = _GatedRunner()
        self.manager = _manager(Path(self._tmp.name), self.bus, self.runner)

    async def test_preset_subagent_uses_a_different_model_than_the_parent(self) -> None:
        parent_runtime = mock.Mock(
            model="parent-deep-model",
            with_generation_overrides=lambda **_: mock.Mock(),
        )
        fast_runtime = mock.Mock(
            model="fast-model",
            with_generation_overrides=lambda **_: fast_runtime,
        )
        token = bind_request_context(RequestContext(
            channel="cli",
            chat_id="local",
            session_key="cli:local",
            runtime=parent_runtime,
        ))
        self.addCleanup(reset_request_context, token)

        tool = SpawnTool(
            manager=self.manager,
            resolve_model_preset=lambda name: fast_runtime,
        )
        out = await tool.execute(task="implement the helper", model_preset="fast")
        self.assertIn("started", out)
        statuses = list(self.manager._task_statuses.values())
        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0].model, "fast-model")
        self.assertNotEqual(statuses[0].model, parent_runtime.model)
        self.runner.gate.set()
        await asyncio.gather(*list(self.manager._running_tasks.values()))

    async def test_without_preset_the_parent_model_is_inherited(self) -> None:
        parent_runtime = mock.Mock(
            model="parent-deep-model",
            with_generation_overrides=lambda **_: mock.Mock(),
        )
        token = bind_request_context(RequestContext(
            channel="cli",
            chat_id="local",
            session_key="cli:local",
            runtime=parent_runtime,
        ))
        self.addCleanup(reset_request_context, token)

        tool = SpawnTool(manager=self.manager, resolve_model_preset=lambda name: None)
        out = await tool.execute(task="implement the helper")
        self.assertIn("started", out)
        statuses = list(self.manager._task_statuses.values())
        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0].model, "parent-deep-model")
        self.runner.gate.set()
        await asyncio.gather(*list(self.manager._running_tasks.values()))


class _WritingRunner:
    """A runner that touches a file the way a real editing tool would."""

    def __init__(self, target: Path | None) -> None:
        self.target = target

    async def run(self, spec) -> AgentRunResult:
        from navin.agent.checkpoints import record_file_before

        if self.target is not None:
            record_file_before(self.target)
            self.target.write_text("edited", encoding="utf-8")
        return AgentRunResult(final_content="done", messages=[])


class EditBaselineTest(unittest.IsolatedAsyncioTestCase):
    """A subagent's edits must stay reviewable, which the parent turn cannot do.

    Baselines used to be written into the parent turn's recorder, which is
    flushed when that turn ends - normally before any subagent finishes. The
    files a subagent touched were therefore absent from the pending review, so
    nothing could show or undo them.
    """

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        self.recorded: list[tuple[str, dict]] = []
        self.runner = _WritingRunner(self.workspace / "touched.txt")
        self.manager = _manager(
            self.workspace,
            _Bus(),
            self.runner,
            record_edits=lambda key, files: self.recorded.append((key, files)),
        )

    async def _run(self) -> None:
        await self.manager.spawn(
            task="edit something",
            runtime=mock.Mock(with_generation_overrides=lambda **_: mock.Mock()),
            origin_channel="cli",
            origin_chat_id="local",
            session_key="cli:local",
        )
        await asyncio.gather(*list(self.manager._running_tasks.values()))

    async def test_a_subagent_edit_is_handed_to_the_review_store(self) -> None:
        await self._run()
        self.assertEqual(len(self.recorded), 1)
        session_key, files = self.recorded[0]
        self.assertEqual(session_key, "cli:local")
        self.assertIn(str(self.workspace / "touched.txt"), files)

    async def test_the_baseline_is_the_content_before_the_edit(self) -> None:
        target = self.workspace / "touched.txt"
        target.write_text("original", encoding="utf-8")
        await self._run()
        _, files = self.recorded[0]
        self.assertEqual(files[str(target)], b"original")

    async def test_a_subagent_that_edits_nothing_records_nothing(self) -> None:
        self.runner.target = None
        await self._run()
        self.assertEqual(self.recorded, [])

    async def test_a_failing_review_store_does_not_lose_the_result(self) -> None:
        """The task finished; a bookkeeping failure must not bury that."""
        bus = _Bus()
        runner = _WritingRunner(self.workspace / "touched.txt")
        manager = _manager(
            self.workspace, bus, runner,
            record_edits=mock.Mock(side_effect=OSError("disk full")),
        )
        await manager.spawn(
            task="edit something",
            runtime=mock.Mock(with_generation_overrides=lambda **_: mock.Mock()),
            origin_channel="cli",
            origin_chat_id="local",
            session_key="cli:local",
        )
        await asyncio.gather(*list(manager._running_tasks.values()))
        self.assertEqual(len(bus.published), 1)


class InjectionCapCoversOneWaveTest(unittest.TestCase):
    """The parent must drain a full subagent wave in a single injection cycle.

    Only a comment tied these two numbers together, and raising the plan
    entitlement to 20 concurrent agents broke it: a wave took three of the five
    available cycles, starving the ones left for real follow-up work.
    """

    def test_the_cap_covers_the_default_fan_out(self):
        from navin.agent.runner import _MAX_INJECTIONS_PER_TURN
        from navin.config.schema import Config

        self.assertGreaterEqual(
            _MAX_INJECTIONS_PER_TURN,
            Config().agents.defaults.max_concurrent_subagents,
        )


class InjectionOverflowTest(unittest.IsolatedAsyncioTestCase):
    """A result the turn cannot carry must be named, not merely logged.

    The drain asks its callback for at most the cap, so in the normal path the
    surplus simply waits for the next cycle. This is the guard for a callback
    that over-delivers anyway, where the difference is between a subagent that
    looks slow and one that looks like it never existed.
    """

    def setUp(self) -> None:
        from navin.agent.runner import _MAX_INJECTIONS_PER_TURN, AgentRunner

        self.cap = _MAX_INJECTIONS_PER_TURN
        self.runner = AgentRunner()

    async def _drain(self, items: list[dict]) -> list[dict]:
        async def over_delivering_callback():
            return items

        return await self.runner._drain_injections(
            mock.Mock(injection_callback=over_delivering_callback)
        )

    async def _drain_texts(self, count: int) -> list[dict]:
        return await self._drain(
            [{"role": "user", "content": f"result {i}"} for i in range(count)]
        )

    async def test_results_within_the_cap_are_untouched(self) -> None:
        out = await self._drain_texts(self.cap)
        self.assertEqual(len(out), self.cap)
        self.assertNotIn("did not fit", out[-1]["content"])

    async def test_an_overflow_is_named_in_the_last_message(self) -> None:
        out = await self._drain_texts(self.cap + 2)
        self.assertEqual(len(out), self.cap)
        self.assertIn("2 further background result(s)", out[-1]["content"])

    async def test_the_surviving_content_is_not_replaced_by_the_note(self) -> None:
        out = await self._drain_texts(self.cap + 1)
        self.assertIn(f"result {self.cap - 1}", out[-1]["content"])

    async def test_a_block_content_message_still_gets_the_note(self) -> None:
        out = await self._drain(
            [
                {"role": "user", "content": [{"type": "text", "text": f"r{i}"}]}
                for i in range(self.cap + 1)
            ]
        )
        self.assertIn("did not fit", out[-1]["content"][-1]["text"])


if __name__ == "__main__":
    unittest.main()
