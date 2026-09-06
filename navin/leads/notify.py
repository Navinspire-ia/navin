"""Fan-out Leads alerts and outreach via Email, WhatsApp, Telegram and Teams."""

from __future__ import annotations

from typing import Any

from navin.bus.notify import notify
from navin.leads.store import LeadsStore, default_channels

CHANNEL_NAMES = ("email", "whatsapp", "telegram", "teams")
BUS_NAMES = {
    "email": "email",
    "whatsapp": "whatsapp",
    "telegram": "telegram",
    "teams": "msteams",
}


def _put_outbound(channel: str, chat_id: str, content: str) -> bool:
    chat = (chat_id or "").strip()
    text = (content or "").strip()
    if not chat or not text:
        return False
    try:
        from navin.bus.events import OutboundMessage
        from navin.bus.notify import _default_bus
    except Exception:
        return False
    bus = _default_bus
    if bus is None:
        return False
    try:
        bus.outbound.put_nowait(OutboundMessage(channel=channel, chat_id=chat, content=text))
        return True
    except Exception:
        return False


def _send_email(to_addr: str, subject: str, body: str) -> bool:
    dest = (to_addr or "").strip()
    if not dest or "@" not in dest:
        return False
    try:
        from navin.crm.outreach import _send_email as smtp_send

        smtp_send(dest, subject, body)
        return True
    except Exception:
        return False


def _telegram_status() -> dict[str, Any]:
    try:
        from navin.config.loader import load_config

        cfg = load_config()
        raw = getattr(getattr(cfg, "channels", None), "telegram", None)
        if raw is None:
            extra = getattr(getattr(cfg, "channels", None), "__pydantic_extra__", None) or {}
            raw = extra.get("telegram")
        data = raw.model_dump() if raw is not None and hasattr(raw, "model_dump") else (raw or {})
        if not isinstance(data, dict):
            data = {}
        enabled = bool(data.get("enabled"))
        token = bool(str(data.get("token") or data.get("bot_token") or "").strip())
        return {
            "channel": "telegram",
            "enabled": enabled,
            "ready": enabled and token,
            "hint": "Settings > Channels > Telegram. Paste the bot token, then the chat id in Config.",
        }
    except Exception:
        return {
            "channel": "telegram",
            "enabled": False,
            "ready": False,
            "hint": "Settings > Channels > Telegram.",
        }


def channel_readiness() -> dict[str, Any]:
    """Live Settings > Channels status for the Leads desk."""
    try:
        from navin.crm.outreach import channel_status
    except Exception:
        channel_status = None  # type: ignore[assignment]
    email = channel_status("email") if channel_status else {"ready": False, "enabled": False, "hint": ""}
    whatsapp = channel_status("whatsapp") if channel_status else {"ready": False, "enabled": False, "hint": ""}
    teams = channel_status("teams") if channel_status else {"ready": False, "enabled": False, "hint": ""}
    return {
        "email": email,
        "whatsapp": whatsapp,
        "telegram": _telegram_status(),
        "teams": teams,
    }


def deliver_alert(
    store: LeadsStore | None,
    *,
    title: str,
    detail: str,
    level: str = "warn",
) -> dict[str, Any]:
    """Best-effort fan-out to the user. A closed channel never fails watch."""
    profile = store.load_profile() if store else {}
    raw = profile.get("channels") if isinstance(profile.get("channels"), dict) else {}
    channels = {**default_channels(), **raw}
    body = f"{title}\n\n{detail}".strip()
    result = {
        "webui": notify(title=title, detail=detail, level=level, source="leads", key="leads-alert"),
        "telegram": False,
        "whatsapp": False,
        "email": False,
        "teams": False,
    }
    if channels.get("telegram"):
        result["telegram"] = _put_outbound("telegram", str(channels.get("telegram_to") or ""), body)
    if channels.get("whatsapp"):
        result["whatsapp"] = _put_outbound("whatsapp", str(channels.get("whatsapp_to") or ""), body)
    if channels.get("teams"):
        result["teams"] = _put_outbound("msteams", str(channels.get("teams_to") or ""), body)
    if channels.get("email"):
        result["email"] = _send_email(str(channels.get("email_to") or ""), title, detail)
    if store:
        store.append_journal(
            {
                "kind": "alert",
                "text": (
                    f"{title} - webui={int(result['webui'])} "
                    f"telegram={int(result['telegram'])} "
                    f"whatsapp={int(result['whatsapp'])} "
                    f"email={int(result['email'])} "
                    f"teams={int(result['teams'])}"
                ),
            }
        )
    return result


def send_channel(channel: str, dest: str, *, subject: str, body: str) -> bool:
    """Send one message on a connected Navin channel. False if the bus or SMTP is down."""
    kind = (channel or "").strip().lower()
    if kind == "msteams":
        kind = "teams"
    text = (body or subject or "").strip()
    target = (dest or "").strip()
    if kind == "email":
        return _send_email(target, subject or "Navin Leads", text)
    bus = BUS_NAMES.get(kind)
    if not bus:
        return False
    return _put_outbound(bus, target, text)
