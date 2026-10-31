"""Collaboration Mode: a named team on the subagent engine."""

import asyncio
import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from navin.agent.subagent import _COLLAB_ROUND_IDLE_S, SubagentManager
from navin.agent.tools.collab import CollabTool
from navin.agent.tools.context import RequestContext, request_context
from navin.collab import runtime as collab_runtime
from navin.collab.team import (
    WRITE_TOOLS,
    allowed_tools_for,
    default_team,
    denied_tools_for,
    find_member,
    member_prompt,
    roster_prompt,
)
from navin.config.schema import CollabConfig
from tests.test_subagent_concurrency import _Bus, _GatedRunner


class _Outbound:
    def __init__(self):
        self.items = []

    def put_nowait(self, item):
        self.items.append(item)


class _RecordingRunner(_GatedRunner):
    def __init__(self):
        super().__init__()
        self.specs = []

    async def run(self, spec):
        self.specs.append(spec)
        return await super().run(spec)


def _manager(tmp_path, monkeypatch):
    monkeypatch.setattr("navin.agent.subagent._outcomes_dir", lambda: tmp_path / "outcomes")
    bus = _Bus()
    bus.outbound = _Outbound()
    manager = SubagentManager(workspace=tmp_path, bus=bus, max_tool_result_chars=4000,
                              max_concurrent_subagents=4)
    manager.runner = _RecordingRunner()
    manager._build_tools = Mock()
    manager._build_subagent_prompt = Mock(return_value="Agent")
    return manager


