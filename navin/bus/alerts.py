"""Persistent, recipient-specific alerts with transport acknowledgements.

Enqueueing is never delivery. The gateway records the provider message ID
after sending; a later watch pass observes that receipt and finishes the event.
An ambiguous transport error is not automatically retried, because the first
message might already have been accepted by the provider.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock

from navin.bus.events import OutboundMessage
from navin.utils.atomic_io import atomic_write_text

CHANNELS = ("telegram", "whatsapp", "email", "teams", "slack")
_INSTANCE = secrets.token_hex(12)
_RUNTIME: dict[str, Any] = {}
_INFLIGHT: dict[str, dict[str, Any]] = {}
_TASKS: set[asyncio.Task] = set()
_META_TOKEN = "_alert_delivery_token"
_MAX_ATTEMPTS = 3
_DISPATCH_TIMEOUT = 45.0


class AlertTransportError(RuntimeError):
    """A known transport outcome, without secret-bearing exception strings."""

    def __init__(self, code: str, *, uncertain: bool = False, message_ids: list[str] | None = None):
        super().__init__(code)
        self.code = code
        self.uncertain = uncertain
        self.message_ids = message_ids or []


def bind_alert_runtime(bus: Any, loop: asyncio.AbstractEventLoop, channels: dict[str, Any]) -> None:
    """Called by the gateway dispatcher, on the loop owning the outbound queue."""
    _RUNTIME.update({"bus": bus, "loop": loop, "channels": channels, "semaphore": asyncio.Semaphore(4)})


def clear_alert_runtime(bus: Any = None) -> None:
    if bus is None or _RUNTIME.get("bus") is bus:
        _RUNTIME.clear()
        for token, entry in list(_INFLIGHT.items()):
            def fail_queued(payload: dict[str, Any], entry: dict[str, Any] = entry) -> None:
                receipt = payload["events"].get(entry["event_key"], {}).get("receipts", {}).get(entry["receipt_key"], {})
                if receipt.get("attempt_id") == entry["token"] and receipt.get("status") == "pending":
                    receipt.update(status="failed", error="gateway_stopped_before_send", retry_at=time.time() + 60)

            if entry["path"].exists():
                _change(entry["path"], fail_queued)
            _INFLIGHT.pop(token, None)


async def stop_alert_dispatch(bus: Any) -> None:
    if _RUNTIME.get("bus") is not bus:
        return
    tasks = list(_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    clear_alert_runtime(bus)


def schedule_queued_alert(channel: Any, message: OutboundMessage) -> bool:
    """Alert transports cannot hold up chat streaming in the outbound dispatcher."""
    if not (message.metadata or {}).get(_META_TOKEN):
        return False
    semaphore = _RUNTIME.get("semaphore")

    async def run() -> None:
        try:
            if semaphore is None:
                await dispatch_queued_alert(channel, message)
            else:
                async with semaphore:
                    await dispatch_queued_alert(channel, message)
        except asyncio.CancelledError:
            raise
        except Exception:
            from loguru import logger

            logger.error("Alert delivery receipt could not be persisted")

    task = asyncio.create_task(run(), name="navin-alert-delivery")
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return True


def _cfg(name: str) -> dict[str, Any]:
    from navin.crm.outreach import _channel_cfg

    return _channel_cfg("msteams" if name == "teams" else name)


def _setting(raw: dict[str, Any], snake: str, camel: str, default: Any = "") -> Any:
    return raw.get(snake, raw.get(camel, default))


def _configured(channel: str, raw: dict[str, Any]) -> bool:
    if not raw.get("enabled"):
        return False
    if channel == "telegram":
        return bool(raw.get("token") or raw.get("bot_token") or raw.get("botToken"))
    if channel == "email":
        return bool(
            _setting(raw, "smtp_host", "smtpHost")
            and _setting(raw, "smtp_username", "smtpUsername")
            and _setting(raw, "smtp_password", "smtpPassword")
            and _setting(raw, "consent_granted", "consentGranted", False)
        )
    if channel == "teams":
        return bool(_setting(raw, "app_id", "appId") and _setting(raw, "app_password", "appPassword"))
    if channel == "slack":
        return bool(_setting(raw, "bot_token", "botToken"))
    return channel == "whatsapp"


def channel_readiness() -> dict[str, Any]:
    loop = _RUNTIME.get("loop")
    running = bool(loop and loop.is_running())
    hints = {
        "telegram": "Reglages > Canaux > Telegram: bot token et identifiant du chat.",
        "whatsapp": "Reglages > Canaux > WhatsApp: numero connecte par QR et destinataire.",
        "email": "Reglages > Canaux > Email: SMTP, consentement et destinataire.",
        "teams": "Reglages > Canaux > Microsoft Teams: application Bot Framework et conversation existante.",
        "slack": "Reglages > Canaux > Slack: bot token et canal.",
    }
    result = {}
    for channel in CHANNELS:
        raw = _cfg(channel)
        configured = _configured(channel, raw)
        instance = (_RUNTIME.get("channels") or {}).get("msteams" if channel == "teams" else channel)
        connected = running and (channel == "email" or instance is not None)
        if channel == "whatsapp" and instance is not None:
            connected = connected and bool(getattr(instance, "_connected", False))
        result[channel] = {"channel": channel, "enabled": bool(raw.get("enabled")), "configured": configured,
                           "ready": configured and connected, "runtime": connected, "hint": hints[channel]}
    return result


def _path(store: Any, module: str) -> Path:
    if store is not None:
        return Path(store.root) / "alert-deliveries.json"
    from navin.config.paths import get_runtime_subdir

    return get_runtime_subdir(module) / "alert-deliveries.json"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": 1, "events": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), dict):
        raise ValueError("invalid alert delivery ledger")
    return payload


def _change(path: Path, apply: Callable[[dict[str, Any]], Any]) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=5):
        payload = _load(path)
        result = apply(payload)
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2), mode=0o600)
        return result


def _hash(*values: str) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _update_receipt(entry: dict[str, Any], **update: Any) -> bool:
    def apply(payload: dict[str, Any]) -> bool:
        event = payload["events"].get(entry["event_key"], {})
        receipt = event.get("receipts", {}).get(entry["receipt_key"])
        if receipt and receipt.get("attempt_id") == entry["token"]:
            receipt.update(update)
            receipt["updated_at"] = time.time()
            return True
        return False

    return bool(_change(entry["path"], apply))


def _begin_delivery(entry: dict[str, Any]) -> bool:
    def apply(payload: dict[str, Any]) -> bool:
        receipt = payload["events"].get(entry["event_key"], {}).get("receipts", {}).get(entry["receipt_key"], {})
        if receipt.get("attempt_id") != entry["token"] or receipt.get("status") != "pending":
            return False
        receipt.update(status="sending", updated_at=time.time(), attempts=int(receipt.get("attempts") or 0) + 1)
        return True

    return bool(_change(entry["path"], apply))


def _enqueue(entry: dict[str, Any], message: OutboundMessage) -> bool:
    loop = _RUNTIME.get("loop")
    bus = _RUNTIME.get("bus")
    if not loop or not loop.is_running() or bus is None:
        _update_receipt(entry, status="failed", error="gateway_not_running", retry_at=time.time() + 60)
        return False
    _INFLIGHT[entry["token"]] = entry

    def put() -> None:
        try:
            bus.outbound.put_nowait(message)
        except Exception:
            _INFLIGHT.pop(entry["token"], None)
            _update_receipt(entry, status="failed", error="outbound_queue_full", retry_at=time.time() + 60)

    try:
        loop.call_soon_threadsafe(put)
        return True
    except RuntimeError:
        _INFLIGHT.pop(entry["token"], None)
        _update_receipt(entry, status="failed", error="gateway_not_running", retry_at=time.time() + 60)
        return False


def deliver_alert(store: Any, *, module: str, title: str, detail: str, level: str = "warn",
                  event_id: str = "", event_type: str = "alert", retry: bool = False,
                  webui_notify: Callable[..., bool] | None = None) -> dict[str, Any]:
    """Record one event and queue only recipients without a confirmed receipt."""
    title, detail = str(title or "").strip(), str(detail or "").strip()
    event_id = str(event_id or f"{event_type}:{_hash(title, detail)[:24]}")
    profile = store.load_profile() if store is not None else {}
    channels = profile.get("channels") if isinstance(profile.get("channels"), dict) else {}
    path = _path(store, module)
    event_key = _hash(module, event_id)
    now = time.time()
    outgoing: list[tuple[dict[str, Any], OutboundMessage]] = []
    configs = {name: _cfg(name) for name in CHANNELS if channels.get(name)}
    webui = {"queue": False, "new_event": False}

    def prepare(payload: dict[str, Any]) -> None:
        webui["new_event"] = event_key not in payload["events"]
        event = payload["events"].setdefault(event_key, {
            "event_id": event_id, "event_type": event_type, "module": module, "created_at": now,
            "title": title, "detail": detail, "level": level, "receipts": {}, "webui_queued": False,
        })
        # A stable event keeps its original content, so a later retry cannot
        # quietly change the message already accepted on another channel.
        text = f"{event['title']}\n\n{event['detail']}".strip()
        event["updated_at"] = now
        event["targets"] = []
        webui["queue"] = not event.get("webui_queued") and not event.get("webui_pending")
        if webui["queue"]:
            event["webui_pending"] = True
        for channel in CHANNELS:
            if not channels.get(channel):
                continue
            destination = str(channels.get(f"{channel}_to") or "").strip()
            key = _hash(channel, destination)
            event["targets"].append(key)
            receipt = event["receipts"].setdefault(key, {"channel": channel, "destination": destination,
                "status": "new", "attempts": 0, "message_id": "", "message_ids": [], "error": ""})
            status = receipt["status"]
            if status == "accepted":
                continue
            if status == "sending":
                if receipt.get("instance") != _INSTANCE or now - float(receipt.get("updated_at") or now) > 120:
                    receipt.update(status="uncertain", error="confirmation_missing_after_send", updated_at=now)
                continue
            if status in {"uncertain", "cancelled"}:
                continue
            if status == "pending":
                if receipt.get("attempt_id") in _INFLIGHT or (receipt.get("instance") == _INSTANCE and now - float(receipt.get("updated_at") or now) < 120):
                    continue
            if not retry and (float(receipt.get("retry_at") or 0) > now or int(receipt.get("attempts") or 0) >= _MAX_ATTEMPTS):
                continue
            config = configs.get(channel, {})
            if not destination or not _configured(channel, config) or not title or len(text) > 16000:
                error = "destination_missing" if not destination else "channel_not_configured" if not _configured(channel, config) else "alert_content_invalid"
                receipt.update(status="failed", error=error, updated_at=now, retry_at=now + 60)
                continue
            token = secrets.token_urlsafe(24)
            receipt.update(status="pending", instance=_INSTANCE, attempt_id=token,
                           updated_at=now, error="", retry_at=now + 60)
            entry = {"token": token, "path": path, "store": store, "event_key": event_key, "receipt_key": key,
                     "channel": channel, "destination": destination, "raw_config": config,
                     "title": event["title"], "content_hash": _hash(text)}
            message = OutboundMessage(channel="msteams" if channel == "teams" else channel, chat_id=destination,
                                      content=text, metadata={_META_TOKEN: token, "subject": event["title"]})
            outgoing.append((entry, message))

    _change(path, prepare)
    if webui["queue"]:
        try:
            queued = bool(webui_notify and webui_notify(title=title, detail=detail, level="warning" if level == "warn" else level,
                                                       source=module, key=f"{module}-{event_key[:24]}"))
        except Exception:
            queued = False
        _change(path, lambda payload: payload["events"][event_key].update(webui_queued=queued, webui_pending=False))
    queued_count = sum(_enqueue(entry, message) for entry, message in outgoing)
    with FileLock(str(path) + ".lock", timeout=5):
        event = _load(path)["events"][event_key]
    return {**_event_result(event), "queued_count": queued_count, "new_event": webui["new_event"]}


def _event_result(event: dict[str, Any]) -> dict[str, Any]:
    receipts = {row["channel"]: {name: value for name, value in row.items() if name not in {"attempt_id", "instance"}}
                for key, row in event["receipts"].items() if key in event.get("targets", [])}
    result = {name: receipts.get(name, {}).get("status") == "accepted" for name in CHANNELS}
    result.update({"webui": False, "webui_queued": bool(event.get("webui_queued")), "event_id": event["event_id"],
                   "event_type": event["event_type"], "receipts": receipts,
                   "confirmed": any(result.values()),
                   "complete": bool(receipts) and all(row["status"] == "accepted" for row in receipts.values()),
                   "confirmation_scope": "transport_acceptance_not_read_receipt"})
    return result


def delivery_snapshot(store: Any, *, module: str = "tenders", limit: int = 30) -> dict[str, Any]:
    path = _path(store, module)
    if not path.exists():
        return {"events": [], "pending": 0, "failed": 0, "accepted": 0, "uncertain": 0}
    try:
        with FileLock(str(path) + ".lock", timeout=5):
            events = list(_load(path)["events"].values())
    except (OSError, ValueError, TimeoutError):
        return {"events": [], "pending": 0, "failed": 0, "accepted": 0, "uncertain": 0, "error": "delivery_state_unavailable"}
    receipts = [row for event in events for key, row in event["receipts"].items() if key in event.get("targets", [])]
    result: dict[str, Any] = {"pending": sum(row["status"] in {"pending", "sending"} for row in receipts)}
    result.update({key: sum(row["status"] == key for row in receipts) for key in ("failed", "accepted", "uncertain")})
    result["events"] = [{"title": event["title"], "created_at": event["created_at"], **_event_result(event)}
                        for event in sorted(events, key=lambda item: item["created_at"], reverse=True)[:limit]]
    return result


def retry_failed_alerts(store: Any, *, module: str) -> dict[str, Any]:
    """Explicit retry of definite failures; accepted or ambiguous sends are untouched."""
    path = _path(store, module)
    if not path.exists():
        return delivery_snapshot(store, module=module)
    with FileLock(str(path) + ".lock", timeout=5):
        events = list(_load(path)["events"].values())
    for event in events:
        if any(row["status"] == "failed" for row in event["receipts"].values()):
            deliver_alert(store, module=module, title=event["title"], detail=event["detail"], level=event["level"],
                          event_id=event["event_id"], event_type=event["event_type"], retry=True)
    return delivery_snapshot(store, module=module)


def resume_alerts(store: Any, *, module: str, limit: int = 30) -> None:
    """Scheduled retries respect backoff, opt-in and the transport attempt limit."""
    path = _path(store, module)
    if not path.exists():
        return
    with FileLock(str(path) + ".lock", timeout=5):
        events = list(_load(path)["events"].values())
    waiting = [event for event in events if event.get("event_type") != "watch_digest" and any(row["status"] in {"failed", "pending", "sending"}
                for key, row in event["receipts"].items() if key in event.get("targets", []))]
    for event in sorted(waiting, key=lambda row: row["created_at"])[:limit]:
        deliver_alert(store, module=module, title=event["title"], detail=event["detail"], level=event["level"],
                      event_id=event["event_id"], event_type=event["event_type"])


def cancel_alert(store: Any, *, module: str, event_id: str) -> None:
    """Cancel unsent reminders whose underlying work is no longer pending."""
    path = _path(store, module)
    if not path.exists():
        return

    def apply(payload: dict[str, Any]) -> None:
        event = payload["events"].get(_hash(module, event_id), {})
        for receipt in event.get("receipts", {}).values():
            if receipt.get("status") in {"new", "pending", "failed"}:
                receipt.update(status="cancelled", error="event_no_longer_pending", updated_at=time.time())

    _change(path, apply)


async def dispatch_queued_alert(channel: Any, message: OutboundMessage) -> bool:
    """Intercept only internal alert tokens before ordinary channel dispatch."""
    token = (message.metadata or {}).get(_META_TOKEN)
    if not token:
        return False
    entry = _INFLIGHT.get(str(token))
    if entry is None:
        return True
    expected_channel = "msteams" if entry["channel"] == "teams" else entry["channel"]
    if message.channel != expected_channel or message.chat_id != entry["destination"] or _hash(message.content) != entry["content_hash"]:
        _update_receipt(entry, status="failed", error="queued_message_changed")
        _INFLIGHT.pop(str(token), None)
        return True
    try:
        profile = entry["store"].load_profile() if entry.get("store") is not None else {}
        channels = profile.get("channels") or {}
        name = entry["channel"]
        if not channels.get(name) or str(channels.get(f"{name}_to") or "").strip() != entry["destination"]:
            raise AlertTransportError("channel_opt_in_removed")
        if name != "email" and (channel is None or not callable(getattr(channel, "send_alert", None))):
            raise AlertTransportError("channel_not_connected")
        if not _begin_delivery(entry):
            return True
        if name == "email":
            operation = asyncio.to_thread(send_smtp_alert, entry["raw_config"], entry["destination"], entry["title"], message.content)
        else:
            operation = channel.send_alert(message)
        receipt = await asyncio.wait_for(operation, timeout=_DISPATCH_TIMEOUT)
        ids = receipt.get("message_ids") if isinstance(receipt, dict) else None
        if not isinstance(ids, list) or not ids or any(not isinstance(value, str) or not value.strip() for value in ids):
            raise AlertTransportError("provider_confirmation_missing", uncertain=True)
        _update_receipt(entry, status="accepted", message_id=ids[0], message_ids=ids,
                        acknowledged_at=time.time(), error="", provider=receipt.get("provider") or name)
    except AlertTransportError as exc:
        _update_receipt(entry, status="uncertain" if exc.uncertain or exc.message_ids else "failed", error=exc.code,
                        message_ids=exc.message_ids, retry_at=time.time() + 60)
    except asyncio.CancelledError:
        _update_receipt(entry, status="uncertain", error="dispatch_interrupted")
        raise
    except Exception as exc:
        # No raw provider exception is persisted: its URL may contain a token.
        _update_receipt(entry, status="uncertain", error="transport_outcome_unknown", error_type=type(exc).__name__)
    finally:
        _INFLIGHT.pop(str(token), None)
    return True


def send_smtp_alert(raw: dict[str, Any], destination: str, subject: str, content: str) -> dict[str, Any]:
    """SMTP acceptance receipt; it does not claim the recipient read the email."""
    import smtplib
    import ssl
    from email.message import EmailMessage
    from email.utils import make_msgid

    if not _configured("email", raw):
        raise AlertTransportError("channel_not_configured")
    if "@" not in destination or any(value in destination for value in ("\r", "\n", ",", ";")):
        raise AlertTransportError("invalid_recipient")
    host = str(_setting(raw, "smtp_host", "smtpHost"))
    user = str(_setting(raw, "smtp_username", "smtpUsername"))
    password = str(_setting(raw, "smtp_password", "smtpPassword"))
    sender = str(_setting(raw, "from_address", "fromAddress") or user)
    use_ssl = bool(_setting(raw, "smtp_use_ssl", "smtpUseSsl", False))
    use_tls = bool(_setting(raw, "smtp_use_tls", "smtpUseTls", True))
    port = int(_setting(raw, "smtp_port", "smtpPort", 465 if use_ssl else 587))
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, destination, subject
    message["Message-ID"] = make_msgid()
    message.set_content(content)
    try:
        if use_ssl:
            smtp = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(host, port, timeout=20)
        with smtp:
            if use_tls and not use_ssl:
                smtp.starttls(context=ssl.create_default_context())
            smtp.login(user, password)
            refused = smtp.send_message(message)
            if refused:
                raise AlertTransportError("smtp_recipient_refused")
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPAuthenticationError, smtplib.SMTPConnectError, smtplib.SMTPDataError):
        raise AlertTransportError("smtp_rejected") from None
    return {"message_ids": [str(message["Message-ID"])], "provider": "smtp"}
