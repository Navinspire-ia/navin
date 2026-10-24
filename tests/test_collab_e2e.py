"""Collaboration Mode end to end, on the real AgentRunner and real tools.

Two teammates on two different models work one mission: the developer hands
off, the tester challenges with evidence, the developer answers and fixes, the
tester asks the user a question, both report to the lead. The user interrupts
the developer mid-run. Then the developer is assigned again and starts from its
own earlier report, and its profile is journaled so a restart resumes it as
itself. Everything the chat shows (journal entries, member cards, notifications)
is checked on the outbound bus.
"""

import asyncio
from unittest.mock import Mock

from navin.agent.subagent import SubagentManager
from navin.bus.outbound_events import NotificationEvent, SubagentProgressEvent, TeamMessageEvent
from navin.collab import runtime as collab_runtime
from navin.config.schema import CollabConfig
from navin.providers.base import GenerationSettings, LLMResponse, ToolCallRequest
from navin.utils.llm_runtime import LLMRuntime

SESSION = "websocket:e2e"


class _Outbound:
    def __init__(self):
        self.items = []

    def put_nowait(self, item):
        self.items.append(item)


class _Bus:
    def __init__(self):
        self.published = []
        self.outbound = _Outbound()

    async def publish_inbound(self, msg):
        self.published.append(msg)


def _call(name, **arguments):
    return LLMResponse(
        content=None,
        tool_calls=[ToolCallRequest(id=f"{name}-{len(arguments)}-{id(arguments)}", name=name, arguments=arguments)],
        finish_reason="tool_calls",
    )


def _final(text):
    return LLMResponse(content=text, finish_reason="stop")


class ScriptedTeam:
    """One fake model per teammate, scripted like a real exchange."""

    def __init__(self):
        self.calls = {"Mark": [], "Salma": []}
        self.second_run = False

    def runtime(self, model):
        team = self

        class Provider:
            generation = GenerationSettings()

            async def chat_stream_with_retry(self, **kwargs):
                messages = kwargs["messages"]
                system = str(messages[0]["content"])
                who = "Mark" if "You are Mark" in system else "Salma"
                team.calls[who].append({"model": kwargs.get("model"), "messages": list(messages)})
                return await (team.mark(messages) if who == "Mark" else team.salma(messages))

        return LLMRuntime(provider=Provider(), model=model, generation=GenerationSettings(),
                          context_window_tokens=128000)

    async def mark(self, messages):
        seen = repr(messages)
        turns = len(self.calls["Mark"])
        if self.second_run:
            return _final("Continued from my last report: floats supported now.")
        if turns == 1:
            return _call("team", action="send", target="Salma", intent="handoff",
                         content="add() is in calc.py, ready to test.")
        if "add(2,2) returns 5" in seen and "Fixed: off-by-one removed" not in seen:
            return _call("team", action="send", target="Salma", intent="answer",
                         content="Fixed: off-by-one removed in calc.py line 3.")
        if "Fixed: off-by-one removed" in seen:
            return _final("Done: add() implemented, off-by-one fixed, integers only as the user asked.")
        await asyncio.sleep(0.02)
        if turns > 60:
            return _final("Gave up waiting for the tester.")
        return _call("team", action="members")

    async def salma(self, messages):
        seen = repr(messages)
        turns = len(self.calls["Salma"])
        if turns == 1:
            return _call("team", action="send", target="Mark", intent="challenge",
                         content="add(2,2) returns 5 in test_calc.py::test_add (expected 4).")
        if turns == 2:
            return _call("team", action="send", target="user", intent="question",
                         content="Should add() accept floats?")
        if "Fixed: off-by-one removed" in seen:
            return _final("Verified: 3 tests green after Mark's fix.")
        await asyncio.sleep(0.02)
        if turns > 60:
            return _final("Never got the fix.")
        return _call("team", action="members")


