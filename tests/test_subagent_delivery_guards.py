"""A subagent's result and its team messages survive persistence failures."""

import asyncio
import concurrent.futures
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock

from navin.agent.runner import AgentRunResult
from navin.agent.subagent import SPAWN_REFUSED_PREFIX, SubagentManager
from tests.test_subagent_concurrency import _Bus, _GatedRunner


def _manager(tmp_path, monkeypatch, runner=None, **kwargs) -> SubagentManager:
    monkeypatch.setattr("navin.agent.subagent._outcomes_dir", lambda: tmp_path / "outcomes")
    manager = SubagentManager(workspace=tmp_path, bus=_Bus(), max_tool_result_chars=4000, **kwargs)
    manager.runner = runner or _GatedRunner()
    manager._build_tools = Mock()
    manager._build_subagent_prompt = Mock(return_value="Agent")
    return manager


async def _stop(manager: SubagentManager, *sessions: str) -> None:
    for session in sessions:
        await manager.cancel_by_session(session)
    if manager._watchdog_task is not None:
        manager._watchdog_task.cancel()
        await asyncio.gather(manager._watchdog_task, return_exceptions=True)


def test_failed_journal_and_outcome_writes_do_not_block_the_result(tmp_path, monkeypatch):
    async def run():
        manager = _manager(tmp_path, monkeypatch)
        failed: concurrent.futures.Future = concurrent.futures.Future()
        failed.set_exception(RuntimeError("database is locked"))
        manager._pending_journal_writes.append(failed)
        manager._agent_sessions["c1"] = "cli:one"

        def broken(_outcomes):
            raise RuntimeError("disk full")

        manager._record_outcomes = broken
        await manager._announce_result("c1", "worker", "task", "all good",
                                       {"channel": "cli", "chat_id": "one", "session_key": "cli:one"}, "ok")
        assert len(manager.bus.published) == 1
        assert manager.bus.published[0].metadata["subagent_task_id"] == "c1"
        await manager.flush_journal()  # nothing left to re-raise

    asyncio.run(run())


def test_team_messages_survive_a_failed_durable_delivery(tmp_path, monkeypatch):
    seen: list[list[str]] = []

    class Runner:
        async def run(self, spec):
            task_id = manager.team_members("cli:one")[0]["task_id"]
            for text in ("first", "second", "third"):
                manager.send_team_message("cli:one", "parent", task_id, text)
            real_io = manager._journal_io
            calls = {"n": 0}

            def flaky(method, *args):
                if method == "deliver_injection":
                    calls["n"] += 1
                    if calls["n"] == 2:
                        raise RuntimeError("database is locked")
                return real_io(method, *args)

            manager._journal_io = flaky
            for _ in range(2):
                got = await spec.injection_callback(limit=64)
                seen.append([item["content"].rsplit("\n", 1)[-1] for item in got])
            return AgentRunResult(final_content="done", messages=[])

    async def run():
        nonlocal manager
        manager = _manager(tmp_path, monkeypatch, runner=Runner())
        await manager.spawn(task="Work", runtime=Mock(), session_key="cli:one")
        for _ in range(200):
            if manager.bus.published:
                break
            await asyncio.sleep(0.01)
        await _stop(manager, "cli:one")

    manager = None
    asyncio.run(run())
    assert seen == [["first"], ["second", "third"]]


