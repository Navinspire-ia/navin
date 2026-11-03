"""Remote channels: prefs, approval codes over WhatsApp/Telegram/Teams/email."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from navin.agent import remote_channels as rc


@pytest.fixture(autouse=True)
def _tmp_prefs(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "_path", lambda: tmp_path / "channels.json")
    rc._CODES.clear()
    rc._CACHE.clear()


def test_prefs_are_read_once_and_refreshed_by_save(tmp_path):
    assert not rc.load_prefs()["telegram"]["saved"]
    (tmp_path / "channels.json").write_text('{"telegram": {"notify": true, "to": "7"}}')
    assert not rc.load_prefs()["telegram"]["saved"]  # cached: the engine loop does not re-read the disk
    rc.save_prefs("telegram", approve=True)
    row = rc.load_prefs()["telegram"]
    assert row["notify"] and row["approve"] and row["to"] == "7"  # save merges what is on disk
    rc.load_prefs()["telegram"]["to"] = "changed"
    assert rc.load_prefs()["telegram"]["to"] == "7"  # callers get copies


def test_prefs_default_off_and_saved():
    assert all(not r["notify"] and not r["approve"] for r in rc.status()["channels"])
    rc.save_prefs("msteams", notify=True, to="conv-1")
    row = next(r for r in rc.status()["channels"] if r["channel"] == "teams")
    assert row["notify"] and row["to"] == "conv-1"
    with pytest.raises(ValueError):
        rc.save_prefs("fax", notify=True)


def test_approval_reply_resolves_only_from_known_chat():
    rc.save_prefs("whatsapp", approve=True, to="+33 6 12 34 56 78")
    code = rc._new_code("req-1")
    rc._CODES[code]["chats"].add(("whatsapp", "+33 6 12 34 56 78"))
    stranger = SimpleNamespace(channel="whatsapp", chat_id="999", sender_id="999", content=f"OUI {code}")
    assert rc.parse_reply(stranger) is None
    me = SimpleNamespace(channel="whatsapp", chat_id="33612345678@s.whatsapp.net", sender_id="", content=f"toujours {code}")
    assert rc.parse_reply(me) == ("req-1", True, True)
    assert rc.parse_reply(me) is None  # one-shot


def test_approval_asked_in_the_origin_chat_is_not_sent_twice(monkeypatch):
    """A prompt from Telegram: the loop asks there; the panel only adds the other chats."""
    import navin.bus.alerts as alerts

    delivered = []
    monkeypatch.setattr(alerts, "deliver_alert", lambda store, **kw: delivered.append(store.load_profile()["channels"]) or {})
    rc.save_prefs("telegram", approve=True, to="42")
    rc.save_prefs("whatsapp", approve=True, to="+33 6 12 34 56 78")
    sent = []

    async def publish_outbound(msg):
        sent.append(msg)

    bus = SimpleNamespace(publish_outbound=publish_outbound)
    request = SimpleNamespace(action="deploy", tool="exec", reason="", detail="", consequence="", scope="")
    route = {"channel": "telegram", "chat_id": "42"}
    asyncio.run(rc.publish_approval(bus, "req-9", request, route, origin_asked=True))
    assert sent == []  # the origin chat already has the loop's message
    assert delivered == [{"whatsapp": True, "whatsapp_to": "+33 6 12 34 56 78"}]
    (code,) = rc._CODES
    assert rc._CODES[code]["chats"] == {("whatsapp", "+33 6 12 34 56 78")}

    rc._CODES.clear()
    delivered.clear()
    rc.save_prefs("whatsapp", approve=False)
    asyncio.run(rc.publish_approval(bus, "req-10", request, route, origin_asked=True))
    assert sent == [] and delivered == [] and rc._CODES == {}  # nothing else to ask: no code, no message

    asyncio.run(rc.publish_approval(bus, "req-11", request, {"channel": "msteams", "chat_id": "conv-1"}))
    assert len(sent) == 1 and sent[0].chat_id == "conv-1" and "OUI " in sent[0].content


def test_handle_remote_message_resolves_broker_and_blocks_disabled_chat():
    resolved = {}
    broker = SimpleNamespace(resolve=lambda rid, **kw: resolved.update(rid=rid, **kw) or True)
    sent = []

    async def publish_outbound(msg):
        sent.append(msg)

    state = SimpleNamespace(approvals=broker, bus=SimpleNamespace(publish_outbound=publish_outbound))
    code = rc._new_code("req-2")
    rc._CODES[code]["chats"].add(("telegram", "42"))
    msg = SimpleNamespace(channel="telegram", chat_id="42", sender_id="42", content=f"non {code}")
    assert asyncio.run(rc.handle_remote_message(state, msg))
    assert resolved == {"rid": "req-2", "allowed": False, "remember": False}
    assert sent and sent[0].content == "Refuse."

    prompt = SimpleNamespace(channel="telegram", chat_id="42", sender_id="42", content="fais le build")
    assert not asyncio.run(rc.handle_remote_message(state, prompt))
    rc.save_prefs("telegram", chat=False)
    assert asyncio.run(rc.handle_remote_message(state, prompt))
    assert not rc.can_answer("telegram")


def test_remote_answer_check_only_tolerates_a_disk_error(monkeypatch):
    """An unreadable runtime folder means that chat cannot answer; a bug is not hidden."""
    from navin.agent import approval

    rc.save_prefs("telegram", chat=True)
    assert approval._remote_can_answer("telegram") is True

    def disk_error(_channel):
        raise PermissionError("runtime folder not writable")

    monkeypatch.setattr(rc, "can_answer", disk_error)
    assert approval._remote_can_answer("telegram") is False

    def bug(_channel):
        raise KeyError("telegram")

    monkeypatch.setattr(rc, "can_answer", bug)
    with pytest.raises(KeyError):
        approval._remote_can_answer("telegram")


# -- "When it finishes, send me a Teams": what reaches a notify channel --------


def _alerts_ready(monkeypatch):
    """A configured Teams transport and a gateway forwarder, as the gateway installs them."""
    import navin.bus.alerts as alerts
    import navin.bus.notify as notify_mod

    monkeypatch.setattr(alerts, "_cfg", lambda name: {"enabled": True, "app_id": "app", "app_password": "pw"})
    monkeypatch.setattr(notify_mod, "_remote_forwarder", rc.forward_notification)
    return alerts


async def _teams_messages(bus, count, timeout=5.0):
    """Drain the outbound queue until ``count`` Teams alerts arrived (the copy runs in a thread)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    teams, other = [], []
    while len(teams) < count:
        assert loop.time() < deadline, f"only {len(teams)} Teams alerts arrived"
        while not bus.outbound.empty():
            item = bus.outbound.get_nowait()
            (teams if item.channel == "msteams" else other).append(item)
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.1)
    while not bus.outbound.empty():
        item = bus.outbound.get_nowait()
        (teams if item.channel == "msteams" else other).append(item)
    return teams, other


