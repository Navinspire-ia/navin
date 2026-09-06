"""Fan-out Marketing alerts to the WebUI centre and enabled desk channels."""

from __future__ import annotations

from typing import Any

from navin.bus.notify import notify
from navin.marketing.store import MarketingStore


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
    store: MarketingStore | None,
    *,
    title: str,
    detail: str,
    level: str = "warn",
) -> dict[str, Any]:
    """Best-effort fan-out. A closed channel never fails watch."""
    settings = store.load_settings() if store else {}
    raw = settings.get("channels") if isinstance(settings.get("channels"), dict) else {}
    body = f"{title}\n\n{detail}".strip()
    result = {
        "webui": notify(
            title=title, detail=detail, level=level, source="marketing", key="marketing-alert"
        ),
        "telegram": False,
        "whatsapp": False,
        "email": False,
    }
    if raw.get("telegram"):
        result["telegram"] = _put_outbound("telegram", str(raw.get("telegram_to") or ""), body)
    if raw.get("whatsapp"):
        result["whatsapp"] = _put_outbound("whatsapp", str(raw.get("whatsapp_to") or ""), body)
    if raw.get("email"):
        result["email"] = _send_email(str(raw.get("email_to") or ""), title, detail)
    if store:
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