def test_nested_report_is_recorded_and_reannounced_when_spawner_ends_unread(tmp_path, monkeypatch):
    async def run():
        runner = _GatedRunner()
        manager = _manager(tmp_path, monkeypatch, runner=runner)
        await manager.spawn(task="Lead work", runtime=Mock(), session_key="cli:one")
        parent = manager.team_members("cli:one")[0]["task_id"]
        manager._spawn_parents["c1"] = parent
        manager._agent_sessions["c1"] = "cli:one"
        origin = {"channel": "cli", "chat_id": "one", "session_key": "cli:one"}
        await manager._announce_result("c1", "helper", "sub task", "helper result", origin, "ok")
        assert manager.bus.published == []
        assert [o.task_id for o in manager.recent_outcomes("cli:one")] == ["c1"]
        assert manager._agent_mailboxes[parent][0]["announce"]["result"] == "helper result"
        manager._spawn_parents.pop("c1")
        manager._agent_sessions.pop("c1")
        runner.gate.set()
        for _ in range(200):
            if len(manager.bus.published) >= 2:
                break
            await asyncio.sleep(0.01)
        ids = sorted(m.metadata["subagent_task_id"] for m in manager.bus.published)
        assert ids == sorted([parent, "c1"])
        # Re-announcing does not record the child's outcome twice.
        assert [o.task_id for o in manager.recent_outcomes("cli:one")].count("c1") == 1
        await _stop(manager, "cli:one")

    asyncio.run(run())


def test_refused_spawn_leaves_no_ghost_records(tmp_path, monkeypatch):
    async def run():
        manager = _manager(tmp_path, monkeypatch, max_concurrent_subagents=1)
        manager.max_queued_subagents = 0
        await manager.spawn(task="One", runtime=Mock(), session_key="cli:one")
        refused = await manager.spawn(task="Two", runtime=Mock(), session_key="cli:one",
                                      parent_task_id="p1", depth=2,
                                      member={"id": "dev", "name": "Dev"}, budget_tokens=10)
        assert refused.startswith(SPAWN_REFUSED_PREFIX)
        assert len(manager._spawn_depths) == 1
        assert manager._spawn_parents == {}
        assert manager._collab_budget == {}
        assert manager._task_members == {}
        await _stop(manager, "cli:one")
        assert manager._spawn_depths == {}

    asyncio.run(run())