async def _until(predicate, timeout=5.0):
    """Teammate prompts preload skills from disk: wait for the run to start."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "teammate run did not start"
        await asyncio.sleep(0.02)


async def _stop(manager, session):
    manager.runner.gate.set()
    await asyncio.sleep(0.05)
    await manager.cancel_by_session(session)
    manager._watchdog_task.cancel()
    await asyncio.gather(manager._watchdog_task, return_exceptions=True)


def test_default_team_is_complete_and_editable():
    team = default_team()
    names = [m["name"] for m in team]
    assert names[:5] == ["Michel", "Mark", "Salma", "Ethan", "Grace"]
    for member in team:
        assert member["prompt"] and member["tools"] and member["email"].endswith("@team.navin.local")
        assert "team" in member["tools"]
    mark = find_member(team, "@mark")
    assert mark["role"] == "developer" and "skills" in mark and "fullstack-dev" in mark["skills"]
    assert find_member(team, "Salma, run the tests")["role"] == "tester"
    # A fresh config carries the same team, off until the user turns it on.
    config = CollabConfig()
    assert not config.enabled and [m.name for m in config.members] == names


def test_sandbox_and_tools_shape_what_a_teammate_may_call():
    team = default_team()
    nassima = find_member(team, "Ethan")
    # Everyone gets the full toolbox and the shared project by default.
    assert nassima["sandbox"] == "workspace" and denied_tools_for(nassima) == frozenset()
    allowed = allowed_tools_for(nassima)
    assert {"security_scan", "exec", "write_file", "spawn", "team"} <= allowed
    for name in ("Mark", "Daniel", "Lucas"):  # dev, devops, infra: the whole general list
        assert {"exec", "git", "test_run", "browser", "debug_repair", "start_app", "mobile", "appfactory"} <= allowed_tools_for(find_member(team, name))
    # The Ops engine goes to the roles that run infrastructure, not to a copywriter.
    for name in ("Daniel", "Lucas"):
        assert "infra" in allowed_tools_for(find_member(team, name))
    assert "infra" not in allowed_tools_for(find_member(team, "Jack"))
    assert {"campaigns", "crm", "leads", "scrape"} <= allowed_tools_for(find_member(team, "Jack"))
    # Read-only stays available when the user picks it.
    assert denied_tools_for({**nassima, "sandbox": "readonly"}) == WRITE_TOOLS
    assert allowed_tools_for({"tools": []}) is None


def test_a_saved_team_gets_the_new_tools_on_load():
    """A team saved before v4 learns mobile, appfactory and (for ops roles) infra; nothing is removed."""
    from navin.config.schema import COLLAB_TOOLS_VERSION

    saved = {
        "tools_version": 3,
        "members": [
            {"id": "mark", "name": "Mark", "role": "developer", "tools": ["read_file", "my_plugin_tool"]},
            {"id": "daniel", "name": "Daniel", "role": "devops", "tools": ["exec"]},
            {"id": "jack", "name": "Jack", "role": "sales", "tools": ["crm"]},
        ],
    }
    collab = CollabConfig(**saved)
    assert collab.tools_version == COLLAB_TOOLS_VERSION == 4
    by_name = {member.name: list(member.tools) for member in collab.members}
    assert {"read_file", "my_plugin_tool", "mobile", "appfactory"} <= set(by_name["Mark"])
    assert "infra" not in by_name["Mark"]
    assert {"exec", "infra", "appfactory"} <= set(by_name["Daniel"])
    assert "infra" not in by_name["Jack"] and {"crm", "mobile"} <= set(by_name["Jack"])
    # An up-to-date team is left exactly as saved.
    kept = CollabConfig(tools_version=4, members=[{"id": "zed", "name": "Zed", "role": "custom", "tools": ["read_file"]}])
    assert list(kept.members[0].tools) == ["read_file"]


def test_member_prompt_and_roster_name_the_team():
    team = default_team()
    prompt = member_prompt(team[1], teammates=team, collab=CollabConfig())
    assert "You are Mark, Senior Developer" in prompt
    assert "Salma: QA Engineer" in prompt
    assert 'user.email="mark@team.navin.local"' in prompt
    assert "human approval" in prompt
    roster = roster_prompt(CollabConfig(enabled=True))
    assert "collab(action=\"assign\"" in roster and "- Mark - Senior Developer" in roster
    assert "Karim" not in roster  # disabled by default


def test_assign_runs_the_profile_and_posts_a_live_team_conversation(tmp_path, monkeypatch):
    async def run():
        manager = _manager(tmp_path, monkeypatch)
        collab = CollabConfig(enabled=True, max_rounds=2, budget_tokens=0)
        runtime = Mock()
        runtime.with_generation_overrides = Mock(return_value=runtime)
        try:
            detail = await collab_runtime.assign_member(
                manager=manager, collab=collab, member_name="mark", task="Build the auth module",
                session_key="websocket:c1", origin_channel="websocket", origin_chat_id="c1",
                parent_runtime=runtime, mission="auth",
            )
            assert "started" in detail
            runtime.with_generation_overrides.assert_called_with(reasoning_effort="medium")
            await collab_runtime.assign_member(
                manager=manager, collab=collab, member_name="Salma", task="Test it",
                session_key="websocket:c1", origin_channel="websocket", origin_chat_id="c1",
                parent_runtime=runtime, mission="auth",
            )
            await _until(lambda: len(manager.runner.specs) == 2)
            spec_mark, spec_salma = manager.runner.specs
            assert "exec" in spec_mark.allowed_tools and "team" in spec_mark.allowed_tools

            members = {m["name"]: m["task_id"] for m in manager.team_members("websocket:c1")}
            assert set(members) == {"Mark", "Salma"}
            # Teammates address each other by name; the user can interrupt one.
            manager.send_team_message("websocket:c1", members["Salma"], "Mark", "2 tests fail")
            manager.send_team_message("websocket:c1", "user", "mark", "Use argon2")
            inbox = manager.drain_team_messages("websocket:c1", members["Mark"])
            assert [m["content"] for m in inbox] == ["2 tests fail", "Use argon2"]
            assert inbox[0]["sender_name"] == "Salma"

            kinds = [(e["kind"], e["sender"]["name"], (e["target"] or {}).get("name"))
                     for e in manager.team_feed("websocket:c1")]
            assert kinds == [("assign", "Navin", "Mark"), ("assign", "Navin", "Salma"),
                             ("message", "Salma", "Mark"), ("user", "You", "Mark")]
            events = [item.event for item in manager.bus.outbound.items]
            team_events = [e for e in events if type(e).__name__ == "TeamMessageEvent"]
            assert len(team_events) == 4
            progress = [e for e in events if type(e).__name__ == "SubagentProgressEvent"]
            assert any(e.member and e.member["name"] == "Mark" for e in progress)

            await collab_runtime.assign_member(
                manager=manager, collab=collab, member_name="Mark", task="Fix",
                session_key="websocket:c1", origin_channel="websocket", origin_chat_id="c1",
                parent_runtime=runtime, mission="auth",
            )
            with pytest.raises(collab_runtime.CollabError, match="Round limit"):
                await collab_runtime.assign_member(
                    manager=manager, collab=collab, member_name="Mark", task="Fix again",
                    session_key="websocket:c1", origin_channel="websocket", origin_chat_id="c1",
                    parent_runtime=runtime, mission="auth",
                )
            with pytest.raises(collab_runtime.CollabError, match="No teammate"):
                await collab_runtime.assign_member(
                    manager=manager, collab=collab, member_name="Zed", task="x",
                    session_key="websocket:c1", origin_channel="websocket", origin_chat_id="c1",
                    parent_runtime=runtime,
                )
        finally:
            await _stop(manager, "websocket:c1")
        assert not manager._task_members

    asyncio.run(run())


def test_plan_turn_assigns_read_only_teammates(tmp_path, monkeypatch):
    async def run():
        manager = _manager(tmp_path, monkeypatch)
        try:
            await collab_runtime.assign_member(
                manager=manager, collab=CollabConfig(enabled=True), member_name="Mark",
                task="Plan the change", session_key="cli:p", origin_channel="cli",
                origin_chat_id="p", parent_runtime=Mock(), composer_mode="plan",
            )
            await _until(lambda: manager.runner.specs)
            spec = manager.runner.specs[0]
            assert {"write_file", "exec", "apply_patch"} <= set(spec.denied_tools)
        finally:
            await _stop(manager, "cli:p")

    asyncio.run(run())


def test_collab_tool_refuses_when_mode_is_off_and_lists_the_team(tmp_path, monkeypatch):
    async def run():
        manager = _manager(tmp_path, monkeypatch)
        tool = CollabTool(manager)
        ctx = RequestContext(channel="cli", chat_id="t", session_key="cli:t", runtime=Mock())
        monkeypatch.setattr("navin.agent.tools.collab._load_collab", lambda: CollabConfig())
        with request_context(ctx):
            assert "off" in await tool.execute(action="assign", member="Mark", task="x")
        monkeypatch.setattr("navin.agent.tools.collab._load_collab",
                            lambda: CollabConfig(enabled=True))
        with request_context(ctx):
            roster = json.loads(await tool.execute(action="roster"))
            assert [r["name"] for r in roster["team"]][:2] == ["Michel", "Mark"]
            assert "No teammate is running" in await tool.execute(action="message", member="all",
                                                                  content="Stand-up")
        sub = RequestContext(channel="cli", chat_id="t", session_key="cli:t", runtime=Mock(),
                             metadata={"subagent": True})
        with request_context(sub):
            assert "only the lead" in await tool.execute(action="assign", member="Mark", task="x")
            assert "signs as the lead" in await tool.execute(action="email", subject="s", content="c")
        # Broadcast: one stopped teammate does not hide the delivery to the others.
        from collections import deque

        from navin.agent.subagent import SubagentStatus

        for tid, phase in (("a1", "running"), ("a2", "done")):
            manager._agent_sessions[tid] = "cli:t"
            manager._agent_mailboxes[tid] = deque()
            manager._task_statuses[tid] = SubagentStatus(
                task_id=tid, label=tid, task_description="", started_at=0.0, phase=phase)
            manager._task_members[tid] = {"id": tid, "name": tid.upper()}
        manager.team_members = lambda s: [
            {"task_id": "a1", "name": "A1", "phase": "running"},
            {"task_id": "a2", "name": "A2", "phase": "running"}]
        with request_context(ctx):
            out = await tool.execute(action="message", member="all", content="Stand-up")
        assert "queued for a1" in out and "Not delivered to a2" in out
        assert len(manager._agent_mailboxes["a1"]) == 1
        if manager._watchdog_task:
            manager._watchdog_task.cancel()

    asyncio.run(run())


def test_settings_save_validates_and_resets_the_team(tmp_path, monkeypatch):
    from navin.config.loader import load_config, set_config_path
    from navin.webui import collab_api
    from navin.webui.settings_api import WebUISettingsError

    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    set_config_path(path)
    monkeypatch.setattr(collab_api, "_skills_catalog", lambda workspace: [])
    try:
        payload = collab_api.collab_payload()
        members = payload["collab"]["members"]
        assert payload["catalog"]["presets"][0]["name"] == "default"
        members[1]["name"] = "Marc"
        members[1]["prompt"] = "Toujours en TypeScript strict."
        members[1]["tools"] = ["read_file", "bogus_tool"]
        members.append({"name": "Nour", "role": "tester", "email": "nour@example.com"})
        saved = collab_api.update_collab({"enabled": True, "members": members, "maxRounds": 99})
        assert saved["collab"]["enabled"] and saved["collab"]["max_rounds"] == 20
        marc = saved["collab"]["members"][1]
        assert marc["name"] == "Marc" and marc["tools"] == ["read_file"]
        assert saved["collab"]["members"][-1]["id"] == "nour"
        assert load_config().collab.members[1].prompt == "Toujours en TypeScript strict."
        assert collab_runtime.current_collab().enabled
        # The chat switch: off then on again, read back at once each time.
        import os

        stat = path.stat()
        assert collab_api.update_collab({"enabled": False})["collab"]["enabled"] is False
        # Same timestamp as the previous save (a coarse clock): still seen.
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert collab_runtime.current_collab().enabled is False
        assert collab_api.update_collab({"enabled": True})["collab"]["enabled"] is True
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert collab_runtime.current_collab().enabled is True

        with pytest.raises(WebUISettingsError, match="share a name"):
            collab_api.update_collab({"members": [{"name": "A"}, {"name": "a"}]})
        with pytest.raises(WebUISettingsError, match="email"):
            collab_api.update_collab({"members": [{"name": "A", "email": "nope"}]})
        with pytest.raises(WebUISettingsError, match="unknown model"):
            collab_api.update_collab({"members": [{"name": "A", "model_preset": "ghost"}]})

        reset = collab_api.update_collab({"reset": True})
        assert reset["collab"]["members"][1]["name"] == "Mark" and reset["collab"]["enabled"]
    finally:
        set_config_path(None)


def test_settings_route_reads_the_chunked_body():
    from navin.webui.file_preview import file_body_from_headers

    body = json.dumps({"enabled": True, "members": [{"name": "Salma é"}]})
    headers = {"X-Navin-File-Body-0": base64.b64encode(body.encode()).decode()}
    assert json.loads(file_body_from_headers(SimpleNamespace(get=headers.get)))["members"][0]["name"] == "Salma é"


def test_debate_caps_continuity_and_intents(tmp_path, monkeypatch):
    async def run():
        manager = _manager(tmp_path, monkeypatch)
        collab = CollabConfig(enabled=True)
        runtime = Mock()
        runtime.with_generation_overrides = Mock(return_value=runtime)
        kwargs = dict(manager=manager, collab=collab, session_key="cli:d", origin_channel="cli",
                      origin_chat_id="d", parent_runtime=runtime, mission="m")
        try:
            await collab_runtime.assign_member(member_name="Mark", task="Build", **kwargs)
            await collab_runtime.assign_member(member_name="Salma", task="Test", **kwargs)
            ids = {m["name"]: m["task_id"] for m in manager.team_members("cli:d")}
            manager.send_team_message("cli:d", ids["Salma"], "Mark", "Login returns 500", intent="challenge")
            entry = manager.team_feed("cli:d")[-1]
            assert entry["status"] == "challenge"
            inbox = manager.drain_team_messages("cli:d", ids["Mark"])
            assert inbox[0]["intent"] == "challenge"
            for index in range(23):
                manager.send_team_message("cli:d", ids["Mark"], "Salma", f"point {index}", intent="answer")
                manager.drain_team_messages("cli:d", ids["Salma"])
            with pytest.raises(ValueError, match="debate already took"):
                manager.send_team_message("cli:d", ids["Salma"], "Mark", "again")
            # The lead and the user are never capped.
            manager.send_team_message("cli:d", "user", "Mark", "Stop, use argon2")

            manager.post_team_feed("cli:d", "result", ids["Mark"], "Auth done, 12 tests green",
                                   target="parent", task_id=ids["Mark"], status="ok")
            history = manager.member_history("cli:d", "mark")
            assert "Auth done, 12 tests green" in history and "[answer]" in history and "use argon2" in history
            payload = collab_runtime.member_payload(find_member(collab.members, "Mark"), collab)
            assert payload["_teammates"]
        finally:
            await _stop(manager, "cli:d")

    asyncio.run(run())


def test_session_team_picks_members_and_off():
    collab = CollabConfig(enabled=True)
    team, members = collab_runtime.session_team(collab, None)
    assert team.id == "dev" and [m.name for m in members][:2] == ["Michel", "Mark"]
    assert "Karim" not in [m.name for m in members]  # disabled teammate
    team, members = collab_runtime.session_team(collab, "sales")
    assert [m.name for m in members] == ["Jack", "Nora", "Lea"]
    assert collab_runtime.session_team(collab, "off") == (None, [])
    with pytest.raises(collab_runtime.CollabError, match="No teammate named 'Mark'"):
        collab_runtime.resolve_member(collab, "Mark", members)
    summary = collab_runtime.team_summary(collab, "infra")
    assert summary["team_name"] == "Infra" and summary["members"][0]["name"] == "Lucas"


class _FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        self.user = user

    def send_message(self, msg):
        _FakeSMTP.sent.append(msg)


def test_teammate_emails_the_manager_and_the_journal_shows_it(tmp_path, monkeypatch):
    from navin.agent.tools.team import TeamTool
    from navin.collab import mail

    _FakeSMTP.sent = []
    mail._sent.clear()
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    monkeypatch.setattr(mail, "_smtp_config", lambda: {
        "host": "smtp.example.com", "port": 587, "user": "bot@example.com", "password": "x",
        "from": "bot@example.com", "tls": True, "ssl": False,
    })
    collab = CollabConfig(enabled=True, manager_name="Aymen", manager_email="boss@example.com")
    monkeypatch.setattr("navin.collab.runtime.current_collab", lambda: collab)

    async def run():
        manager = _manager(tmp_path, monkeypatch)
        try:
            await collab_runtime.assign_member(
                manager=manager, collab=collab, member_name="Mark", task="Ship it",
                session_key="cli:e", origin_channel="cli", origin_chat_id="e",
                parent_runtime=Mock(with_generation_overrides=Mock(return_value=Mock())),
            )
            mark = manager.team_members("cli:e")[0]["task_id"]
            tool = TeamTool(manager)
            with request_context(RequestContext(channel="cli", chat_id="e", session_key="cli:e",
                                                metadata={"subagent_task_id": mark})):
                result = await tool.execute(action="email", subject="Auth shipped", content="All green.")
                assert "boss@example.com" in result
                asked = await tool.execute(action="send", target="user", intent="question",
                                           content="Argon2 or bcrypt?")
                assert "team journal" in asked
            msg = _FakeSMTP.sent[0]
            assert msg["To"] == "boss@example.com" and "Mark (Senior Developer) via Navin" in msg["From"]
            assert "All green." in msg.get_content() and "mark@team.navin.local" in msg.get_content()
            kinds = [(e["kind"], (e["target"] or {}).get("name")) for e in manager.team_feed("cli:e")]
            assert ("email", "Aymen") in kinds and ("message", "You") in kinds
            for _ in range(mail.MAX_PER_SENDER_PER_HOUR - 1):
                mail.send_manager_email(collab, sender={"id": "mark", "name": "Mark"}, subject="s",
                                        body="b", session_key="cli:e")
            with pytest.raises(mail.CollabMailError, match="limit"):
                mail.send_manager_email(collab, sender={"id": "mark", "name": "Mark"}, subject="s",
                                        body="b", session_key="cli:e")
        finally:
            await _stop(manager, "cli:e")

    asyncio.run(run())


def test_email_needs_a_manager_and_smtp(monkeypatch):
    from navin.collab import mail

    with pytest.raises(mail.CollabMailError, match="No manager email"):
        mail.send_manager_email(CollabConfig(), sender={"name": "Mark"}, subject="s", body="b")
    monkeypatch.setattr(mail, "_smtp_config", lambda: {"host": "", "user": "", "password": ""})
    with pytest.raises(mail.CollabMailError, match="not configured"):
        mail.send_manager_email(CollabConfig(manager_email="a@b.co"), sender={"name": "M"},
                                subject="s", body="b")


def test_email_to_several_contacts_and_the_allowlist(monkeypatch):
    from navin.collab import mail

    _FakeSMTP.sent = []
    mail._sent.clear()
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    monkeypatch.setattr(mail, "_smtp_config", lambda: {
        "host": "smtp.example.com", "port": 587, "user": "bot@example.com", "password": "x",
        "from": "bot@example.com", "tls": True, "ssl": False,
    })
    collab = CollabConfig(manager_name="Aymen", manager_email="boss@example.com",
                          email_contacts=[{"name": "Sara", "email": "sara@example.com"}])
    sent = mail.send_team_email(collab, sender={"name": "Lea", "title": "Marketing Lead"},
                                subject="Launch", body="Plan attached.", to="Aymen, sara")
    assert [r["email"] for r in sent] == ["boss@example.com", "sara@example.com"]
    assert _FakeSMTP.sent[-1]["To"] == "boss@example.com, sara@example.com"
    with pytest.raises(mail.CollabMailError, match="not in the team's contacts"):
        mail.send_team_email(collab, sender={"name": "Lea"}, subject="s", body="b", to="x@other.org")
    collab.email_allow_any = True
    sent = mail.send_team_email(collab, sender={"name": "Lea"}, subject="s", body="b", to="x@other.org")
    assert sent[0]["role"] == "external"
    with pytest.raises(mail.CollabMailError, match="Unknown recipient"):
        mail.send_team_email(collab, sender={"name": "Lea"}, subject="s", body="b", to="Nobody")


def test_helper_subagents_journal_persists_and_rounds_reset(tmp_path, monkeypatch):
    async def run():
        manager = _manager(tmp_path, monkeypatch)
        collab = CollabConfig(enabled=True, max_rounds=1)
        runtime = Mock(with_generation_overrides=Mock(return_value=Mock()))
        try:
            await collab_runtime.assign_member(
                manager=manager, collab=collab, member_name="Mark", task="Build", session_key="cli:h",
                origin_channel="cli", origin_chat_id="h", parent_runtime=runtime, mission="m1",
            )
            mark = manager.team_members("cli:h")[0]["task_id"]
            # Mark delegates to a helper: the journal shows it, and its report back.
            await manager.spawn(task="Write the migration", label="helper", runtime=Mock(),
                                session_key="cli:h", parent_task_id=mark, depth=2)
            helper = next(m["task_id"] for m in manager.team_members("cli:h") if m["task_id"] != mark)
            await manager._announce_result(helper, "helper", "Write the migration", "Migration 0042 added",
                                           {"channel": "cli", "chat_id": "h", "session_key": "cli:h"}, "ok")
            story = [(e["kind"], e["sender"]["name"], (e["target"] or {}).get("name")) for e in manager.team_feed("cli:h")]
            assert ("assign", "Mark", "helper") in story and ("result", "helper", "Mark") in story
            notes = [i.event.title for i in manager.bus.outbound.items if type(i.event).__name__ == "NotificationEvent"]
            assert not any("helper" in n for n in notes)  # helpers do not ping the user

            # Round limit per mission, then the idle reset frees the teammate again.
            with pytest.raises(collab_runtime.CollabError, match="Round limit"):
                await collab_runtime.assign_member(
                    manager=manager, collab=collab, member_name="Mark", task="Again", session_key="cli:h",
                    origin_channel="cli", origin_chat_id="h", parent_runtime=runtime, mission="m1",
                )
            key = ("cli:h", "m1", "mark")
            count, _ = manager._collab_rounds[key]
            manager._collab_rounds[key] = (
                count, time.monotonic() - _COLLAB_ROUND_IDLE_S - 1
            )  # two hours idle
            assert manager.collab_round("cli:h", "m1", "mark") == 1
        finally:
            await _stop(manager, "cli:h")
        # After a restart the journal (and so each teammate's continuity) is still there.
        fresh = SubagentManager(workspace=tmp_path, bus=manager.bus, max_tool_result_chars=1000)
        reloaded = fresh.team_feed("cli:h")
        assert [e["entry_id"] for e in reloaded] == [e["entry_id"] for e in manager.team_feed("cli:h")]
        assert fresh.member_history("cli:h", "mark").startswith("# Continuity")

    asyncio.run(run())


def test_rounds_follow_the_user_request_without_a_mission_name(tmp_path, monkeypatch):
    async def run():
        manager = _manager(tmp_path, monkeypatch)
        tool = CollabTool(manager)
        collab = CollabConfig(enabled=True, max_rounds=1)
        monkeypatch.setattr("navin.agent.tools.collab._load_collab", lambda: collab)
        runtime = Mock(with_generation_overrides=Mock(return_value=Mock()))
        try:
            first = RequestContext(channel="cli", chat_id="r", session_key="cli:r", runtime=runtime, message_id="m-1")
            with request_context(first):
                assert "started" in await tool.execute(action="assign", member="Mark", task="Task A")
                assert "Round limit" in await tool.execute(action="assign", member="Mark", task="Task A again")
            # The report of request m-1 comes back injected: same request, same count.
            follow = RequestContext(channel="cli", chat_id="r", session_key="cli:r", runtime=runtime,
                                    metadata={"origin_message_id": "m-1"})
            with request_context(follow):
                assert "Round limit" in await tool.execute(action="assign", member="Mark", task="Fix A")
            # A new user request starts fresh.
            second = RequestContext(channel="cli", chat_id="r", session_key="cli:r", runtime=runtime, message_id="m-2")
            with request_context(second):
                assert "started" in await tool.execute(action="assign", member="Mark", task="Task B")
        finally:
            await _stop(manager, "cli:r")

    asyncio.run(run())
