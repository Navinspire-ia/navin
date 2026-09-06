"""Send or prepare CRM messages via the existing email / WhatsApp / Teams channels."""

from __future__ import annotations

from email.message import EmailMessage
from typing import Any

from navin.crm.schema import ACTIVITY_KINDS
from navin.crm.store import CrmError, create_record

SETTINGS_HINTS = {
    "email": "Reglages > Canaux > Email (SMTP). Configure smtpHost, smtpUsername, smtpPassword.",
    "whatsapp": "Reglages > Canaux > WhatsApp. Active le plugin WhatsApp puis relie le numero.",
    "teams": "Reglages > Canaux > Microsoft Teams. Relie l'app Bot Framework.",
    "msteams": "Reglages > Canaux > Microsoft Teams. Relie l'app Bot Framework.",
    "slack": "Reglages > Canaux > Slack. Relie le workspace puis le canal.",
}


def _channel_cfg(name: str) -> dict[str, Any]:
    try:
        from navin.config.loader import load_config
    except Exception:
        return {}
    try:
        cfg = load_config()
    except Exception:
        return {}
    channels = getattr(cfg, "channels", None)
    if channels is None:
        return {}
    raw = getattr(channels, name, None)
    if raw is None and hasattr(channels, "model_extra"):
        raw = (channels.model_extra or {}).get(name)
    if raw is None:
        extra = getattr(channels, "__pydantic_extra__", None) or {}
        raw = extra.get(name)
    if isinstance(raw, dict):
        return raw
    if raw is not None and hasattr(raw, "model_dump"):
        return raw.model_dump()
    return {}


def channel_status(name: str) -> dict[str, Any]:
    key = "msteams" if name == "teams" else name
    raw = _channel_cfg(key)
    enabled = bool(raw.get("enabled"))
    if key == "email":
        ready = enabled and bool(raw.get("smtp_host") or raw.get("smtpHost")) and bool(
            raw.get("smtp_password") or raw.get("smtpPassword")
        )
    elif key == "whatsapp":
        ready = enabled
    else:
        ready = enabled
    return {
        "channel": name,
        "enabled": enabled,
        "ready": ready,
        "hint": SETTINGS_HINTS.get(name) or SETTINGS_HINTS.get(key, ""),
    }


def _send_email(to_addr: str, subject: str, body: str) -> None:
    raw = _channel_cfg("email")
    host = str(raw.get("smtp_host") or raw.get("smtpHost") or "").strip()
    user = str(raw.get("smtp_username") or raw.get("smtpUsername") or "").strip()
    password = str(raw.get("smtp_password") or raw.get("smtpPassword") or "").strip()
    from_addr = str(raw.get("from_address") or raw.get("fromAddress") or user).strip()
    try:
        port = int(raw.get("smtp_port") or raw.get("smtpPort") or 587)
    except (TypeError, ValueError):
        port = 587
    use_tls = bool(raw.get("smtp_use_tls", raw.get("smtpUseTls", True)))
    use_ssl = bool(raw.get("smtp_use_ssl", raw.get("smtpUseSsl", False)))
    if not host or not user or not password:
        raise CrmError(
            f"Email n'est pas configure. {SETTINGS_HINTS['email']}",
            status=409,
        )
    import smtplib
    import ssl

    msg = EmailMessage()
    msg["From"] = from_addr or user
    msg["To"] = to_addr
    msg["Subject"] = subject or "Navin CRM"
    msg.set_content(body or "")
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context()) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
        return
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        if use_tls:
            smtp.starttls(context=ssl.create_default_context())
        smtp.login(user, password)
        smtp.send_message(msg)


def outreach(
    project,
    *,
    channel: str,
    to: str,
    subject: str = "",
    body: str = "",
    send: bool = False,
    contact_id: str = "",
    company_id: str = "",
    opportunity_id: str = "",
    lead_id: str = "",
    actor: str = "",
    log_anyway: bool = False,
) -> dict[str, Any]:
    kind = (channel or "email").strip().lower()
    if kind == "msteams":
        kind = "teams"
    if kind not in {"email", "whatsapp", "teams"}:
        raise CrmError("channel must be email, whatsapp, or teams")
    activity_kind = kind if kind in ACTIVITY_KINDS else "note"
    status = channel_status(kind)
    sent = False
    error = ""
    if send:
        try:
            if kind == "email":
                dest = (to or "").strip()
                if not dest or "@" not in dest:
                    raise CrmError("email recipient is required")
                _send_email(dest, subject, body)
                sent = True
            else:
                raise CrmError(
                    f"{kind} est installe comme canal Navin, mais l'envoi depuis une fiche "
                    f"exige un canal connecte. {status['hint']}",
                    status=409,
                )
        except CrmError as exc:
            error = exc.message
            if not log_anyway:
                raise
        except Exception as exc:
            error = str(exc)[:240]
            if not log_anyway:
                raise CrmError(error, status=502) from exc

    title = subject.strip() or f"{kind} - {to or 'brouillon'}"
    prefix = "Envoye" if sent else ("Brouillon" if not send else "Echec envoi")
    activity = create_record(
        project,
        "activities",
        {
            "kind": activity_kind,
            "title": f"{prefix}: {title}"[:160],
            "body": body[:4000],
            "contactId": contact_id,
            "companyId": company_id,
            "opportunityId": opportunity_id,
            "leadId": lead_id,
            "owner": actor,
        },
        actor=actor,
    )
    return {
        "sent": sent,
        "prepared": not send or bool(error),
        "channel": kind,
        "status": status,
        "error": error,
        "activity": activity,
    }
