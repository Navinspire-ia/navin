# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Teammates email the human manager through Navin's own SMTP channel.

The transport is the Email channel (Settings > Channels > Email), the same one
the CRM and Marketing desks send with. A teammate can only write to the
manager address set in Settings > Team: reaching anyone else (prospects,
campaign lists) stays with the CRM / Marketing tools and their own rules.
"""

from __future__ import annotations

import re
import time
from collections import deque
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
# Anti-spam: per teammate per hour, and per conversation per hour.
MAX_PER_SENDER_PER_HOUR = 6
MAX_PER_SESSION_PER_HOUR = 20
_sent: dict[str, deque[float]] = {}


class CollabMailError(ValueError):
    """Why an email could not go out, phrased for the agent and the user."""


def smtp_status() -> dict[str, Any]:
    """Whether the Email channel can send, and where to configure it."""
    from navin.crm.outreach import channel_status

    try:
        status = channel_status("email")
    except Exception:  # noqa: BLE001 - a broken channel config reads as not ready
        status = {"ready": False}
    return {
        "ready": bool(status.get("ready")),
        "hint": "Settings > Channels > Email: SMTP host, username, password and sender address.",
    }


def _smtp_config() -> dict[str, Any]:
    from navin.crm.outreach import _channel_cfg

    raw = _channel_cfg("email")

    def pick(snake: str, camel: str, default: Any = "") -> Any:
        value = raw.get(snake, raw.get(camel, default))
        return default if value is None else value

    try:
        port = int(pick("smtp_port", "smtpPort", 587) or 587)
    except (TypeError, ValueError):
        port = 587
    return {
        "host": str(pick("smtp_host", "smtpHost")).strip(),
        "port": port,
        "user": str(pick("smtp_username", "smtpUsername")).strip(),
        "password": str(pick("smtp_password", "smtpPassword")).strip(),
        "from": str(pick("from_address", "fromAddress") or pick("smtp_username", "smtpUsername")).strip(),
        "tls": bool(pick("smtp_use_tls", "smtpUseTls", True)),
        "ssl": bool(pick("smtp_use_ssl", "smtpUseSsl", False)),
    }


def _rate_check(keys: list[tuple[str, int]]) -> None:
    now = time.time()
    for key, limit in keys:
        window = _sent.setdefault(key, deque())
        while window and now - window[0] > 3600:
            window.popleft()
        if len(window) >= limit:
            raise CollabMailError(
                f"Email limit reached ({limit} per hour). Put the rest in your report "
                "or the team journal instead."
            )
    for key, _limit in keys:
        _sent[key].append(now)


def build_message(
    *,
    sender: dict[str, str],
    to: str,
    subject: str,
    body: str,
    from_address: str,
    team_name: str = "",
) -> EmailMessage:
    """The email as the manager receives it: who wrote, for which team, why."""
    name = sender.get("name") or "Navin"
    title = sender.get("title") or ""
    display = f"{name} ({title}) via Navin" if title else f"{name} via Navin"
    msg = EmailMessage()
    msg["From"] = formataddr((display, from_address))
    msg["To"] = to
    msg["Subject"] = (subject or "Update from your Navin team").strip()[:200]
    msg["Message-ID"] = make_msgid(domain="navin.team")
    msg["X-Navin-Teammate"] = sender.get("id") or name
    signature = ["-- ", f"{name}" + (f", {title}" if title else "")]
    if team_name:
        signature.append(f"Navin team {team_name}")
    if sender.get("email"):
        signature.append(sender["email"])
    msg.set_content(f"{body.strip()}\n\n" + "\n".join(signature) + "\n")
    return msg


def address_book(collab: Any) -> list[dict[str, str]]:
    """Who the team may email: the manager first, then the saved contacts."""
    book: list[dict[str, str]] = []
    manager = (getattr(collab, "manager_email", "") or "").strip()
    if manager:
        book.append({"name": getattr(collab, "manager_name", "") or "Manager", "email": manager,
                     "role": "manager"})
    for contact in getattr(collab, "email_contacts", None) or []:
        email = str(contact.get("email") or "").strip()
        if email and all(entry["email"].lower() != email.lower() for entry in book):
            book.append({"name": str(contact.get("name") or email), "email": email, "role": "contact"})
    return book


def resolve_recipients(collab: Any, to: Any) -> list[dict[str, str]]:
    """Turn ``to`` (names or addresses, list or comma text) into recipients.

    Empty means the manager. Outside the address book an address is refused
    unless the user allowed any address in Settings > Team.
    """
    book = address_book(collab)
    if isinstance(to, str):
        wanted = [part.strip() for part in re.split(r"[,;]", to) if part.strip()]
    else:
        wanted = [str(part).strip() for part in (to or []) if str(part).strip()]
    if not wanted:
        manager = next((entry for entry in book if entry["role"] == "manager"), None)
        if manager is None:
            raise CollabMailError(
                "No manager email is set. The user adds it in Settings > Team (Human manager)."
            )
        return [manager]
    if len(wanted) > 20:
        raise CollabMailError("20 recipients at most per email; use a Marketing campaign for lists.")
    out: list[dict[str, str]] = []
    for item in wanted:
        key = item.lower()
        match = next((e for e in book if e["email"].lower() == key or e["name"].lower() == key), None)
        if match is None and key in {"manager", "responsable", "boss"}:
            match = next((e for e in book if e["role"] == "manager"), None)
        if match is None:
            if not _EMAIL.match(item):
                names = ", ".join(e["name"] for e in book) or "nobody yet"
                raise CollabMailError(f"Unknown recipient {item!r}. The team's contacts: {names}.")
            if not getattr(collab, "email_allow_any", False):
                raise CollabMailError(
                    f"{item} is not in the team's contacts. The user adds contacts (or allows any "
                    "address) in Settings > Team."
                )
            match = {"name": item, "email": item, "role": "external"}
        if all(r["email"].lower() != match["email"].lower() for r in out):
            out.append(match)
    return out


def send_team_email(
    collab: Any,
    *,
    sender: dict[str, str],
    subject: str,
    body: str,
    to: Any = None,
    session_key: str = "",
    team_name: str = "",
) -> list[dict[str, str]]:
    """Send one email to one or several recipients; return who got it."""
    recipients = resolve_recipients(collab, to)
    for recipient in recipients:
        if not _EMAIL.match(recipient["email"]):
            raise CollabMailError(f"{recipient['email']!r} is not a valid address.")
    _send(collab, sender=sender, recipients=[r["email"] for r in recipients], subject=subject,
          body=body, session_key=session_key, team_name=team_name)
    return recipients


def send_manager_email(
    collab: Any,
    *,
    sender: dict[str, str],
    subject: str,
    body: str,
    session_key: str = "",
    team_name: str = "",
    to: str | None = None,
) -> str:
    """Send one email to the manager; return the address it went to."""
    recipients = send_team_email(collab, sender=sender, subject=subject, body=body, to=to,
                                 session_key=session_key, team_name=team_name)
    return recipients[0]["email"]


def _send(
    collab: Any,
    *,
    sender: dict[str, str],
    recipients: list[str],
    subject: str,
    body: str,
    session_key: str,
    team_name: str,
) -> None:
    import smtplib
    import ssl

    if not (body or "").strip():
        raise CollabMailError("An email needs a body.")
    if len(body) > 20_000:
        raise CollabMailError("Email body is too long (20000 characters max).")
    cfg = _smtp_config()
    if not cfg["host"] or not cfg["user"] or not cfg["password"]:
        raise CollabMailError(f"Email is not configured. {smtp_status()['hint']}")
    _rate_check([
        (f"sender:{session_key}:{sender.get('id') or sender.get('name')}", MAX_PER_SENDER_PER_HOUR),
        (f"session:{session_key}", MAX_PER_SESSION_PER_HOUR),
    ])
    msg = build_message(sender=sender, to=", ".join(recipients), subject=subject, body=body,
                        from_address=cfg["from"] or cfg["user"], team_name=team_name)
    try:
        if cfg["ssl"]:
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20,
                                  context=ssl.create_default_context()) as smtp:
                smtp.login(cfg["user"], cfg["password"])
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as smtp:
                if cfg["tls"]:
                    smtp.starttls(context=ssl.create_default_context())
                smtp.login(cfg["user"], cfg["password"])
                smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise CollabMailError(
            "The email server refused or could not be reached. Check Settings > Channels > Email."
        ) from exc
