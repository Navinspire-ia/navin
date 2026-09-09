# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Actual outbox, gateway dispatch and channel adapters with fake transports only."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from navin.bus import alerts
from navin.bus.events import OutboundMessage
from navin.bus.queue import MessageBus
from navin.tenders.store import TenderStore


def _config(name: str) -> dict:
    return {"enabled": True, "token": "audit-fake-token", "bot_token": "audit-fake-token",
            "app_id": "audit-app", "app_password": "audit-fake-password", "consent_granted": True,
            "smtp_host": "smtp.example.invalid", "smtp_username": "audit@example.invalid",
            "smtp_password": "audit-fake-password", "smtp_use_tls": True}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "_cfg", _config)
    desk = TenderStore(tmp_path / "tenders")
    desk.save_profile({"name": "Audit Alerts", "channels": {"telegram": True, "telegram_to": "123",
                                                           "teams": True, "teams_to": "audit-conversation"}})
    yield desk
    alerts.clear_alert_runtime()
    alerts._INFLIGHT.clear()


def _send(store, **kwargs):
    return alerts.deliver_alert(store, module="tenders", event_id="audit-event-001", event_type="watch_digest",
                               title="Audit appel d'offre", detail="Audit: un nouvel avis public.", **kwargs)


async def _messages(bus):
    await asyncio.sleep(0.01)
    result = []
    while not bus.outbound.empty():
        result.append(bus.outbound.get_nowait())
    return result


@pytest.mark.asyncio
async def test_queue_is_pending_and_only_confirmed_targets_are_deduplicated(store):
    bus = MessageBus()
    alerts.bind_alert_runtime(bus, asyncio.get_running_loop(), {})
    initial = _send(store, webui_notify=lambda **kwargs: True)
    assert not initial["telegram"] and not initial["teams"]
    assert not initial["webui"] and initial["webui_queued"]
    assert not initial["complete"] and not initial["confirmed"]
    telegram = SimpleNamespace(send_alert=AsyncMock(return_value={"message_ids": ["audit-tg-11"]}))
    teams = SimpleNamespace(send_alert=AsyncMock(side_effect=alerts.AlertTransportError("channel_not_connected")))
    queued = await _messages(bus)
    for message in queued:
        await alerts.dispatch_queued_alert(telegram if message.channel == "telegram" else teams, message)
    partial = _send(store)
    assert partial["telegram"] and not partial["teams"]
    assert partial["confirmed"] and not partial["complete"]
    assert partial["receipts"]["telegram"]["message_id"] == "audit-tg-11"
    assert "attempt_id" not in partial["receipts"]["telegram"]
    assert await _messages(bus) == []

    teams.send_alert = AsyncMock(return_value={"message_ids": ["audit-teams-22"]})
    _send(store, retry=True)
    retry = await _messages(bus)
    assert [message.channel for message in retry] == ["msteams"]
    await alerts.dispatch_queued_alert(teams, retry[0])
    complete = _send(store)
    assert complete["complete"] and complete["confirmed"]
    assert await _messages(bus) == []
    telegram.send_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_receipt_is_uncertain_and_never_automatically_repeated(store):
    store.save_profile({"channels": {"teams": False}})
    bus = MessageBus()
    alerts.bind_alert_runtime(bus, asyncio.get_running_loop(), {})
    _send(store)
    message = (await _messages(bus))[0]
    transport = SimpleNamespace(send_alert=AsyncMock(return_value=None))
    await alerts.dispatch_queued_alert(transport, message)
    result = _send(store, retry=True)
    assert result["receipts"]["telegram"]["status"] == "uncertain"
    assert not result["complete"] and not result["telegram"]
    assert await _messages(bus) == []
    transport.send_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_profile_opt_out_before_dispatch_prevents_send(store):
    store.save_profile({"channels": {"teams": False}})
    bus = MessageBus()
    alerts.bind_alert_runtime(bus, asyncio.get_running_loop(), {})
    _send(store)
    message = (await _messages(bus))[0]
    store.save_profile({"channels": {"telegram": False}})
    transport = SimpleNamespace(send_alert=AsyncMock())
    await alerts.dispatch_queued_alert(transport, message)
    transport.send_alert.assert_not_called()
    ledger = json.loads((store.root / "alert-deliveries.json").read_text())
    receipt = next(iter(next(iter(ledger["events"].values()))["receipts"].values()))
    assert receipt["error"] == "channel_opt_in_removed"


@pytest.mark.asyncio
async def test_slow_alert_does_not_hold_the_gateway_dispatcher(store):
    store.save_profile({"channels": {"teams": False}})
    bus = MessageBus()
    alerts.bind_alert_runtime(bus, asyncio.get_running_loop(), {})
    _send(store)
    message = (await _messages(bus))[0]
    ready, release = asyncio.Event(), asyncio.Event()

    async def delayed(_message):
        ready.set()
        await release.wait()
        return {"message_ids": ["audit-late-receipt"]}

    assert alerts.schedule_queued_alert(SimpleNamespace(send_alert=delayed), message)
    await asyncio.wait_for(ready.wait(), timeout=1)
    assert not alerts.schedule_queued_alert(None, OutboundMessage(channel="websocket", chat_id="audit", content="normal chat"))
    assert alerts.delivery_snapshot(store)["pending"] == 1
    release.set()
    await asyncio.wait_for(asyncio.gather(*list(alerts._TASKS)), timeout=1)
    assert _send(store)["complete"]


def test_absent_runtime_and_corrupt_state_do_not_claim_delivery(store):
    result = _send(store)
    assert result["receipts"]["telegram"]["error"] == "gateway_not_running"
    assert not result["confirmed"]
    path = store.root / "alert-deliveries.json"
    path.write_text("corrupt audit ledger")
    from navin.tenders.notify import deliver_alert

    safe = deliver_alert(store, title="Audit", detail="Audit")
    assert safe["error"] == "delivery_state_unavailable"
    assert path.read_text() == "corrupt audit ledger"


