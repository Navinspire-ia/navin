"""Durable per-agent visibility and safe restart boundaries."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from navin.agent.runner import AgentRunResult
from navin.agent.subagent import (
    SubagentManager,
    SubagentStatus,
    _SubagentHook,
    request_subagent_operation,
)
from navin.agent.subagent_journal import SubagentJournal, owner_alive, process_identity
from navin.providers.base import GenerationSettings
from navin.utils.llm_runtime import LLMRuntime


def runtime():
    return LLMRuntime(provider=Mock(), model="test-model", generation=GenerationSettings(),
                      context_window_tokens=128000)


def test_history_is_complete_paginated_private_and_persistent(tmp_path):
    path = tmp_path / "execution.sqlite3"
    journal = SubagentJournal(path)
    journal.create("one", "agent", {"task_id": "agent"}, {"task": "Implement"})
    for index in range(450):
        journal.append("one", "agent", "tool_done", {
            "name": "exec", "arguments": {"command": f"echo {index}"},
            "result": "full result " + str(index) + "x" * 2000,
        })
    journal.checkpoint("one", "agent", {
        "phase": "final_response", "iteration": 1,
        "assistant_message": {"role": "assistant", "content": "Visible final answer",
                              "reasoning_content": "Hidden private reasoning"},
    })
    restored = SubagentJournal(path)
    assert restored.get("two", "agent") is None
    assert not restored.history("two", "agent")["events"]
    cursor, events = 0, []
    while True:
        page = restored.history("one", "agent", cursor, 37)
        events.extend(page["events"])
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
    assert len(events) == 452
    assert len({event["seq"] for event in events}) == 452
    assert "Hidden private reasoning" not in repr(events)
    assert "449" in events[-2]["payload"]["result"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_a_shared_session_key_lists_only_one_project_and_conversation(tmp_path):
    # Every CLI starts on cli:direct whatever the project, and /new keeps the
    # key: F8 listed the agents of every project and every earlier chat.
    journal = SubagentJournal(tmp_path / "execution.sqlite3")
    for task_id, workspace, created in (
        ("old-here", "/projects/app", 100.0), ("new-here", "/projects/app", 300.0),
        ("other-project", "/projects/bbac", 300.0), ("no-folder", None, 300.0),
    ):
        descriptor = {"task": task_id} | ({"workspace": workspace} if workspace else {})
        journal.create("cli:direct", task_id, {"task_id": task_id}, descriptor)
        journal._db.execute("UPDATE agents SET created_at=? WHERE task_id=?", (created, task_id))
    journal._db.commit()

    def ids(**scope):
        return [row["metadata"]["task_id"] for row in journal.list("cli:direct", **scope)]

    assert ids() == ["old-here", "new-here", "no-folder", "other-project"]
    assert ids(workspaces={"/projects/app"}) == ["old-here", "new-here"]
    assert ids(workspaces={"/projects/app"}, since=200.0) == ["new-here"]
    assert ids(workspaces=set()) == []


def test_claim_is_exclusive_and_pid_reuse_does_not_hide_interrupted_work(tmp_path):
    path = tmp_path / "execution.sqlite3"
    first, second = SubagentJournal(path), SubagentJournal(path)
    first.create("one", "agent", {"task_id": "agent", "done": False}, {"task": "Implement"})
    assert owner_alive(os.getpid(), process_identity(os.getpid()))
    assert not owner_alive(os.getpid(), "a previous boot or process")
    assert not first.claim("one", "agent")
    assert not second.claim("other", "agent")
    with first._lock, first._db:
        first._db.execute("UPDATE agents SET owner_identity='previous process'")
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda journal: journal.claim("one", "agent"), (first, second)))
    assert sorted(claims) == [False, True]


def test_failed_agents_offer_explicit_relaunch(tmp_path):
    manager = SubagentManager(workspace=tmp_path, bus=Mock(), max_tool_result_chars=4000)
    manager._journal_instance = SubagentJournal(tmp_path / "history.sqlite3")
    manager.journal.create("one", "worker", {
        "task_id": "worker", "done": True, "phase": "error", "error": "Provider failed",
    }, {"task": "Work", "isolate": False})
    agent = manager.list_agents("one")[0]
    assert agent["requires_relaunch"] and not agent["recoverable"]


def test_history_pages_bound_bytes_without_truncating_large_single_results(tmp_path):
    journal = SubagentJournal(tmp_path / "history.sqlite3")
    journal.create("one", "worker", {}, {"task": "Work"})
    for index in range(12):
        journal.append("one", "worker", "tool_done", {"result": str(index) + "x" * 150000})
    journal.append("one", "worker", "tool_done", {"result": "large" + "y" * 1100000})
    cursor, events, pages = 0, [], []
    while True:
        page = journal.history("one", "worker", cursor, 100)
        pages.append(page)
        events.extend(page["events"])
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
    assert len(pages) > 3
    assert len(events) == 14
    assert len(events[-1]["payload"]["result"]) == 1100005
    assert len(pages[-1]["events"]) == 1


def test_peer_delivery_commits_injection_and_consumption_together(tmp_path):
    journal = SubagentJournal(tmp_path / "history.sqlite3")
    journal.create("one", "worker", {}, {"task": "Work"})
    message = {"message_id": "m1", "sender": "peer", "content": "API total"}
    journal.append("one", "worker", "team_received", message)
    assert journal.pending_messages("one", "worker") == [message]
    injection = {"role": "user", "content": "Peer says API total"}
    journal.deliver_injection("one", "worker", message, injection)
    assert journal.pending_messages("one", "worker") == []
    assert injection in journal.messages("one", "worker")


def test_journal_hook_failure_cancels_and_drains_sibling_tools():
    from navin.agent.hook import AgentHookContext, CompositeHook
    from navin.agent.runner import AgentRunner
    from navin.providers.base import ToolCallRequest

    async def run():
        runner = AgentRunner()
        sibling_started, sibling_cancelled = asyncio.Event(), asyncio.Event()
        calls = [ToolCallRequest(id=name, name="read_file", arguments={"path": name})
                 for name in ("fast", "slow")]

        async def run_tool(spec, call, *args, **kwargs):
            if call.id == "fast":
                await sibling_started.wait()
                return "Full output", {"name": "read_file", "status": "ok"}, None
            sibling_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                sibling_cancelled.set()

        async def broken_journal(*args):
            raise OSError("Journal storage failed")

        runner._partition_tool_batches = Mock(return_value=[calls])
        runner._run_tool = run_tool
        hook = CompositeHook([_SubagentHook("worker", SubagentStatus(
            task_id="worker", label="Worker", task_description="Task", started_at=0,
        ), record_event=broken_journal)])
        spec = SimpleNamespace(loop_guard=None, concurrent_tools=True,
                               requires_verify_before_done=False, validate_code_changes=False)
        with pytest.raises(OSError, match="Journal storage failed"):
            await runner._execute_tools(spec, calls, {}, {}, hook,
                                        AgentHookContext(iteration=1, messages=[], tool_calls=calls))
        assert sibling_cancelled.is_set()

    asyncio.run(run())


def test_subagent_control_timeout_also_bounds_full_inbound_queue():
    async def run():
        class StalledBus:
            async def publish_inbound(self, msg):
                await asyncio.Event().wait()

        result = await asyncio.wait_for(request_subagent_operation(
            StalledBus(), "cli:one", "relaunch", "worker", timeout=0.01,
        ), 1)
        assert not result["ok"] and result["timed_out"]

    asyncio.run(run())


def test_expired_control_request_cannot_relaunch_work(tmp_path):
    from navin.agent.subagent import handle_subagents_query
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_ACK,
        RUNTIME_CONTROL_SUBAGENTS_QUERY,
    )

    async def run():
        manager = SubagentManager(workspace=tmp_path, bus=Bus(), max_tool_result_chars=4000)
        manager.relaunch_agent = AsyncMock()
        ack = asyncio.get_running_loop().create_future()
        ack.cancel()
        msg = SimpleNamespace(metadata={
            INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_SUBAGENTS_QUERY,
            RUNTIME_CONTROL_ACK: ack, "operation": "relaunch", "session_key": "cli:one",
        })
        assert await handle_subagents_query(SimpleNamespace(subagents=manager), msg, None)
        manager.relaunch_agent.assert_not_called()

    asyncio.run(run())


class Bus:
    def __init__(self):
        self.outbound = asyncio.Queue()
        self.inbound = []

    async def publish_inbound(self, msg):
        self.inbound.append(msg)


class Runner:
    def __init__(self):
        self.specs = []

    async def run(self, spec):
        self.specs.append(spec)
        return AgentRunResult(final_content="Recovered safely", messages=spec.initial_messages)


def test_restart_recovers_complete_boundary_without_replaying_tool_batch(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr("navin.agent.subagent._outcomes_dir", lambda: tmp_path / "history")
        manager = SubagentManager(workspace=tmp_path, bus=Bus(), max_tool_result_chars=4000)
        manager.runner = Runner()
        manager._build_tools = Mock()
        manager._build_subagent_prompt = Mock(return_value="Agent")
        descriptor = {"task": "Implement", "label": "Worker", "agent": None,
                      "origin": {"channel": "cli", "chat_id": "one", "session_key": "cli:one"},
                      "origin_message_id": None, "isolate": False, "scope_paths": None,
                      "workspace": str(tmp_path), "policy": {
                          "requires_verify_before_done": True, "validate_code_changes": True,
                          "locked_denied_tools": ["forbidden"], "allowed_tools": ["read_file"],
                      }}
        for task_id, phase in (("safe", "tools_completed"), ("unsafe", "awaiting_tools"),
                               ("queued", "queued"), ("final", "final_response")):
            manager.journal.create("cli:one", task_id, {
                "task_id": task_id, "label": "Worker", "done": False, "phase": phase,
            }, descriptor)
            if task_id == "final":
                manager.journal.checkpoint("cli:one", task_id, {
                    "phase": "final_response", "iteration": 2,
                    "assistant_message": {"role": "assistant", "content": "Already finished result"},
                })
            elif task_id != "queued":
                manager.journal.checkpoint("cli:one", task_id, {
                    "phase": phase, "iteration": 1,
                    "assistant_message": {"role": "assistant", "content": "Checking",
                                          "tool_calls": [{"id": "read", "type": "function",
                                                          "function": {"name": "read_file", "arguments": "{}"}}]},
                    "completed_tool_results": [{"role": "tool", "tool_call_id": "read",
                                                "content": "Existing file contents"}]
                    if task_id == "safe" else [],
                    "validation_state": {"paths": ["app.py"], "revision": 1,
                                         "needs_tests": True, "root": str(tmp_path)},
                })
        with manager.journal._lock, manager.journal._db:
            manager.journal._db.execute("UPDATE agents SET owner_pid=-1")
        # A fresh manager reconstructs history from disk, not an in-memory cache.
        fresh = SubagentManager(workspace=tmp_path, bus=Bus(), max_tool_result_chars=4000)
        fresh.runner = Runner()
        fresh._build_tools = Mock()
        fresh._build_subagent_prompt = Mock(return_value="Agent")
        entries = {item["task_id"]: item for item in fresh.list_agents("cli:one")}
        assert entries["safe"]["recoverable"]
        assert entries["queued"]["recoverable"]
        assert entries["unsafe"]["requires_relaunch"]
        assert "interrupted" in entries["unsafe"]["phase"]
        assert "Cannot resume" in await fresh.recover_agent("cli:other", "safe", runtime=runtime())
        assert "tool batch" in await fresh.recover_agent("cli:one", "unsafe", runtime=runtime())
        assert not fresh.runner.specs
        assert "resumed" in await fresh.recover_agent("cli:one", "safe", runtime=runtime())
        await asyncio.gather(*fresh._running_tasks.values())
        spec = fresh.runner.specs[0]
        assert "Existing file contents" in repr(spec.initial_messages)
        assert spec.requires_verify_before_done
        assert "forbidden" in spec.denied_tools
        assert spec.allowed_tools == frozenset({"read_file"})
        assert spec.loop_guard.validation.pending
        assert spec.loop_guard.validation.paths == {"app.py"}
        await fresh.flush_journal()
        assert fresh.agent_history("cli:one", "safe")["agent"]["done"]
        assert "recovery" in repr(fresh.agent_history("cli:one", "safe")["events"])
        assert "started" in await fresh.relaunch_agent("cli:one", "unsafe", runtime=runtime())
        await asyncio.gather(*fresh._running_tasks.values())
        assert len(fresh.runner.specs) == 2
        assert fresh.agent_history("cli:one", "unsafe")["agent"]["requires_relaunch"]
        assert "resumed" in await fresh.recover_agent("cli:one", "final", runtime=runtime())
        await asyncio.gather(*fresh._running_tasks.values())
        assert len(fresh.runner.specs) == 2, "Durable final response must not run the model again"
        history = fresh.agent_history("cli:one", "final")
        assert history["agent"]["done"]
        assert history["events"][-1]["payload"]["content"] == "Already finished result"
        fresh._watchdog_task.cancel()
        await asyncio.gather(fresh._watchdog_task, return_exceptions=True)

    asyncio.run(run())


def test_hundreds_have_full_results_and_do_not_block_event_loop(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr("navin.agent.subagent._outcomes_dir", lambda: tmp_path / "history")
        manager = SubagentManager(workspace=tmp_path, bus=Bus(), max_tool_result_chars=4000,
                                  max_concurrent_subagents=200)
        gate = asyncio.Event()
        entered = 0
        ticks = 0

        class GatedRunner:
            async def run(self, spec):
                nonlocal entered
                entered += 1
                await gate.wait()
                return AgentRunResult(final_content="Complete result " + "x" * 3000, messages=[])

        async def heartbeat():
            nonlocal ticks
            while not gate.is_set():
                ticks += 1
                await asyncio.sleep(0.001)

        manager.runner = GatedRunner()
        manager._build_tools = Mock()
        manager._build_subagent_prompt = Mock(return_value="Agent")
        beat = asyncio.create_task(heartbeat())
        try:
            await asyncio.gather(*(manager.spawn(task=f"Part {index}", runtime=runtime(),
                                                 session_key="cli:load") for index in range(300)))
            async with asyncio.timeout(15):
                while entered < 200:
                    await asyncio.sleep(0.01)
            assert entered == 200
            assert manager.queued_count("cli:load") == 100
            assert ticks > 10
            gate.set()
            async with asyncio.timeout(20):
                while manager.get_running_count():
                    await asyncio.sleep(0.01)
            await manager.flush_journal()
            agents = manager.list_agents("cli:load", limit=1000)
            assert len(agents) == 300
            assert all(agent["done"] for agent in agents)
            assert manager.list_agents("cli:another") == []
            events = manager.agent_history("cli:load", agents[0]["task_id"])["events"]
            result = next(event for event in events if event["kind"] == "result")
            assert len(result["payload"]["content"]) > 3000
        finally:
            gate.set()
            await beat
            await manager.cancel_by_session("cli:load")
            manager._watchdog_task.cancel()
            await asyncio.gather(manager._watchdog_task, return_exceptions=True)

    asyncio.run(run())


def test_total_capacity_is_shared_and_queued_conversations_make_progress(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr("navin.agent.subagent._outcomes_dir", lambda: tmp_path / "history")
        manager = SubagentManager(workspace=tmp_path, bus=Bus(), max_tool_result_chars=4000,
                                  max_concurrent_subagents=5, max_total_concurrent_subagents=2)
        gates = {}
        entered = []

        class PerTaskRunner:
            async def run(self, spec):
                label = spec.initial_messages[-1]["content"]
                entered.append(label)
                gates[label] = asyncio.Event()
                await gates[label].wait()
                return AgentRunResult(final_content="Done", messages=[])

        manager.runner = PerTaskRunner()
        manager._build_tools = Mock()
        manager._build_subagent_prompt = Mock(return_value="Agent")
        try:
            await manager.spawn(task="A1", session_key="cli:a", runtime=runtime())
            await manager.spawn(task="B1", session_key="cli:b", runtime=runtime())
            await manager.spawn(task="A2", session_key="cli:a", runtime=runtime())
            await manager.spawn(task="B2", session_key="cli:b", runtime=runtime())
            async with asyncio.timeout(5):
                while len(entered) < 2:
                    await asyncio.sleep(0.01)
            assert entered == ["A1", "B1"]
            assert manager.queued_count() == 2
            gates["A1"].set()
            async with asyncio.timeout(5):
                while len(entered) < 3:
                    await asyncio.sleep(0.01)
            assert len(manager._running_tasks) <= 2
            gates["B1"].set()
            async with asyncio.timeout(5):
                while len(entered) < 4:
                    await asyncio.sleep(0.01)
            assert set(entered) == {"A1", "A2", "B1", "B2"}
        finally:
            for gate in gates.values():
                gate.set()
            await manager.cancel_by_session("cli:a")
            await manager.cancel_by_session("cli:b")
            manager._watchdog_task.cancel()
            await asyncio.gather(manager._watchdog_task, return_exceptions=True)

    asyncio.run(run())


def test_graceful_shutdown_can_resume_safe_work_and_unread_peer_messages(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr("navin.agent.subagent._outcomes_dir", lambda: tmp_path / "history")
        manager = SubagentManager(workspace=tmp_path, bus=Bus(), max_tool_result_chars=4000)
        entered = asyncio.Event()

        class WaitingRunner:
            async def run(self, spec):
                entered.set()
                await asyncio.Event().wait()

        manager.runner = WaitingRunner()
        manager._build_tools = Mock()
        manager._build_subagent_prompt = Mock(return_value="Agent")
        try:
            await manager.spawn(task="Implement", session_key="cli:one", runtime=runtime())
            await asyncio.wait_for(entered.wait(), 5)
            task_id = manager.list_agents("cli:one")[0]["task_id"]
            manager.send_team_message("cli:one", "parent", task_id, "Remember API field total")
            await manager.flush_journal()
            await manager.cancel_by_session("cli:one")
            await manager.flush_journal()
            agent = manager.agent_history("cli:one", task_id)["agent"]
            assert agent["done"] and agent["recoverable"]
            entered.clear()
            assert "resumed" in await manager.recover_agent("cli:one", task_id, runtime=runtime())
            await asyncio.wait_for(entered.wait(), 5)
            assert manager._agent_mailboxes[task_id][0]["content"] == "Remember API field total"
            assert "still active" in await manager.recover_agent("cli:one", task_id, runtime=runtime())
        finally:
            await manager.cancel_by_session("cli:one")
            manager._watchdog_task.cancel()
            await asyncio.gather(manager._watchdog_task, return_exceptions=True)

    asyncio.run(run())


def test_admission_keeps_queue_when_completion_removes_old_deque(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr("navin.agent.subagent._outcomes_dir", lambda: tmp_path / "history")
        manager = SubagentManager(workspace=tmp_path, bus=Bus(), max_tool_result_chars=4000,
                                  max_concurrent_subagents=1)
        entered, gates = [], {}

        class ControlledRunner:
            async def run(self, spec):
                label = spec.initial_messages[-1]["content"]
                entered.append(label)
                gates[label] = asyncio.Event()
                await gates[label].wait()
                return AgentRunResult(final_content="Done", messages=[])

        manager.runner = ControlledRunner()
        manager._build_tools = Mock()
        manager._build_subagent_prompt = Mock(return_value="Agent")
        original_persist = manager._persist_spawn
        writing, allow_write = asyncio.Event(), asyncio.Event()

        async def delayed_persist(pending):
            if pending.task == "C":
                writing.set()
                await allow_write.wait()
            await original_persist(pending)

        manager._persist_spawn = delayed_persist
        try:
            for label in ("A", "B"):
                await manager.spawn(task=label, session_key="cli:one", runtime=runtime())
            async with asyncio.timeout(5):
                while "A" not in entered:
                    await asyncio.sleep(0.01)
            spawning = asyncio.create_task(manager.spawn(task="C", session_key="cli:one", runtime=runtime()))
            await asyncio.wait_for(writing.wait(), 5)
            gates["A"].set()
            async with asyncio.timeout(5):
                while "B" not in entered:
                    await asyncio.sleep(0.01)
            assert "cli:one" not in manager._queued
            allow_write.set()
            assert "queued" in await spawning
            assert manager.queued_count("cli:one") == 1
            gates["B"].set()
            async with asyncio.timeout(5):
                while "C" not in entered:
                    await asyncio.sleep(0.01)
            gates["C"].set()
            async with asyncio.timeout(5):
                while manager.get_running_count():
                    await asyncio.sleep(0.01)
            assert entered == ["A", "B", "C"]
        finally:
            allow_write.set()
            for gate in gates.values():
                gate.set()
            await manager.cancel_by_session("cli:one")
            manager._watchdog_task.cancel()
            await asyncio.gather(manager._watchdog_task, return_exceptions=True)

    asyncio.run(run())


def test_auto_recover_resumes_safe_agents_and_leaves_unsafe_for_review(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr("navin.agent.subagent._outcomes_dir", lambda: tmp_path / "history")
        manager = SubagentManager(workspace=tmp_path, bus=Bus(), max_tool_result_chars=4000)
        descriptor = {"task": "Implement", "label": "Worker", "agent": None,
                      "origin": {"channel": "cli", "chat_id": "one", "session_key": "cli:one"},
                      "origin_message_id": None, "isolate": False, "scope_paths": None,
                      "workspace": str(tmp_path), "policy": {
                          "requires_verify_before_done": False, "validate_code_changes": False,
                          "locked_denied_tools": [], "allowed_tools": None,
                      }}
        for task_id, phase, done in (("safe", "tools_completed", False),
                                     ("unsafe", "awaiting_tools", False),
                                     ("over", "done", True)):
            manager.journal.create("cli:one", task_id, {
                "task_id": task_id, "label": "Worker", "done": done, "phase": phase,
            }, descriptor)
            manager.journal.checkpoint("cli:one", task_id, {
                "phase": phase, "iteration": 1,
                "assistant_message": {"role": "assistant", "content": "x", "tool_calls": [
                    {"id": "r", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
                "completed_tool_results": [{"role": "tool", "tool_call_id": "r", "content": "ok"}],
            })
        with manager.journal._lock, manager.journal._db:
            manager.journal._db.execute("UPDATE agents SET owner_pid=-1")
        fresh = SubagentManager(workspace=tmp_path, bus=Bus(), max_tool_result_chars=4000)
        fresh.runner = Runner()
        fresh._build_tools = Mock()
        fresh._build_subagent_prompt = Mock(return_value="Agent")
        assert sorted(t for _, t in fresh.journal.unfinished()) == ["safe", "unsafe"]
        counts = await fresh.auto_recover(runtime=runtime(), scope_for=lambda _key: None)
        assert counts == {"resumed": 1, "skipped": 1}
        await asyncio.gather(*fresh._running_tasks.values())
        assert len(fresh.runner.specs) == 1
        await fresh.flush_journal()
        assert fresh.agent_history("cli:one", "safe")["agent"]["done"]
        assert fresh.agent_history("cli:one", "unsafe")["agent"]["requires_relaunch"]
        # A second pass is a no-op: nothing is resumed twice.
        assert (await fresh.auto_recover(runtime=runtime(), scope_for=lambda _k: None))["resumed"] == 0
        fresh._watchdog_task.cancel()
        await asyncio.gather(fresh._watchdog_task, return_exceptions=True)

    asyncio.run(run())


def test_an_isolated_run_resumes_in_its_saved_checkout(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr("navin.agent.subagent._outcomes_dir", lambda: tmp_path / "history")
        settled = []

        async def settle(_manager, checkout, **_kw):
            settled.append(checkout.path)
            return ""

        monkeypatch.setattr("navin.agent.worktree_merge.settle_after_run", settle)
        manager = SubagentManager(workspace=tmp_path, bus=Bus(), max_tool_result_chars=4000)
        checkout = tmp_path / "wt"
        checkout.mkdir()
        descriptor = {"task": "Implement", "label": "Worker", "agent": None,
                      "origin": {"channel": "cli", "chat_id": "one", "session_key": "cli:one"},
                      "origin_message_id": None, "isolate": True, "scope_paths": None,
                      "workspace": str(tmp_path), "policy": {
                          "requires_verify_before_done": False, "validate_code_changes": False,
                          "locked_denied_tools": [], "allowed_tools": None,
                      }}
        for task_id, saved in (("kept", checkout), ("gone", tmp_path / "missing")):
            manager.journal.create("cli:one", task_id, {
                "task_id": task_id, "label": "Worker", "done": False, "phase": "tools_completed",
            }, descriptor)
            manager.journal.checkpoint("cli:one", task_id, {
                "phase": "tools_completed", "iteration": 1,
                "assistant_message": {"role": "assistant", "content": "x", "tool_calls": [
                    {"id": "r", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
                "completed_tool_results": [{"role": "tool", "tool_call_id": "r", "content": "ok"}],
                "checkout": {"path": str(saved), "origin": str(tmp_path), "base": "abc123"},
            })
        with manager.journal._lock, manager.journal._db:
            manager.journal._db.execute("UPDATE agents SET owner_pid=-1")
        fresh = SubagentManager(workspace=tmp_path, bus=Bus(), max_tool_result_chars=4000)
        fresh.runner = Runner()
        fresh._build_tools = Mock()
        fresh._build_subagent_prompt = Mock(return_value="Agent")
        gone = await fresh.recover_agent("cli:one", "gone", runtime=runtime())
        assert gone.startswith("Cannot resume: isolated workspace")
        detail = await fresh.recover_agent("cli:one", "kept", runtime=runtime())
        assert not detail.startswith("Cannot"), detail
        await asyncio.gather(*fresh._running_tasks.values())
        assert [Path(s.workspace) for s in fresh.runner.specs] == [checkout]
        assert settled == [checkout]
        fresh._watchdog_task.cancel()
        await asyncio.gather(fresh._watchdog_task, return_exceptions=True)

    asyncio.run(run())


def test_llm_requests_in_flight_are_capped(monkeypatch):
    from navin.providers import base

    monkeypatch.setenv("NAVIN_MAX_INFLIGHT_LLM", "3")
    monkeypatch.setattr(base, "_inflight_sem", None)

    async def run():
        live = peak = 0

        async def call(**_kw):
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.01)
            live -= 1
            return "ok"

        bounded = base._bounded_call(call)
        assert await asyncio.gather(*(bounded() for _ in range(40))) == ["ok"] * 40
        return peak

    assert asyncio.run(run()) == 3


def test_concurrency_ceiling_accepts_more_than_200():
    from navin.agent.runner import _MAX_INJECTIONS_PER_TURN
    from navin.config.schema import AgentDefaults

    assert AgentDefaults(max_concurrent_subagents=500).max_concurrent_subagents == 500
    assert _MAX_INJECTIONS_PER_TURN >= 1000
    with pytest.raises(Exception):
        AgentDefaults(max_concurrent_subagents=1001)


def test_prune_drops_old_finished_agents_and_keeps_unfinished(tmp_path):
    journal = SubagentJournal(tmp_path / "j.sqlite3")
    for task_id, done in (("old_done", True), ("old_open", False), ("new_done", True)):
        journal.create("s", task_id, {"task_id": task_id, "done": done}, {"task": "t"})
    with journal._lock, journal._db:
        journal._db.execute("UPDATE agents SET updated_at=0 WHERE task_id LIKE 'old%'")
    assert journal.prune() == 1
    assert journal.get("s", "old_done") is None
    assert journal.history("s", "old_done")["events"] == []
    assert journal.get("s", "old_open") and journal.get("s", "new_done")


def test_owner_alive_on_windows_does_not_call_os_kill(monkeypatch):
    import navin.agent.subagent_journal as journal
    import navin.process_runtime as runtime

    def boom(*_args):
        raise OSError(22, "[WinError 87] The parameter is incorrect")

    monkeypatch.setattr(journal.os, "name", "nt")
    monkeypatch.setattr(journal.os, "kill", boom)
    monkeypatch.setattr(runtime, "_windows_process_identity", lambda pid: "t" if pid == 7 else None)
    assert journal.owner_alive(7) is True
    assert journal.owner_alive(8) is False


def test_owner_alive_treats_unexpected_oserror_as_dead(monkeypatch):
    import navin.agent.subagent_journal as journal

    def boom(*_args):
        raise OSError(22, "invalid")

    monkeypatch.setattr(journal.os, "kill", boom)
    assert journal.owner_alive(1234) is False
