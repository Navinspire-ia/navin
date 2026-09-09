# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Alert the user on Telegram, WhatsApp, email, and the WebUI centre."""

from __future__ import annotations

from typing import Any

from navin.bus.notify import notify
from navin.trading.store import TradingStore


def channel_readiness() -> dict[str, Any]:
    try:
        from navin.crm.outreach import channel_status
    except Exception:
        channel_status = None  # type: ignore[assignment]
    email = channel_status("email") if channel_status else {"ready": False, "enabled": False, "hint": ""}
    whatsapp = channel_status("whatsapp") if channel_status else {"ready": False, "enabled": False, "hint": ""}
    telegram = _telegram_status()
    return {
        "telegram": telegram,
        "whatsapp": whatsapp,
        "email": email,
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
            "hint": "Reglages > Canaux > Telegram. Colle le bot token puis le chat id sur le desk.",
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
    store: TradingStore,
    *,
    title: str,
    detail: str,
    level: str = "info",
) -> dict[str, Any]:
    """Best-effort fan-out. A closed channel never fails the loop."""
    mandate = (store.load_settings().get("mandate") or {}) if store else {}
    channels = mandate.get("channels") if isinstance(mandate.get("channels"), dict) else {}
    body = f"{title}\n\n{detail}".strip()
    result = {
        "webui": notify(title=title, detail=detail, level=level, source="trading", key="trading-alert"),
        "telegram": False,
        "whatsapp": False,
        "email": False,
    }
    if channels.get("telegram"):
        result["telegram"] = _put_outbound("telegram", str(channels.get("telegram_to") or ""), body)
    if channels.get("whatsapp"):
        result["whatsapp"] = _put_outbound("whatsapp", str(channels.get("whatsapp_to") or ""), body)
    if channels.get("email"):
        result["email"] = _send_email(str(channels.get("email_to") or ""), title, detail)
    store.append_journal(
        {
            "kind": "alert",
            "text": (
                f"{title} - webui={int(result['webui'])} "
                f"telegram={int(result['telegram'])} "
                f"whatsapp={int(result['whatsapp'])} "
                f"email={int(result['email'])}"
            ),
        }
    )
    return result


def alert_cycle(store: TradingStore, tick: dict[str, Any]) -> dict[str, Any] | None:
    orders = [row for row in (tick.get("orders") or []) if isinstance(row, dict)]
    stops = [row for row in (tick.get("stops") or []) if isinstance(row, dict)]
    pending = [row for row in orders if row.get("status") == "pending"]
    blocked = [row for row in orders if row.get("status") == "blocked"]
    filled = [row for row in orders if row.get("status") == "filled"]
    if not (pending or blocked or filled or stops):
        return None
    parts = [str(tick.get("reason") or "cycle")]
    if pending:
        parts.append(
            "Validation: "
            + ", ".join(f"{row.get('side')} {row.get('qty')} {row.get('symbol')}" for row in pending[:6])
        )
    if filled:
        parts.append("Fills papier: " + ", ".join(str(row.get("symbol")) for row in filled[:6]))
    if blocked:
        parts.append("Risque a bloque: " + ", ".join(str(row.get("symbol")) for row in blocked[:6]))
    if stops:
        parts.append("Stops: " + ", ".join(str(row.get("symbol")) for row in stops[:6]))
    parts.append("Ouvre #/trading pour approuver ou ajuster le mandat.")
    level = "warning" if (pending or blocked) else "success"
    return deliver_alert(store, title="Trading Agent OS", detail="\n".join(parts), level=level)
