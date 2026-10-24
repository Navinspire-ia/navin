# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Turn a campaign into the email (or WhatsApp text) one contact receives.

Merge tags: ``{{first_name}}``, ``{{first_name|there}}`` (fallback),
``{{attrs.plan}}``. Email bodies get a hidden preheader, the legal footer with
the unsubscribe link, and, when a public tracking URL is set, an open pixel and
rewritten links. Tracking tokens are HMAC-signed so a URL cannot be forged to
mark someone else opened, clicked or unsubscribed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html as html_lib
import re
from typing import Any
from urllib.parse import quote

_TAG = re.compile(r"\{\{\s*([a-zA-Z_][\w.]*)\s*(?:\|\s*([^}]*?))?\s*\}\}")
_SCRIPT = re.compile(r"<\s*(script|iframe|object|embed)\b.*?(</\s*\1\s*>|/?>)", re.IGNORECASE | re.DOTALL)
_ON_ATTR = re.compile(r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_HREF = re.compile(r"""(<a\b[^>]*?\bhref\s*=\s*)(["'])(.*?)\2""", re.IGNORECASE | re.DOTALL)
FIELDS = ("first_name", "last_name", "company", "title", "email", "phone", "city", "country")


def contact_value(contact: dict[str, Any], key: str) -> str:
    if key.startswith("attrs."):
        return str((contact.get("attrs") or {}).get(key[6:], "") or "")
    if key == "full_name":
        return " ".join(p for p in (contact.get("first_name"), contact.get("last_name")) if p).strip()
    return str(contact.get(key, "") or "")


def merge(text: str, contact: dict[str, Any], *, escape: bool = False, missing: set[str] | None = None) -> str:
    def repl(match: re.Match[str]) -> str:
        key, fallback = match.group(1), match.group(2)
        value = contact_value(contact, key)
        if not value:
            if fallback is None and missing is not None:
                missing.add(key)
            value = fallback or ""
        return html_lib.escape(value) if escape else value

    return _TAG.sub(repl, text or "")


def sanitize_html(body: str) -> str:
    """Drop scripts, frames and inline handlers; mail clients block them anyway."""
    return _ON_ATTR.sub("", _SCRIPT.sub("", body or ""))


def html_to_text(body: str) -> str:
    text = re.sub(r"(?is)<(style|head)\b.*?</\1>", "", body or "")
    text = re.sub(r"(?is)<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", r"\2 (\1)", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|h[1-6]|li|tr|table)>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


# -- tracking tokens ----------------------------------------------------------


def sign(secret: str, *parts: str) -> str:
    payload = ":".join(parts)
    mac = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()[:12]
    raw = f"{payload}:{base64.urlsafe_b64encode(mac).decode().rstrip('=')}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def verify(secret: str, token: str) -> list[str] | None:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    *parts, mac = raw.split(":")
    if not parts:
        return None
    expected = sign(secret, *parts)
    return parts if hmac.compare_digest(expected, token) else None


def tracking_urls(base_url: str, secret: str, send_id: str) -> dict[str, str]:
    base = base_url.rstrip("/")
    return {
        "open": f"{base}/api/campaigns/t/o/{sign(secret, 'o', send_id)}.gif",
        "click": f"{base}/api/campaigns/t/c/{sign(secret, 'c', send_id)}",
        "unsubscribe": f"{base}/api/campaigns/t/u/{sign(secret, 'u', send_id)}",
    }


# -- email ------------------------------------------------------------------


def render_email(
    campaign: dict[str, Any],
    contact: dict[str, Any],
    settings: dict[str, Any],
    *,
    send_id: str = "",
    secret: str = "",
) -> dict[str, Any]:
    """Subject, HTML and text for one contact, plus warnings for the preview."""
    missing: set[str] = set()
    subject = merge(campaign.get("subject") or "", contact, missing=missing)
    preheader = merge(campaign.get("preheader") or "", contact, missing=missing)
    body = sanitize_html(merge(campaign.get("html") or "", contact, escape=True, missing=missing))
    tracking = bool(settings.get("tracking_enabled") and settings.get("tracking_base_url") and send_id and secret)
    urls = tracking_urls(settings["tracking_base_url"], secret, send_id) if tracking else {}
    unsubscribe_url = urls.get("unsubscribe", "")
    if tracking and campaign.get("track_clicks", True):
        def rewrite(match: re.Match[str]) -> str:
            href = html_lib.unescape(match.group(3))
            if not re.match(r"^https?://", href) or "/api/campaigns/t/" in href:
                return match.group(0)
            return f"{match.group(1)}{match.group(2)}{urls['click']}?u={quote(href, safe='')}{match.group(2)}"

        body = _HREF.sub(rewrite, body)
    footer_parts = []
    if settings.get("footer_html"):
        footer_parts.append(sanitize_html(merge(settings["footer_html"], contact, escape=True)))
    if settings.get("company_address"):
        footer_parts.append(html_lib.escape(settings["company_address"]))
    unsubscribe_text = html_lib.escape(settings.get("unsubscribe_text") or "Unsubscribe")
    if unsubscribe_url:
        footer_parts.append(f'<a href="{unsubscribe_url}" style="color:#6b7280">{unsubscribe_text}</a>')
    else:
        footer_parts.append(f"{unsubscribe_text}: reply STOP")
    footer = "<br>".join(footer_parts)
    pixel = (f'<img src="{urls["open"]}" width="1" height="1" alt="" style="display:block;border:0">'
             if tracking and campaign.get("track_opens", True) else "")
    hidden = (f'<div style="display:none;max-height:0;overflow:hidden;opacity:0">{html_lib.escape(preheader)}</div>'
              if preheader else "")
    document = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{html_lib.escape(subject)}</title></head>"
        "<body style=\"margin:0;padding:0;background:#f4f5f7\">"
        f"{hidden}"
        "<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"background:#f4f5f7\">"
        "<tr><td align=\"center\" style=\"padding:24px 12px\">"
        "<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" "
        "style=\"max-width:640px;background:#ffffff;border-radius:12px\">"
        "<tr><td style=\"padding:28px 28px 20px;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;"
        f"font-size:15px;line-height:1.6;color:#111827\">{body}</td></tr></table>"
        "<p style=\"max-width:640px;margin:16px auto 0;font-family:Arial,sans-serif;font-size:12px;"
        f"line-height:1.5;color:#6b7280\">{footer}</p>{pixel}"
        "</td></tr></table></body></html>"
    )
    text = html_to_text(body)
    footer_text = html_to_text(footer.replace("<br>", "\n"))
    if unsubscribe_url:
        footer_text = footer_text.replace(f"{settings.get('unsubscribe_text') or 'Unsubscribe'}",
                                          f"{settings.get('unsubscribe_text') or 'Unsubscribe'}: {unsubscribe_url}", 1)
    warnings: list[str] = []
    if not subject.strip():
        warnings.append("The subject is empty.")
    if missing:
        warnings.append("No value and no fallback for: " + ", ".join(sorted(missing))
                        + ". Add a fallback like {{first_name|there}}.")
    if not settings.get("company_address"):
        warnings.append("Add your company address in Campaigns settings (required by anti-spam laws).")
    if not (settings.get("tracking_enabled") and settings.get("tracking_base_url")):
        warnings.append("No public tracking URL: opens and clicks will not be measured, and the unsubscribe "
                        "link is replaced by 'reply STOP'.")
    if not body.strip():
        warnings.append("The message is empty.")
    return {
        "subject": subject,
        "preheader": preheader,
        "html": document,
        "text": f"{text}\n\n--\n{footer_text}".strip(),
        "unsubscribe_url": unsubscribe_url,
        "warnings": warnings,
    }


def render_whatsapp(campaign: dict[str, Any], contact: dict[str, Any]) -> dict[str, Any]:
    missing: set[str] = set()
    params = [merge(p, contact, missing=missing) for p in campaign.get("wa_params") or []]
    text = merge(campaign.get("text") or "", contact, missing=missing)
    warnings = []
    if missing:
        warnings.append("No value and no fallback for: " + ", ".join(sorted(missing)) + ".")
    if not campaign.get("wa_template") and text:
        warnings.append("Free text only reaches people who wrote to you in the last 24 hours. "
                        "Use an approved template for a campaign.")
    if not campaign.get("wa_template") and not text:
        warnings.append("Pick an approved WhatsApp template.")
    return {"template": campaign.get("wa_template") or "", "language": campaign.get("wa_language") or "en",
            "params": params, "text": text, "warnings": warnings}
