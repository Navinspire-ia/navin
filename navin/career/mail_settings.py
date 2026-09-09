# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Public account settings and explicit Career mail permissions."""

from __future__ import annotations

import re
from email.errors import HeaderParseError
from email.headerregistry import Address
from typing import Any

from navin.career.errors import CareerError


def default_mailbox() -> dict[str, Any]:
    return {
        "enabled": False,
        "sender_name": "",
        "sender_email": "",
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "smtp_username": "",
        "imap_host": "",
        "imap_port": 993,
        "imap_security": "ssl",
        "imap_username": "",
        "imap_folder": "INBOX",
        "read_replies": False,
        "auto_send": False,
        "min_match_score": 85,
        "max_per_day": 3,
        "poll_interval_minutes": 5,
        "allowed_recipient_domains": [],
    }


def normalize_mailbox(raw: Any) -> dict[str, Any]:
    """Whitelist public fields; never serialize a password into the profile."""
    default = default_mailbox()
    incoming = raw if isinstance(raw, dict) else {}
    result = {key: incoming.get(key, value) for key, value in default.items()}
    for key in ("enabled", "read_replies", "auto_send"):
        result[key] = result[key] is True
    for key in (
        "sender_name", "sender_email", "smtp_host", "smtp_username",
        "imap_host", "imap_username", "imap_folder",
    ):
        result[key] = str(result[key] or "").strip()[:254]
    result["imap_folder"] = result["imap_folder"] or "INBOX"
    for key in ("smtp_security", "imap_security"):
        value = str(result[key] or default[key]).lower()
        result[key] = value if value in {"ssl", "starttls"} else default[key]
    for key, lower, upper in (
        ("smtp_port", 1, 65535), ("imap_port", 1, 65535),
        ("min_match_score", 0, 100), ("max_per_day", 1, 25),
        ("poll_interval_minutes", 1, 60),
    ):
        try:
            result[key] = min(upper, max(lower, int(result[key])))
        except (TypeError, ValueError):
            result[key] = default[key]
    domains = result["allowed_recipient_domains"]
    if isinstance(domains, str):
        domains = re.split(r"[,;\s]+", domains)
    result["allowed_recipient_domains"] = sorted({
        str(value).strip().lower().removeprefix("@").rstrip(".")
        for value in domains if isinstance(value, str) and value.strip()
    })[:100] if isinstance(domains, list) else []
    return result


def email_address(value: Any) -> str:
    """Accept one explicit mailbox, without a display name or header injection."""
    text = str(value or "").strip()
    if not text or len(text) > 254 or any(char in text for char in "\r\n\x00,;<>"):
        raise CareerError("mail_recipient_invalid")
    try:
        text.encode("ascii")
        address = Address(addr_spec=text)
    except (ValueError, IndexError, UnicodeError, HeaderParseError) as exc:
        raise CareerError("mail_recipient_invalid") from exc
    if not address.username or not address.domain or "." not in address.domain:
        raise CareerError("mail_recipient_invalid")
    return f"{address.username}@{address.domain.lower()}"


def validate_mailbox(config: dict[str, Any], *, protocol: str = "smtp") -> None:
    if protocol == "smtp":
        email_address(config.get("sender_email"))
    for key in (f"{protocol}_host", f"{protocol}_username"):
        value = str(config.get(key) or "")
        if not value or any(char.isspace() for char in value) or any(char in value for char in "\x00/\\"):
            raise CareerError(f"mail_{key}_required")
    for key in ("sender_name", "imap_folder"):
        if any(char in str(config.get(key) or "") for char in "\r\n\x00"):
            raise CareerError("mail_header_invalid")


def published_recipient(job: dict[str, Any]) -> tuple[str, str]:
    """Only an explicit address or an unambiguous invitation in the offer."""
    explicit = job.get("application_email")
    if explicit:
        return email_address(explicit), str(job.get("application_email_source") or "provided")
    for key in ("apply_url", "application_url"):
        value = str(job.get(key) or "")
        if value.lower().startswith("mailto:"):
            return email_address(value[7:].split("?", 1)[0]), "published_offer"
    description = str(job.get("description") or "")
    invitations = re.findall(
        r"(?:send\s+(?:your\s+)?(?:cv|resume|application)|apply\s+(?:by\s+email|via\s+email|to|at)|"
        r"envoy(?:ez|er)\s+(?:votre\s+)?(?:cv|candidature)|(?:candidature|postulez)\s+(?:par\s+(?:e-?mail|courriel)|[àa]))"
        r"[^\n.!?]{0,100}?([A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,})",
        description, re.IGNORECASE,
    )
    addresses = {email_address(value) for value in invitations}
    if len(addresses) == 1:
        return next(iter(addresses)), "published_offer"
    return "", ""