def test_two_teammates_collaborate_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr("navin.agent.subagent._outcomes_dir", lambda: tmp_path / "outcomes")
    team = ScriptedTeam()
    runtimes = {"claude": team.runtime("anthropic/claude-test"), "gpt": team.runtime("openai/gpt-test")}
    collab = CollabConfig(enabled=True)
    for member in collab.members:
        if member.name == "Mark":
            member.model_preset = "claude"
        if member.name == "Salma":
            member.model_preset = "gpt"

    async def run():
        bus = _Bus()
        manager = SubagentManager(workspace=tmp_path, bus=bus, max_tool_result_chars=4000,
                                  max_concurrent_subagents=4)
        common = dict(manager=manager, collab=collab, session_key=SESSION, origin_channel="websocket",
                      origin_chat_id="e2e", parent_runtime=Mock(), resolve_preset=runtimes.__getitem__,
                      mission="calc")
        try:
            await collab_runtime.assign_member(member_name="Mark", task="Implement add(a, b) in calc.py", **common)
            await collab_runtime.assign_member(member_name="Salma", task="Test add() and report", **common)
            mark_id = next(m["task_id"] for m in manager.team_members(SESSION) if m["name"] == "Mark")
            # The user leans in while Mark works.
            manager.send_team_message(SESSION, "user", "Mark", "Integers only, please.")
            await asyncio.wait_for(asyncio.gather(*manager._running_tasks.values()), 30)

            # Each teammate really ran on its own model, with its role's effort.
            assert {c["model"] for c in team.calls["Mark"]} == {"anthropic/claude-test"}
            assert {c["model"] for c in team.calls["Salma"]} == {"openai/gpt-test"}
            assert "Message" in repr(team.calls["Mark"][-1]["messages"]) and "from the user" in repr(
                team.calls["Mark"][-1]["messages"])

            # The journal tells the whole story, in order of what happened.
            feed = manager.team_feed(SESSION)
            story = [(e["kind"], e["sender"]["name"], (e["target"] or {}).get("name"), e.get("status"))
                     for e in feed]
            assert story[0] == ("assign", "Navin", "Mark", "started")
            assert story[1] == ("assign", "Navin", "Salma", "started")
            assert ("user", "You", "Mark", None) in story
            assert ("message", "Mark", "Salma", "handoff") in story
            assert ("message", "Salma", "Mark", "challenge") in story
            assert ("message", "Mark", "Salma", "answer") in story
            assert ("message", "Salma", "You", "question") in story
            assert ("result", "Mark", "Navin", "ok") in story
            assert ("result", "Salma", "Navin", "ok") in story
            assert story.index(("message", "Salma", "Mark", "challenge")) < story.index(
                ("message", "Mark", "Salma", "answer"))

            # The lead is told, with the teammate's name, and continues from there.
            announced = [m for m in bus.published if m.metadata.get("injected_event") == "subagent_result"]
            assert len(announced) == 2
            assert any("Mark (Senior Developer)" in m.content and "off-by-one fixed" in m.content for m in announced)
            assert any("Salma (QA Engineer)" in m.content and "3 tests green" in m.content for m in announced)

            # What the desktop receives: journal frames, faces on cards, notifications.
            events = [item.event for item in bus.outbound.items]
            assert sum(isinstance(e, TeamMessageEvent) for e in events) == len(feed)
            cards = [e for e in events if isinstance(e, SubagentProgressEvent) and e.member]
            assert {e.member["name"] for e in cards} == {"Mark", "Salma"}
            assert {e.model for e in cards if e.member["name"] == "Salma"} == {"openai/gpt-test"}
            notes = [e.title for e in events if isinstance(e, NotificationEvent)]
            assert "Mark finished" in notes and "Salma finished" in notes
            assert "Salma has a question for you" in notes

            # Resume after a restart: the teammate's profile is in the journal.
            record = manager.journal.get(SESSION, mark_id)
            assert record["descriptor"]["member"]["name"] == "Mark"
            assert record["descriptor"]["member"]["model_preset"] == "claude"

            # Assigned again: Mark starts from his own earlier work.
            team.second_run = True
            await collab_runtime.assign_member(member_name="Mark", task="Now support floats", **common)
            await asyncio.wait_for(asyncio.gather(*manager._running_tasks.values()), 30)
            first_user = team.calls["Mark"][-1]["messages"][1]["content"]
            assert "Your last report" in first_user and "off-by-one fixed" in first_user
            assert "Your new task:\nNow support floats" in first_user
            assert manager.team_feed(SESSION)[-1]["content"].startswith("Continued from my last report")
            assert not manager._task_members and not manager._running_tasks
        finally:
            if manager._watchdog_task:
                manager._watchdog_task.cancel()
                await asyncio.gather(manager._watchdog_task, return_exceptions=True)

    asyncio.run(run())


