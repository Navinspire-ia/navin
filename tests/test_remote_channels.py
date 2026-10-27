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
