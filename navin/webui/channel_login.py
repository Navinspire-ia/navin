"""In-workbench channel login (QR). No terminal handoff."""

from __future__ import annotations

import asyncio
import base64
import io
from typing import Any


class ChannelLoginError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


_lock = asyncio.Lock()
_state: dict[str, dict[str, Any]] = {}
_tasks: dict[str, asyncio.Task[Any]] = {}


def qr_png_data_url(qr_data: bytes | str) -> str:
    try:
        import segno
    except ImportError as exc:
        raise ChannelLoginError(
            "WhatsApp support is not installed. Click Install support on the WhatsApp tool, then connect again.",
            status=400,
        ) from exc

    buf = io.BytesIO()
    segno.make_qr(qr_data).save(buf, kind="png", scale=8)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def login_snapshot(name: str) -> dict[str, Any]:
    key = (name or "").strip().lower()
    row = _state.get(key) or {}
    return {
        "name": key,
        "status": str(row.get("status") or "idle"),
        "qrDataUrl": row.get("qrDataUrl"),
        "error": row.get("error"),
    }


async def start_channel_login(name: str, *, force: bool = False) -> dict[str, Any]:
    key = (name or "").strip().lower()
    if key != "whatsapp":
        raise ChannelLoginError(f"channel '{name}' has no in-app login", status=400)
    try:
        from navin.channels.whatsapp import _load_neonize

        _load_neonize()
    except Exception as exc:
        raise ChannelLoginError(
            str(exc) or "WhatsApp support is not installed. Click Install support on the WhatsApp tool, then connect again.",
            status=400,
        ) from exc
    async with _lock:
        existing = _tasks.get(key)
        if existing is not None and not existing.done():
            existing.cancel()
            try:
                await existing
            except (asyncio.CancelledError, Exception):
                pass
        _state[key] = {"status": "starting", "qrDataUrl": None, "error": None}
        task = asyncio.create_task(_run_whatsapp_login(force=force), name=f"channel-login-{key}")
        _tasks[key] = task
    return login_snapshot(key)


async def cancel_channel_login(name: str) -> dict[str, Any]:
    key = (name or "").strip().lower()
    async with _lock:
        existing = _tasks.get(key)
        if existing is not None and not existing.done():
            existing.cancel()
            try:
                await existing
            except (asyncio.CancelledError, Exception):
                pass
        current = _state.get(key) or {}
        if current.get("status") not in {"connected", "idle"}:
            _state[key] = {"status": "cancelled", "qrDataUrl": None, "error": None}
    return login_snapshot(key)


async def _run_whatsapp_login(*, force: bool) -> None:
    from navin.channels.whatsapp import WhatsAppChannel
    from navin.config.loader import load_config

    def on_qr(qr_data: bytes) -> None:
        _state["whatsapp"] = {
            "status": "waiting_qr",
            "qrDataUrl": qr_png_data_url(qr_data),
            "error": None,
        }

    try:
        cfg = load_config()
        section = getattr(cfg.channels, "whatsapp", None) or {}
        channel = WhatsAppChannel(section, bus=None)
        ok = await channel.login(force=force, on_qr=on_qr)
        if ok:
            previous = _state.get("whatsapp") or {}
            _state["whatsapp"] = {
                "status": "connected",
                "qrDataUrl": previous.get("qrDataUrl"),
                "error": None,
            }
            return
        _state["whatsapp"] = {
            "status": "failed",
            "qrDataUrl": None,
            "error": "WhatsApp login did not complete.",
        }
    except asyncio.CancelledError:
        _state["whatsapp"] = {"status": "cancelled", "qrDataUrl": None, "error": None}
        raise
    except Exception as exc:
        _state["whatsapp"] = {
            "status": "failed",
            "qrDataUrl": None,
            "error": str(exc) or "WhatsApp login failed.",
        }