def test_smtp_receipt_requires_acceptance_for_the_recipient():
    smtp = Mock()
    smtp.__enter__ = Mock(return_value=smtp)
    smtp.__exit__ = Mock(return_value=False)
    smtp.send_message.return_value = {}
    with patch("smtplib.SMTP", return_value=smtp):
        result = alerts.send_smtp_alert(_config("email"), "audit-recipient@example.invalid", "Audit", "Audit notification")
    message = smtp.send_message.call_args.args[0]
    assert result["message_ids"] == [message["Message-ID"]]
    smtp.starttls.assert_called_once()
    assert message["To"] == "audit-recipient@example.invalid"
    smtp.send_message.return_value = {"audit-recipient@example.invalid": (550, b"refused")}
    with patch("smtplib.SMTP", return_value=smtp), pytest.raises(alerts.AlertTransportError, match="smtp_recipient_refused"):
        alerts.send_smtp_alert(_config("email"), "audit-recipient@example.invalid", "Audit", "Audit")
    with patch("smtplib.SMTP") as factory, pytest.raises(alerts.AlertTransportError, match="channel_not_configured"):
        alerts.send_smtp_alert({**_config("email"), "consent_granted": False}, "audit@example.invalid", "Audit", "Audit")
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_real_telegram_adapter_keeps_transport_ids():
    pytest.importorskip("telegram", reason="Optional python-telegram-bot SDK is not installed")
    from navin.channels.telegram import TelegramChannel

    message = OutboundMessage(channel="telegram", chat_id="123", content="Audit notification")
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=432)))
    telegram = SimpleNamespace(_app=SimpleNamespace(bot=bot))
    assert (await TelegramChannel.send_alert(telegram, message))["message_ids"] == ["432"]
    bot.send_message.return_value = SimpleNamespace(message_id=None)
    with pytest.raises(alerts.AlertTransportError, match="provider_confirmation_missing"):
        await TelegramChannel.send_alert(telegram, message)


@pytest.mark.asyncio
async def test_real_whatsapp_adapter_keeps_transport_ids():
    from navin.channels.whatsapp import WhatsAppChannel

    message = OutboundMessage(channel="whatsapp", chat_id="audit", content="Audit notification")
    client = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(ID="audit-wa-server-id")))
    whatsapp = SimpleNamespace(_client=client, _connected=True, _build_jid=lambda value: value)
    assert (await WhatsAppChannel.send_alert(whatsapp, message))["message_ids"] == ["audit-wa-server-id"]
    whatsapp._connected = False
    with pytest.raises(alerts.AlertTransportError, match="channel_not_connected"):
        await WhatsAppChannel.send_alert(whatsapp, message)


@pytest.mark.asyncio
async def test_real_teams_adapter_validates_conversation_and_activity_receipt():
    from navin.channels.msteams import MSTeamsChannel

    message = OutboundMessage(channel="msteams", chat_id="audit-conversation", content="Audit")
    response = httpx.Response(200, json={"id": "audit-activity-id"}, request=httpx.Request("POST", "https://example.invalid"))
    teams = SimpleNamespace(_http=SimpleNamespace(post=AsyncMock(return_value=response)),
                            _conversation_refs={"audit-conversation": SimpleNamespace(service_url="https://smba.trafficmanager.net/emea/", conversation_id="audit-conversation")},
                            _is_trusted_service_url=lambda url: True, _get_access_token=AsyncMock(return_value="audit-token"),
                            _touch_conversation_ref=Mock())
    assert (await MSTeamsChannel.send_alert(teams, message))["message_ids"] == ["audit-activity-id"]
    teams._is_trusted_service_url = lambda url: False
    with pytest.raises(alerts.AlertTransportError, match="teams_service_url_untrusted"):
        await MSTeamsChannel.send_alert(teams, message)
    assert teams._http.post.await_count == 1


def test_watch_keeps_partial_batch_unchanged_when_new_notices_arrive(store):
    from navin.tenders.watch import pending_alerts, run_watch

    def notice(identity):
        return {"id": identity, "title": f"Audit {identity}", "source_id": "ted", "country": "FR",
                "source_url": f"https://ted.europa.eu/en/notice/-/detail/{identity}",
                "deadline": "2099-12-31", "stage": "go", "go": True, "score": 85}

    store.save_tenders([notice("audit-first")])
    with patch("navin.tenders.watch.deliver_alert", return_value={"telegram": True, "complete": False}) as delivery:
        first = run_watch(store)
        initial = delivery.call_args.kwargs
    assert not first["delivered"]
    store.upsert_tenders([notice("audit-second")])
    with patch("navin.tenders.watch.deliver_alert", return_value={"telegram": True, "complete": False}) as delivery:
        run_watch(store)
    calls = [call.kwargs for call in delivery.call_args_list]
    assert len(calls) == 2
    assert calls[0]["event_id"] == initial["event_id"]
    assert calls[0]["detail"] == initial["detail"]
    assert "audit-first" not in calls[1]["detail"]
    with patch("navin.tenders.watch.deliver_alert", return_value={"complete": True}):
        assert run_watch(store)["delivered"]
    assert pending_alerts(store) == []


def test_deadline_watch_uses_today_instead_of_old_score():
    import datetime as dt

    from navin.tenders.watch import _days_left

    deadline = (dt.date.today() + dt.timedelta(days=2)).isoformat()
    assert _days_left({"deadline": deadline, "score_breakdown": {"days_left": 40}}) == 2