def test_drain_all_survives_a_start_failure(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    started = []

    def start(pending):
        if pending.task_id == "bad":
            raise RuntimeError("boom")
        started.append(pending.task_id)

    manager._start = start
    manager._has_free_slot = lambda _key: True
    manager._queued["cli:one"] = deque([SimpleNamespace(task_id="bad"), SimpleNamespace(task_id="ok")])
    manager._drain_all()
    assert started == ["ok"]
    assert "cli:one" not in manager._queued


def test_background_tasks_are_held_until_done(tmp_path, monkeypatch):
    async def run():
        manager = _manager(tmp_path, monkeypatch)
        task = asyncio.ensure_future(asyncio.sleep(0))
        manager._keep_task(task)
        assert task in manager._background_tasks
        await task
        await asyncio.sleep(0)
        assert task not in manager._background_tasks

    asyncio.run(run())


def test_debate_counters_end_with_the_run_and_parent_inbox_keeps_newest(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    for task_id, member in (("a1", "dev"), ("b1", "qa")):
        manager._agent_sessions[task_id] = "cli:one"
        manager._agent_mailboxes[task_id] = deque()
        manager._task_members[task_id] = {"id": member, "name": member}
    manager._pair_counts[("cli:one", "dev", "qa")] = 24
    manager._pair_counts[("cli:two", "dev", "qa")] = 3
    manager._forget("a1")
    assert manager._pair_counts == {("cli:two", "dev", "qa"): 3}

    for index in range(65):
        manager._send_to_parent("cli:one", "b1", f"note {index}")
    box = manager._parent_mailboxes["cli:one"]
    assert len(box) == 64
    assert box[0]["content"] == "note 1" and box[-1]["content"] == "note 64"


def test_stop_does_not_deadlock_on_a_full_inbound_queue(tmp_path, monkeypatch):
    from navin.bus.events import InboundMessage
    from navin.bus.queue import MessageBus

    async def run():
        manager = _manager(tmp_path, monkeypatch)
        manager.bus = MessageBus(inbound_maxsize=1)
        await manager.bus.publish_inbound(InboundMessage(channel="cli", sender_id="u", chat_id="one",
                                                         content="filler"))
        for _ in range(3):
            await manager.spawn(task="Work", runtime=Mock(), session_key="cli:one")
        await asyncio.sleep(0)
        await asyncio.wait_for(manager.cancel_by_session("cli:one"), timeout=5)
        # The parked announcements are still delivered once the loop reads again.
        delivered = []
        for _ in range(4):
            delivered.append(await asyncio.wait_for(manager.bus.consume_inbound(), timeout=5))
        assert len(delivered) == 4
        await _stop(manager)

    asyncio.run(run())


def _run_with_edits(tmp_path, monkeypatch, result: AgentRunResult, files: list[str]):
    class Runner:
        async def run(self, spec):
            spec.loop_guard.validation.root = tmp_path
            spec.loop_guard.validation.paths.update(str(tmp_path / name) for name in files)
            return result

    async def run():
        manager = _manager(tmp_path, monkeypatch, runner=Runner())
        await manager.spawn(task="Add the stats API", runtime=Mock(), session_key="cli:one")
        for _ in range(200):
            if manager.bus.published:
                break
            await asyncio.sleep(0.01)
        published = [m.content for m in manager.bus.published]
        outcomes = manager.recent_outcomes("cli:one")
        await _stop(manager, "cli:one")
        return published, outcomes

    return asyncio.run(run())


def test_a_failed_run_tells_the_parent_which_files_it_already_changed(tmp_path, monkeypatch):
    failed = AgentRunResult(
        final_content=None, messages=[], stop_reason="error", error="provider timed out",
    )
    published, outcomes = _run_with_edits(
        tmp_path, monkeypatch, failed, ["api/stats.py", "web/src/Stats.tsx"],
    )

    assert len(published) == 1
    text = published[0]
    assert "provider timed out" in text
    assert "Files this subagent changed before it stopped (2)" in text
    assert "do not redo this work" in text
    assert "- api/stats.py" in text and "- web/src/Stats.tsx" in text
    # The recorded outcome keeps the list too: it is all a later turn gets.
    assert "- web/src/Stats.tsx" in outcomes[0].summary


def test_a_finished_run_lists_its_files_and_a_run_without_edits_adds_nothing(tmp_path, monkeypatch):
    done = AgentRunResult(final_content="Stats API added.", messages=[])
    published, _ = _run_with_edits(tmp_path, monkeypatch, done, ["api/stats.py"])
    assert "Stats API added." in published[0]
    assert "Files this subagent changed (1):" in published[0]
    assert "- api/stats.py" in published[0]

    plain = AgentRunResult(final_content="Nothing to change.", messages=[])
    published, _ = _run_with_edits(tmp_path / "other", monkeypatch, plain, [])
    assert "Files this subagent changed" not in published[0]


def test_changed_files_note_caps_the_list_and_names_a_kept_checkout(tmp_path):
    from navin.agent.subagent import _MAX_CHANGED_FILES_LISTED, _changed_files_note

    names = [f"src/m{index:02d}.py" for index in range(_MAX_CHANGED_FILES_LISTED + 3)]
    validation = SimpleNamespace(paths={str(tmp_path / n) for n in names}, root=tmp_path)

    note = _changed_files_note(validation, partial=True, kept_at=tmp_path / "wt")
    assert f"({len(names)}), kept in its checkout {tmp_path / 'wt'}" in note
    assert "- src/m00.py" in note
    assert "- ... and 3 more" in note
    assert _changed_files_note(SimpleNamespace(paths=set(), root=None), partial=True) == ""
    assert _changed_files_note(None, partial=False) == ""


def test_changed_files_note_lists_docs_and_command_made_files_once(tmp_path):
    from navin.agent.subagent import _changed_files_note

    validation = SimpleNamespace(
        paths={str(tmp_path / "api" / "stats.py")},
        # The same file seen relative, a report and a file a command generated.
        touched={"./api/stats.py", "audit/report.md", str(tmp_path / "gen" / "schema.py")},
        root=tmp_path,
    )
    note = _changed_files_note(validation, partial=False)
    assert "Files this subagent changed (3):" in note
    assert note.count("- api/stats.py") == 1
    assert "- audit/report.md" in note and "- gen/schema.py" in note
