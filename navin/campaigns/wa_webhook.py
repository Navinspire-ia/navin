# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""WhatsApp Cloud API webhook: inbound replies and delivery/read receipts.

Meta delivers incoming WhatsApp messages only by POSTing to a public webhook.
The gateway's HTTP layer takes no request bodies, so Campaigns runs this small
dedicated server (default port 18795) while the webhook is enabled in
Campaigns settings. Expose it publicly (reverse proxy or tunnel) and give Meta
``https://<public host>/whatsapp/webhook`` with the verify token shown in
settings. Every POST is checked against the app secret signature.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

from loguru import logger

from navin.campaigns import store

PATH = "/whatsapp/webhook"
MAX_BODY = 1024 * 1024
_SERVER: dict[str, Any] = {}


def verify_signature(app_secret: str, body: bytes, header: str) -> bool:
    if not app_secret:
        return False
    expected = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, (header or "").strip())


def _text_of(message: dict[str, Any]) -> str:
    kind = message.get("type")
    if kind == "text":
        return str((message.get("text") or {}).get("body") or "")
    if kind == "button":
        return str((message.get("button") or {}).get("text") or "")
    if kind == "interactive":
        inner = message.get("interactive") or {}
        reply = inner.get("button_reply") or inner.get("list_reply") or {}
        return str(reply.get("title") or "")
    if kind in ("image", "document", "audio", "video", "sticker"):
        caption = (message.get(kind) or {}).get("caption") or ""
        return f"[{kind}] {caption}".strip()
    return f"[{kind or 'message'}]"


def process_payload(payload: dict[str, Any]) -> dict[str, int]:
    """Store inbound replies and read receipts from one webhook delivery."""
    from navin.campaigns.inbound import classify

    counts = {"replies": 0, "receipts": 0}
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts") or []}
            for message in value.get("messages") or []:
                phone = str(message.get("from") or "")
                send = None
                context_id = (message.get("context") or {}).get("id")
                if context_id:
                    send = store.find_send_by_message_id(context_id)
                send = send or store.find_whatsapp_send(phone)
                if send is None:
                    continue
                text = _text_of(message)
                label, confidence, summary = classify("", text, "")
                try:
                    received = datetime.fromtimestamp(int(message.get("timestamp")), timezone.utc).isoformat()
                except (TypeError, ValueError):
                    received = store.now_iso()
                reply = store.add_reply({
                    "campaign_id": send["campaign_id"], "send_id": send["id"], "contact_id": send["contact_id"],
                    "from_addr": f"+{phone}" if phone and not phone.startswith("+") else phone,
                    "subject": names.get(phone) or "WhatsApp", "body": text,
                    "message_id": str(message.get("id") or ""), "received_at": received,
                    "classification": label, "confidence": confidence, "summary": summary, "channel": "whatsapp",
                })
                if reply is None:
                    continue
                counts["replies"] += 1
                store.mark_send(send["id"], replied_at=received)
                if label == "unsubscribe":
                    store.set_contact_status(send["contact_id"], "unsubscribed")
                    store.update_reply({"id": reply["id"], "status": "ignored"})
                store.record_event("reply", campaign_id=send["campaign_id"], send_id=send["id"],
                                   data={"class": label, "channel": "whatsapp"})
            for status in value.get("statuses") or []:
                send = store.find_send_by_message_id(str(status.get("id") or ""))
                if send is None:
                    continue
                state = status.get("status")
                if state == "read" and not send["opened_at"]:
                    store.mark_send(send["id"], opened_at=store.now_iso(), open_count=send["open_count"] + 1)
                    counts["receipts"] += 1
                elif state == "failed":
                    errors = status.get("errors") or [{}]
                    store.mark_send(send["id"], status="failed",
                                    error=str(errors[0].get("title") or errors[0].get("message") or "failed")[:300])
                    counts["receipts"] += 1
    return counts


def handle_request(method: str, target: str, headers: dict[str, str], body: bytes) -> tuple[int, str, bytes]:
    """(status, content type, body) for one webhook HTTP request."""
    url = urlsplit(target)
    if url.path.rstrip("/") != PATH:
        return 404, "text/plain", b"not found"
    wa = store.get_settings(with_secrets=True).get("whatsapp") or {}
    if method == "GET":
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if (query.get("hub.mode") == "subscribe" and wa.get("verify_token")
                and hmac.compare_digest(query.get("hub.verify_token", ""), wa["verify_token"])):
            return 200, "text/plain", query.get("hub.challenge", "").encode()
        return 403, "text/plain", b"forbidden"
    if method != "POST":
        return 405, "text/plain", b"method not allowed"
    if not verify_signature(wa.get("app_secret") or "", body, headers.get("x-hub-signature-256", "")):
        return 401, "text/plain", b"bad signature"
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return 400, "text/plain", b"bad json"
    try:
        process_payload(payload if isinstance(payload, dict) else {})
    except Exception:  # noqa: BLE001 - Meta retries on non-200; log and acknowledge
        logger.exception("WhatsApp webhook processing failed")
    return 200, "application/json", b'{"ok":true}'


async def _serve_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    status, ctype, body = 400, "text/plain", b"bad request"
    try:
        request_line = (await asyncio.wait_for(reader.readline(), 10)).decode("latin-1").strip()
        method, target, _ = request_line.split(" ", 2)
        headers: dict[str, str] = {}
        while True:
            line = (await asyncio.wait_for(reader.readline(), 10)).decode("latin-1")
            if line in ("\r\n", "\n", ""):
                break
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
        length = int(headers.get("content-length") or 0)
        if length > MAX_BODY:
            status, body = 413, b"too large"
        else:
            data = await asyncio.wait_for(reader.readexactly(length), 15) if length else b""
            status, ctype, body = await asyncio.to_thread(handle_request, method.upper(), target, headers, data)
    except (ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
        pass
    reason = {200: "OK", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
              413: "Payload Too Large"}.get(status, "Bad Request")
    try:
        writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                     "Connection: close\r\n\r\n".encode() + body)
        await writer.drain()
    finally:
        writer.close()


async def ensure_server() -> None:
    """Start, restart (port changed) or stop the webhook server to match settings."""
    wa = store.get_settings(with_secrets=True).get("whatsapp") or {}
    wanted = int(wa.get("webhook_port") or 18795) if wa.get("webhook_enabled") else None
    current = _SERVER.get("server")
    if current is not None and _SERVER.get("port") == wanted:
        return
    if current is not None:
        current.close()
        await current.wait_closed()
        _SERVER.clear()
    if wanted is None:
        return
    try:
        server = await asyncio.start_server(_serve_client, host="0.0.0.0", port=wanted)
    except OSError as exc:
        logger.warning("WhatsApp webhook could not listen on port {}: {}", wanted, exc)
        # Shown in the Campaigns setup checklist: plain words, no technical detail.
        store.set_state("wa_webhook_error", "WhatsApp replies cannot be received right now: the reception "
                        "setting is already used by another program. Pick another one in Campaigns settings.")
        return
    _SERVER.update(server=server, port=wanted)
    store.set_state("wa_webhook_error", "")
    logger.info("WhatsApp webhook listening on port {}{}", wanted, PATH)
