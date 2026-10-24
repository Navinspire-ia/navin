# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Deliver campaign messages: SMTP for email, WhatsApp Cloud API for WhatsApp.

Email uses the Email channel's SMTP account (Settings > Channels > Email), the
same one the CRM, Marketing and the Team send with. WhatsApp uses Meta's
official Cloud API with approved templates: the linked-device WhatsApp channel
is for conversations, and bulk sending through it gets numbers banned.
"""

from __future__ import annotations

import mimetypes
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Any

import httpx

from navin.campaigns.db import files_dir
from navin.campaigns.store import CampaignError

GRAPH = "https://graph.facebook.com/v21.0"
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def smtp_config() -> dict[str, Any]:
    from navin.collab.mail import _smtp_config

    return _smtp_config()


def email_ready() -> dict[str, Any]:
    cfg = smtp_config()
    ready = bool(cfg["host"] and cfg["user"] and cfg["password"])
    from navin.crm.outreach import _channel_cfg

    raw = _channel_cfg("email")
    imap = bool((raw.get("imap_host") or raw.get("imapHost")) and
                (raw.get("imap_password") or raw.get("imapPassword") or cfg["password"]))
    return {"ready": ready, "imap_ready": imap, "from_email": cfg["from"] or cfg["user"],
            "hint": "Settings > Channels > Email: SMTP host, username, password and sender address."}


def attachment_path(attachment_id: str) -> Path:
    safe = re.sub(r"[^\w.-]", "", attachment_id)
    path = files_dir() / safe
    if not safe or not path.is_file():
        raise CampaignError(f"Attachment {attachment_id} is missing; upload it again", status=404)
    return path


def save_attachment(filename: str, data: bytes) -> dict[str, Any]:
    import uuid

    if not data:
        raise CampaignError("The file is empty")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise CampaignError("Attachments are limited to 10 MB each")
    name = re.sub(r"[\\/\x00-\x1f]", "_", (filename or "file").strip())[:180] or "file"
    safe_name = re.sub(r"[^\w.-]", "_", name)
    attachment_id = f"{uuid.uuid4().hex[:16]}__{safe_name}"
    (files_dir() / attachment_id).write_bytes(data)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return {"id": attachment_id, "name": name, "size": len(data), "mime": mime}


def build_email(
    *,
    rendered: dict[str, Any],
    to: str,
    from_name: str,
    from_email: str,
    reply_to: str = "",
    attachments: list[dict[str, Any]] | None = None,
    campaign_id: str = "",
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, from_email)) if from_name else from_email
    msg["To"] = to
    msg["Subject"] = rendered["subject"] or "(no subject)"
    domain = from_email.split("@", 1)[-1] if "@" in from_email else "navin.local"
    msg["Message-ID"] = make_msgid(domain=domain)
    if reply_to:
        msg["Reply-To"] = reply_to
    if campaign_id:
        msg["X-Navin-Campaign"] = campaign_id
    if rendered.get("unsubscribe_url"):
        msg["List-Unsubscribe"] = f"<{rendered['unsubscribe_url']}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(rendered["text"] or " ")
    msg.add_alternative(rendered["html"], subtype="html")
    for attachment in attachments or []:
        path = attachment_path(attachment["id"])
        maintype, _, subtype = (attachment.get("mime") or "application/octet-stream").partition("/")
        msg.add_attachment(path.read_bytes(), maintype=maintype or "application",
                           subtype=subtype or "octet-stream", filename=attachment.get("name") or path.name)
    return msg


class SmtpSession:
    """One authenticated SMTP connection for a batch of sends."""

    def __init__(self) -> None:
        self.cfg = smtp_config()
        if not (self.cfg["host"] and self.cfg["user"] and self.cfg["password"]):
            raise CampaignError("Email is not configured: Settings > Channels > Email (SMTP).", status=409)
        self.client: smtplib.SMTP | None = None

    def __enter__(self) -> SmtpSession:
        cfg = self.cfg
        try:
            if cfg["ssl"]:
                self.client = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=30,
                                               context=ssl.create_default_context())
            else:
                self.client = smtplib.SMTP(cfg["host"], cfg["port"], timeout=30)
                if cfg["tls"]:
                    self.client.starttls(context=ssl.create_default_context())
            self.client.login(cfg["user"], cfg["password"])
        except (smtplib.SMTPException, OSError) as exc:
            raise CampaignError("The email server refused the login or could not be reached. "
                                "Check Settings > Channels > Email.", status=502) from exc
        return self

    def send(self, msg: EmailMessage) -> None:
        assert self.client is not None
        self.client.send_message(msg)

    def __exit__(self, *exc: Any) -> None:
        if self.client is not None:
            try:
                self.client.quit()
            except (smtplib.SMTPException, OSError):
                pass


# -- WhatsApp Cloud API -------------------------------------------------------


def _wa_config(settings: dict[str, Any]) -> tuple[str, str, str]:
    wa = settings.get("whatsapp") or {}
    phone_id, token = wa.get("phone_number_id") or "", wa.get("token") or ""
    if not phone_id or not token:
        raise CampaignError("WhatsApp is not configured: add the Cloud API phone number id and access "
                            "token in Campaigns settings.", status=409)
    return phone_id, token, wa.get("waba_id") or ""


def wa_number(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 8:
        raise CampaignError(f"{phone!r} is not a full international phone number")
    return digits


def send_whatsapp(settings: dict[str, Any], to: str, rendered: dict[str, Any],
                  *, client: httpx.Client | None = None) -> str:
    """Send one WhatsApp message; return its message id."""
    phone_id, token, _ = _wa_config(settings)
    if rendered.get("template"):
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp", "to": wa_number(to), "type": "template",
            "template": {"name": rendered["template"], "language": {"code": rendered.get("language") or "en"}},
        }
        if rendered.get("params"):
            payload["template"]["components"] = [{
                "type": "body",
                "parameters": [{"type": "text", "text": p or "-"} for p in rendered["params"]],
            }]
    elif rendered.get("text"):
        payload = {"messaging_product": "whatsapp", "to": wa_number(to), "type": "text",
                   "text": {"body": rendered["text"][:4096]}}
    else:
        raise CampaignError("The WhatsApp message is empty")
    own = client is None
    http = client or httpx.Client(timeout=30)
    try:
        response = http.post(f"{GRAPH}/{phone_id}/messages", json=payload,
                             headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise CampaignError("WhatsApp could not be reached", status=502) from exc
    finally:
        if own:
            http.close()
    data = _json(response)
    if response.status_code >= 400:
        raise CampaignError(_graph_error(data, response.status_code), status=502)
    messages = data.get("messages") or [{}]
    return str(messages[0].get("id") or "")


def whatsapp_templates(settings: dict[str, Any], *, client: httpx.Client | None = None) -> list[dict[str, Any]]:
    _, token, waba = _wa_config(settings)
    if not waba:
        raise CampaignError("Add the WhatsApp Business Account id (WABA) to list templates", status=409)
    own = client is None
    http = client or httpx.Client(timeout=30)
    try:
        response = http.get(f"{GRAPH}/{waba}/message_templates", params={"limit": 200},
                            headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise CampaignError("WhatsApp could not be reached", status=502) from exc
    finally:
        if own:
            http.close()
    data = _json(response)
    if response.status_code >= 400:
        raise CampaignError(_graph_error(data, response.status_code), status=502)
    out = []
    for item in data.get("data") or []:
        body = next((c.get("text", "") for c in item.get("components") or [] if c.get("type") == "BODY"), "")
        out.append({"name": item.get("name", ""), "language": item.get("language", ""),
                    "status": item.get("status", ""), "category": item.get("category", ""),
                    "body": body, "params": len(set(re.findall(r"\{\{(\d+)\}\}", body)))})
    return out


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _graph_error(data: dict[str, Any], status: int) -> str:
    error = data.get("error") or {}
    message = error.get("error_user_msg") or error.get("message") or f"HTTP {status}"
    return f"WhatsApp refused the message: {message}"
