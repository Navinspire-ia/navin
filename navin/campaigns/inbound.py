# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Campaign replies: read the mailbox, attach replies to their campaign, classify.

The poller reads the Email channel's IMAP mailbox with ``BODY.PEEK`` so it never
marks a message read or steals it from the Email channel. Only messages that
answer a campaign (threaded on our Message-ID, or from an address we sent to)
are stored. A first classification is rule-based and honest about it
(confidence < 1); the Campaigns agent refines it on the heartbeat, drafts the
answer and hands real leads to a teammate with a brief.
"""

from __future__ import annotations

import email
import imaplib
import re
from email import policy
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

from loguru import logger

from navin.campaigns import store

_RULES: tuple[tuple[str, float, tuple[str, ...]], ...] = (
    ("unsubscribe", 0.9, ("unsubscribe", "désinscri", "desinscri", "remove me", "stop", "retirez-moi",
                          "ne plus recevoir", "إلغاء الاشتراك")),
    ("out_of_office", 0.9, ("out of office", "absent du bureau", "automatic reply", "réponse automatique",
                            "reponse automatique", "en congé", "en conge", "auto-reply", "autoreply", "vacation")),
    ("meeting", 0.75, ("meeting", "call", "rendez-vous", "rdv", "réunion", "reunion", "calendly", "disponible",
                       "available", "schedule a", "visio", "demo", "démo")),
    ("not_interested", 0.75, ("not interested", "pas intéressé", "pas interesse", "no thanks", "non merci",
                              "pas besoin", "not now", "already have", "déjà équipé")),
    ("interested", 0.7, ("interested", "intéressé", "interesse", "tell me more", "en savoir plus", "pricing",
                         "tarif", "prix", "devis", "quote", "let's talk", "parlons", "oui")),
    ("question", 0.6, ("?",)),
)
_BOUNCE_FROM = re.compile(r"mailer-daemon|postmaster|mail delivery", re.IGNORECASE)


def strip_quoted(body: str) -> str:
    """The new part of a reply, without the quoted thread below it."""
    lines = []
    for line in (body or "").splitlines():
        if line.startswith(">") or re.match(r"^(On .+wrote:|Le .+a écrit\s*:)$", line.strip()):
            break
        if re.match(r"^-{2,}\s*(Original Message|Message d'origine)", line.strip(), re.IGNORECASE):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def classify(subject: str, body: str, from_addr: str = "") -> tuple[str, float, str]:
    """Rule-based first pass: (classification, confidence, one-line summary)."""
    text = f"{subject}\n{strip_quoted(body)}".lower()
    if _BOUNCE_FROM.search(from_addr) or "delivery status notification" in text or "undeliverable" in text:
        return "bounce", 0.95, "Delivery failure"
    first = strip_quoted(body).strip().splitlines()[0] if strip_quoted(body).strip() else subject
    for label, confidence, words in _RULES:
        if label == "unsubscribe":
            if re.search(r"\b(stop|unsubscribe)\b", text) or any(w in text for w in words if len(w) > 4):
                return label, confidence, first[:160]
            continue
        if any(w in text for w in words):
            return label, confidence, first[:160]
    return "other", 0.3, first[:160]


def _body_text(message: email.message.EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = part.get_content()
    if part.get_content_type() == "text/html":
        from navin.campaigns.render import html_to_text

        content = html_to_text(content)
    return str(content)[:20_000]


def _ids(header: str | None) -> list[str]:
    return re.findall(r"<[^>]+>", header or "")


def ingest_message(raw: bytes) -> dict[str, Any] | None:
    """Store one inbound email if it answers a campaign; return the reply."""
    message = email.message_from_bytes(raw, policy=policy.default)
    from_addr = (getaddresses([message.get("From", "")]) or [("", "")])[0][1].lower()
    subject = str(message.get("Subject", ""))
    body = _body_text(message)  # type: ignore[arg-type]
    references = _ids(message.get("In-Reply-To")) + _ids(message.get("References"))
    bounce = bool(_BOUNCE_FROM.search(from_addr))
    if bounce:
        # A bounce quotes the original: find our Message-ID or recipient in it.
        references += re.findall(r"<[^>\s]+@[^>\s]+>", body)
        failed = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", body)
        send = store.find_send_for_reply(references, "")
        if send is None:
            for addr in failed:
                send = store.find_send_for_reply([], addr)
                if send:
                    break
    else:
        send = store.find_send_for_reply(references, from_addr)
    if send is None:
        return None
    try:
        received = parsedate_to_datetime(message.get("Date")).astimezone().isoformat() if message.get("Date") else ""
    except (TypeError, ValueError):
        received = ""
    label, confidence, summary = classify(subject, body, from_addr)
    reply = store.add_reply({
        "campaign_id": send["campaign_id"], "send_id": send["id"], "contact_id": send["contact_id"],
        "from_addr": from_addr, "subject": subject, "body": strip_quoted(body) or body,
        "message_id": str(message.get("Message-ID", "")).strip(), "received_at": received or store.now_iso(),
        "classification": label, "confidence": confidence, "summary": summary,
    })
    if reply is None:
        return None
    if label == "bounce":
        store.mark_send(send["id"], bounced=1)
        store.set_contact_status(send["contact_id"], "bounced")
        store.update_reply({"id": reply["id"], "status": "ignored"})
    else:
        store.mark_send(send["id"], replied_at=reply["received_at"])
        if label == "unsubscribe":
            # Nothing to answer: the contact is out of every future send.
            store.set_contact_status(send["contact_id"], "unsubscribed")
            store.update_reply({"id": reply["id"], "status": "ignored"})
        if label == "out_of_office":
            store.update_reply({"id": reply["id"], "status": "ignored"})
    store.record_event("reply", campaign_id=send["campaign_id"], send_id=send["id"], data={"class": label})
    return store.get_reply(reply["id"])


def _imap_config() -> dict[str, Any]:
    from navin.crm.outreach import _channel_cfg

    raw = _channel_cfg("email")

    def pick(snake: str, camel: str, default: Any = "") -> Any:
        value = raw.get(snake, raw.get(camel, default))
        return default if value is None else value

    return {
        "host": str(pick("imap_host", "imapHost")).strip(),
        "port": int(pick("imap_port", "imapPort", 993) or 993),
        "user": str(pick("imap_username", "imapUsername") or pick("smtp_username", "smtpUsername")).strip(),
        "password": str(pick("imap_password", "imapPassword") or pick("smtp_password", "smtpPassword")).strip(),
        "mailbox": str(pick("imap_mailbox", "imapMailbox", "INBOX") or "INBOX"),
        "ssl": bool(pick("imap_use_ssl", "imapUseSsl", True)),
    }


def poll_replies(*, max_messages: int = 200) -> int:
    """Read new mailbox messages (without marking them read); return replies stored."""
    cfg = _imap_config()
    if not (cfg["host"] and cfg["user"] and cfg["password"]):
        return 0
    with store.connect() as conn:
        has_sends = conn.execute("SELECT 1 FROM sends WHERE status='sent' LIMIT 1").fetchone()
    if not has_sends:
        return 0
    state = store.get_state("imap", {}) or {}
    key = f"{cfg['user']}@{cfg['host']}/{cfg['mailbox']}"
    last_uid = int((state.get(key) or {}).get("uid") or 0)
    stored = 0
    client: Any = None
    try:
        client = imaplib.IMAP4_SSL(cfg["host"], cfg["port"]) if cfg["ssl"] else imaplib.IMAP4(cfg["host"], cfg["port"])
        client.login(cfg["user"], cfg["password"])
        client.select(cfg["mailbox"], readonly=True)
        if last_uid:
            status, data = client.uid("SEARCH", None, f"UID {last_uid + 1}:*")
        else:
            # First run: only the last 14 days, not the whole history.
            from datetime import datetime, timedelta

            since = (datetime.now() - timedelta(days=14)).strftime("%d-%b-%Y")
            status, data = client.uid("SEARCH", None, f"SINCE {since}")
        uids = [int(u) for u in (data[0] or b"").split() if int(u) > last_uid] if status == "OK" else []
        for uid in uids[-max_messages:]:
            status, parts = client.uid("FETCH", str(uid), "(BODY.PEEK[])")
            if status != "OK":
                continue
            raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
            if raw and ingest_message(raw) is not None:
                stored += 1
            last_uid = max(last_uid, uid)
        state[key] = {"uid": last_uid}
        store.set_state("imap", state)
        store.set_state("last_imap_poll", store.now_iso())
    except (imaplib.IMAP4.error, OSError) as exc:
        logger.warning("Campaign reply poll could not read the mailbox: {}", exc)
    finally:
        if client is not None:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
    return stored


def send_reply(reply_id: str, body: str, subject: str = "") -> dict[str, Any]:
    """Answer a reply by email, threaded on the prospect's message."""
    import html as html_lib

    from navin.campaigns import sender
    from navin.campaigns.store import CampaignError

    reply = store.get_reply(reply_id)
    if not (body or "").strip():
        raise CampaignError("The answer is empty")
    if reply.get("channel") == "whatsapp":
        return _send_whatsapp_reply(reply, body)
    settings = store.get_settings()
    subject = subject or (reply["subject"] if reply["subject"].lower().startswith("re:") else f"Re: {reply['subject']}")
    html = "".join(f"<p>{line}</p>" for line in html_lib.escape(body).split("\n"))
    rendered = {"subject": subject, "text": body, "html": f"<!doctype html><html><body>{html}</body></html>",
                "unsubscribe_url": ""}
    msg = sender.build_email(rendered=rendered, to=reply["from_addr"],
                             from_name=settings.get("from_name") or "",
                             from_email=sender.email_ready()["from_email"],
                             reply_to=settings.get("reply_to") or "")
    if reply.get("message_id"):
        msg["In-Reply-To"] = reply["message_id"]
        msg["References"] = reply["message_id"]
    with sender.SmtpSession() as smtp:
        smtp.send(msg)
    with store.connect() as conn:
        conn.execute("UPDATE replies SET status='answered', answered_at=?, draft_reply=? WHERE id=?",
                     (store.now_iso(), body[:8000], reply_id))
    store.record_event("answered", campaign_id=reply["campaign_id"], data={"reply": reply_id})
    return store.get_reply(reply_id)


def _send_whatsapp_reply(reply: dict[str, Any], body: str) -> dict[str, Any]:
    """Answer on WhatsApp: free text is allowed for 24 h after the contact wrote."""
    from datetime import datetime, timedelta, timezone

    from navin.campaigns import sender
    from navin.campaigns.store import CampaignError

    try:
        received = datetime.fromisoformat(reply["received_at"].replace("Z", "+00:00"))
    except ValueError:
        received = datetime.now(timezone.utc)
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - received > timedelta(hours=24):
        raise CampaignError("The 24-hour WhatsApp window is closed: answer with an approved template campaign, "
                            "or by email.", status=409)
    settings = store.get_settings(with_secrets=True)
    sender.send_whatsapp(settings, reply["from_addr"], {"text": body.strip()})
    with store.connect() as conn:
        conn.execute("UPDATE replies SET status='answered', answered_at=?, draft_reply=? WHERE id=?",
                     (store.now_iso(), body[:8000], reply["id"]))
    store.record_event("answered", campaign_id=reply["campaign_id"], data={"reply": reply["id"], "channel": "whatsapp"})
    return store.get_reply(reply["id"])