def test_interrupted_teammate_resumes_as_itself(tmp_path, monkeypatch):
    monkeypatch.setattr("navin.agent.subagent._outcomes_dir", lambda: tmp_path / "outcomes")

    async def run():
        bus = _Bus()
        manager = SubagentManager(workspace=tmp_path, bus=bus, max_tool_result_chars=4000)
        entered = asyncio.Event()
        specs = []

        class WaitingRunner:
            async def run(self, spec):
                specs.append(spec)
                entered.set()
                await asyncio.Event().wait()

        manager.runner = WaitingRunner()
        runtime = ScriptedTeam().runtime("anthropic/claude-test")
        collab = CollabConfig(enabled=True)
        next(m for m in collab.members if m.name == "Ethan").sandbox = "readonly"
        try:
            await collab_runtime.assign_member(
                manager=manager, collab=collab, member_name="Ethan",
                task="Audit auth", session_key=SESSION, origin_channel="websocket",
                origin_chat_id="e2e", parent_runtime=runtime,
            )
            await asyncio.wait_for(entered.wait(), 10)
            task_id = manager.team_members(SESSION)[0]["task_id"]
            manager.send_team_message(SESSION, "user", "Ethan", "Focus on the refresh tokens")
            await manager.flush_journal()
            await manager.cancel_by_session(SESSION)
            await manager.flush_journal()
            assert manager.agent_history(SESSION, task_id)["agent"]["recoverable"]

            entered.clear()
            assert "resumed" in await manager.recover_agent(SESSION, task_id, runtime=runtime)
            await asyncio.wait_for(entered.wait(), 10)
            # Same identity, same sandbox (read-only auditor), same unread message.
            assert manager.member_identity(task_id)["name"] == "Ethan"
            assert "write_file" in specs[-1].denied_tools
            assert "security_scan" in specs[-1].allowed_tools
            assert manager._agent_mailboxes[task_id][0]["content"] == "Focus on the refresh tokens"
            cards = [i.event for i in bus.outbound.items if isinstance(i.event, SubagentProgressEvent)]
            assert cards[-1].member and cards[-1].member["name"] == "Ethan"
        finally:
            await manager.cancel_by_session(SESSION)
            manager._watchdog_task.cancel()
            await asyncio.gather(manager._watchdog_task, return_exceptions=True)

    asyncio.run(run())


def test_lead_sees_this_sessions_team_each_turn():
    from types import SimpleNamespace

    from navin.agent.tools.collab import CollabTool

    sessions = Mock()
    sessions.get_or_create.side_effect = lambda key: SimpleNamespace(
        metadata={"collab_team": {"websocket:a": "sales", "websocket:b": "off"}.get(key)}
        if key != "websocket:c" else {}
    )

    async def run(collab, key, metadata=None):
        import navin.agent.tools.collab as module

        module._load_collab = lambda: collab
        provide = CollabTool(Mock(), None, sessions).runtime_context_provider()
        return await provide(SimpleNamespace(session_key=key, metadata=metadata or {}))

    on = CollabConfig(enabled=True)
    block = asyncio.run(run(on, "websocket:a"))
    assert "your team (Sales)" in block.content and "Jack - Sales Developer" in block.content
    assert "- Mark -" not in block.content and "\"Jack, ...\"" in block.content
    assert asyncio.run(run(on, "websocket:b")) is None          # this chat picked no team
    default = asyncio.run(run(on, "websocket:c"))               # never picked: default team
    assert "your team (Dev)" in default.content and "drive the next step" in default.content
    assert asyncio.run(run(on, "websocket:a", {"subagent": True})) is None
    assert asyncio.run(run(CollabConfig(enabled=False), "websocket:a")) is None