def test_teammate_finished_stopped_and_question_reach_the_teams_notify_chat(tmp_path, monkeypatch):
    from collections import deque

    from navin.agent.subagent import SubagentManager, SubagentStatus
    from navin.bus.queue import MessageBus
    from navin.collab.team import default_team, find_member

    alerts = _alerts_ready(monkeypatch)
    monkeypatch.setattr("navin.agent.subagent._outcomes_dir", lambda: tmp_path / "outcomes")
    rc.save_prefs("msteams", notify=True, to="conv-1")

    async def run():
        bus = MessageBus()
        alerts.bind_alert_runtime(bus, asyncio.get_running_loop(), {})
        manager = SubagentManager(workspace=tmp_path, bus=bus, max_tool_result_chars=4000)
        session = "websocket:desk"
        team = default_team()
        for task_id, member in (("m1", find_member(team, "Mark")), ("s1", find_member(team, "Salma")),
                                ("h1", None)):
            manager._agent_sessions[task_id] = session
            manager._agent_mailboxes[task_id] = deque()
            manager._task_statuses[task_id] = SubagentStatus(
                task_id=task_id, label=task_id, task_description="", started_at=0.0, phase="running")
            if member:
                manager._task_members[task_id] = dict(member)
        try:
            manager.post_team_feed(session, "result", "h1", "Migration added", target="m1", task_id="h1",
                                   status="ok")
            manager.post_team_feed(session, "message", "m1", "Ready for tests", target="s1")
            manager.post_team_feed(session, "result", "m1", "Auth done, 12 tests green", target="parent",
                                   task_id="m1", status="ok")
            manager.post_team_feed(session, "result", "s1", "2 tests red", target="parent",
                                   task_id="s1", status="error")
            manager.post_team_feed(session, "message", "s1", "Postgres or SQLite?", target="user")
            teams, desktop = await _teams_messages(bus, 3)
        finally:
            alerts.clear_alert_runtime(bus)
        assert {m.chat_id for m in teams} == {"conv-1"}
        heads = sorted(m.content.split("\n\n", 1)[0] for m in teams)
        assert heads == ["Navin [BLOQUE] Salma stopped", "Navin [INFO] Salma has a question for you",
                         "Navin [OK] Mark finished"]
        assert any("Auth done, 12 tests green" in m.content for m in teams)
        assert all(m.channel == "websocket" and m.chat_id == "desk" for m in desktop)  # the desktop still has them

    asyncio.run(run())


def test_teammate_notification_stays_home_when_notify_is_off(tmp_path, monkeypatch):
    import navin.bus.notify as notify_mod

    _alerts_ready(monkeypatch)
    rc.save_prefs("msteams", notify=False, to="conv-1")
    assert rc.forward_notification("Mark finished", "", "success", "team:1") == 0
    monkeypatch.setattr(notify_mod, "_remote_forwarder", None)
    assert notify_mod.forward_remote("Mark finished", "", "success", "team:1") is False  # no gateway, no copy


def test_end_of_a_desktop_job_reaches_teams_through_the_notify_tool_only(tmp_path, monkeypatch):
    """A finished long job reaches the phone when the agent calls notify; a plain info stays home."""
    from navin.agent.tools.notify import NotifyTool
    from navin.bus.notify import publish_notification
    from navin.bus.queue import MessageBus

    alerts = _alerts_ready(monkeypatch)
    rc.save_prefs("msteams", notify=True, to="conv-1")

    async def run():
        bus = MessageBus()
        alerts.bind_alert_runtime(bus, asyncio.get_running_loop(), {})
        try:
            result = await NotifyTool(bus=bus).execute(title="Site deployed", detail="v2.4 live", level="success")
            assert not result.is_error
            publish_notification(bus, title="Indexing progress", level="info", source="session")
            publish_notification(bus, title="Nightly job failed", level="error", source="cron", key="cron:1")
            teams, desktop = await _teams_messages(bus, 2)
        finally:
            alerts.clear_alert_runtime(bus)
        assert sorted(m.content for m in teams) == ["Navin [ERREUR] Nightly job failed",
                                                     "Navin [OK] Site deployed\n\nv2.4 live"]
        assert len(desktop) == 3  # every one is in the desktop notification centre

    asyncio.run(run())
