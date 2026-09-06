"""Fan-out Tenders alerts to Telegram, WhatsApp, email, Teams, and the WebUI centre."""

from __future__ import annotations

from typing import Any

from navin.bus.notify import notify
from navin.tenders.store import TenderStore, default_channels


def channel_readiness() -> dict[str, Any]:
    try:
        from navin.crm.outreach import channel_status
    except Exception:
        channel_status = None  # type: ignore[assignment]
    email = channel_status("email") if channel_status else {"ready": False, "enabled": False, "hint": ""}
    whatsapp = channel_status("whatsapp") if channel_status else {"ready": False, "enabled": False, "hint": ""}
    teams = channel_status("teams") if channel_status else {"ready": False, "enabled": False, "hint": ""}
    slack = channel_status("slack") if channel_status else {"ready": False, "enabled": False, "hint": ""}
    return {
        "telegram": _telegram_status(),
        "whatsapp": whatsapp,
        "email": email,
        "teams": teams,
        "slack": slack,
    }


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
            "hint": "Reglages > Canaux > Telegram. Colle le bot token puis le chat id dans Config.",
        }
    except Exception:
        return {
            "channel": "telegram",
            "enabled": False,
            "ready": False,
            "hint": "Reglages > Canaux > Telegram.",
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


def deliver_alert(
    store: TenderStore,
    *,
    title: str,
    detail: str,
    level: str = "info",
) -> dict[str, Any]:
    """Best-effort fan-out. A closed channel never fails Collect or mail."""
    profile = store.load_profile() if store else {}
    raw = profile.get("channels") if isinstance(profile.get("channels"), dict) else {}
    channels = {**default_channels(), **raw}
    body = f"{title}\n\n{detail}".strip()
    result = {
        "webui": notify(title=title, detail=detail, level=level, source="tenders", key="tenders-alert"),
        "telegram": False,
        "whatsapp": False,
        "email": False,
        "teams": False,
        "slack": False,
    }
    if channels.get("telegram"):
        result["telegram"] = _put_outbound("telegram", str(channels.get("telegram_to") or ""), body)
    if channels.get("whatsapp"):
        result["whatsapp"] = _put_outbound("whatsapp", str(channels.get("whatsapp_to") or ""), body)
    if channels.get("teams"):
        result["teams"] = _put_outbound("msteams", str(channels.get("teams_to") or ""), body)
    if channels.get("slack"):
        result["slack"] = _put_outbound("slack", str(channels.get("slack_to") or ""), body)
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
                    f"teams={int(result['teams'])} "
                    f"slack={int(result['slack'])}"
                ),
            }
        )
    return result
