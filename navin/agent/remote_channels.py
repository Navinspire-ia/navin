# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Remote control of the agent from email, WhatsApp, Telegram and Teams.

Three independent switches per channel, all off until the user turns them on:

* ``notify``  - the ``notify`` tool (errors, blockers, finished jobs) is also
  delivered to the channel destination, with the transport receipts of
  :mod:`navin.bus.alerts`.
* ``approve`` - approval requests are mirrored to the channel; the user answers
  ``OUI <code>`` / ``NON <code>`` / ``TOUJOURS <code>`` from the phone.
* ``chat``    - prompts sent from the channel reach the agent. When the user has
  explicitly turned it off, inbound prompts from that channel are ignored.

Transport credentials never live here: they come from Settings > Channels
(``config.channels.*``). This file only stores the user's choices and the
destination (chat id, phone number, email address, Teams conversation id).
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any

from filelock import FileLock
from loguru import logger

from navin.utils.atomic_io import atomic_write_text

CHANNELS = ("email", "whatsapp", "telegram", "teams")
FLAGS = ("notify", "approve", "chat")
# Gateway channel name -> remote channel name.
_GATEWAY = {"email": "email", "whatsapp": "whatsapp", "telegram": "telegram", "msteams": "teams"}
_REPLY = re.compile(
    r"^\s*/?(oui|yes|ok|allow|approve|accepter|non|no|deny|refuse|refuser|toujours|always)\s+([a-z0-9]{4,8})\s*[.!]*\s*$",
    re.IGNORECASE,
)
_ALLOW = {"oui", "yes", "ok", "allow", "approve", "accepter"}
_ALWAYS = {"toujours", "always"}
_CODE_TTL = 3600.0
# code -> {request_id, created_at, chats: {(remote_channel, chat_id)}}
_CODES: dict[str, dict[str, Any]] = {}


def _path() -> Path:
    from navin.config.paths import get_runtime_subdir

    return get_runtime_subdir("remote") / "channels.json"


def _empty() -> dict[str, Any]:
    return {name: {"notify": False, "approve": False, "chat": True, "to": "", "saved": False} for name in CHANNELS}


def _read_prefs(path: Path) -> dict[str, Any]:
    prefs = _empty()
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        raw = {}
    for name in CHANNELS:
        row = raw.get(name) if isinstance(raw, dict) else None
        if isinstance(row, dict):
            for flag in FLAGS:
                if isinstance(row.get(flag), bool):
                    prefs[name][flag] = row[flag]
            prefs[name]["to"] = str(row.get("to") or "").strip()
            prefs[name]["saved"] = True
    return prefs


# The prefs are read on the engine loop (every remote message, every approval):
# they are kept in memory and refreshed by save_prefs, the only writer, so the
# loop reads the disk at most once per file.
_CACHE: dict[str, dict[str, Any]] = {}


def load_prefs() -> dict[str, Any]:
    path = _path()
    key = str(path)
    if key not in _CACHE:
        _CACHE[key] = _read_prefs(path)
    return {name: dict(row) for name, row in _CACHE[key].items()}


def save_prefs(channel: str, **changes: Any) -> dict[str, Any]:
    channel = _GATEWAY.get(channel, channel)
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel: {channel}")
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=5):
        prefs = _read_prefs(path)
        row = prefs[channel]
        for flag in FLAGS:
            if flag in changes and changes[flag] is not None:
                row[flag] = bool(changes[flag])
        if changes.get("to") is not None:
            to = str(changes["to"]).strip()
            if len(to) > 320 or any(c in to for c in "\r\n"):
                raise ValueError("invalid destination")
            row["to"] = to
        row["saved"] = True
        data = {name: {k: v for k, v in value.items() if k != "saved"} for name, value in prefs.items() if value["saved"]}
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2), mode=0o600)
        _CACHE[str(path)] = prefs
    return status()


def status() -> dict[str, Any]:
    """Prefs merged with transport readiness, for the WebUI panel."""
    try:
        from navin.bus.alerts import channel_readiness

        ready = channel_readiness()
    except Exception:  # noqa: BLE001 - the panel must still open
        ready = {}
    prefs = load_prefs()
    rows = []
    for name in CHANNELS:
        info = ready.get(name, {})
        row = prefs[name]
        rows.append({
            "channel": name,
            "notify": row["notify"],
            "approve": row["approve"],
            "chat": row["chat"],
            "to": row["to"],
            "enabled": bool(info.get("enabled")),
            "configured": bool(info.get("configured")),
            "ready": bool(info.get("ready")),
            "hint": str(info.get("hint") or ""),
        })
    return {"channels": rows}


# -- outbound -------------------------------------------------------------


class _PrefsStore:
    """Adapter so :func:`navin.bus.alerts.deliver_alert` reads remote prefs."""

    def __init__(self, flag: str, skip: tuple[str, str] | None = None) -> None:
        self.flag = flag
        self.skip = skip
        self.root = _path().parent

    def load_profile(self) -> dict[str, Any]:
        prefs = load_prefs()
        channels: dict[str, Any] = {}
        for name in CHANNELS:
            row = prefs[name]
            if self.skip and name == self.skip[0] and _same_chat(row["to"], self.skip[1]):
                continue  # this chat already got the request from the agent loop
            if row[self.flag] and row["to"]:
                channels[name] = True
                channels[f"{name}_to"] = row["to"]
        return {"channels": channels}


def _targets(flag: str) -> list[str]:
    prefs = load_prefs()
    return [name for name in CHANNELS if prefs[name][flag] and prefs[name]["to"]]


def forward_notification(title: str, detail: str = "", level: str = "info", key: str = "") -> int:
    """Send a ``notify`` call to every channel with notifications on."""
    if not _targets("notify"):
        return 0
    from navin.bus.alerts import deliver_alert

    event_id = f"notify:{key}:{int(time.time() // 300)}" if key else f"notify:{secrets.token_hex(8)}"
    icon = {"error": "[ERREUR]", "warning": "[BLOQUE]", "success": "[OK]"}.get(level, "[INFO]")
    try:
        result = deliver_alert(_PrefsStore("notify"), module="remote", title=f"Navin {icon} {title}",
                               detail=detail, level="warn" if level == "warning" else level,
                               event_id=event_id, event_type="notify")
    except Exception:  # noqa: BLE001 - the WebUI notification already went out
        return 0
    return int(result.get("queued_count") or 0)


def _new_code(request_id: str) -> str:
    now = time.time()
    for code, entry in list(_CODES.items()):
        if now - entry["created_at"] > _CODE_TTL:
            _CODES.pop(code, None)
    code = secrets.token_hex(3)
    while code in _CODES:
        code = secrets.token_hex(3)
    _CODES[code] = {"request_id": request_id, "created_at": now, "chats": set()}
    return code


def approval_text(request: Any, code: str) -> tuple[str, str]:
    title = f"Navin demande une autorisation : {request.action or request.tool}"
    lines = []
    if request.reason:
        lines.append(f"Pourquoi : {request.reason}")
    if request.detail:
        lines.append(f"Detail : {str(request.detail)[:600]}")
    if request.consequence:
        lines.append(f"Attention : {request.consequence}")
    lines.append("")
    lines.append(f"Repondez  OUI {code}  pour autoriser,  NON {code}  pour refuser"
                 + (f",  TOUJOURS {code}  pour ne plus demander." if request.scope else "."))
    return title, "\n".join(lines)


async def publish_approval(bus: Any, request_id: str, request: Any, route: dict[str, Any], *,
                           origin_asked: bool = False) -> None:
    """Mirror an approval card to the remote channels (and to the asking chat).

    ``origin_asked``: the agent loop already sent the request, in text, to the
    chat the prompt came from (it answers there with ``/approve <id>``), so that
    chat gets no second copy, even when it is also set for approvals.
    """
    origin = _GATEWAY.get(str(route.get("channel") or ""))
    origin_chat = str(route.get("chat_id") or "")
    skip = (origin, origin_chat) if origin_asked and origin and origin_chat else None
    prefs = load_prefs()
    targets = [name for name in _targets("approve")
               if not (skip and name == skip[0] and _same_chat(prefs[name]["to"], skip[1]))]
    if not targets and (not origin or origin_asked):
        return
    code = _new_code(request_id)
    entry = _CODES[code]
    title, detail = approval_text(request, code)
    for name in targets:
        entry["chats"].add((name, prefs[name]["to"]))
    if targets:
        from navin.bus.alerts import deliver_alert

        try:
            # deliver_alert writes the outbox on disk: off the engine loop.
            await asyncio.to_thread(
                deliver_alert, _PrefsStore("approve", skip=skip), module="remote", title=title, detail=detail,
                level="warn", event_id=f"approval:{request_id}", event_type="approval",
            )
        except Exception:  # noqa: BLE001 - the WebUI card and the origin chat already have the request
            logger.opt(exception=True).warning("could not send the approval request to the remote channels")
    if origin_asked:
        return
    if origin and origin_chat and (origin, origin_chat) not in entry["chats"] and bus is not None:
        # The prompt came from this chat: ask there, in plain text.
        from navin.bus.events import OutboundMessage

        entry["chats"].add((origin, origin_chat))
        await bus.publish_outbound(OutboundMessage(channel=str(route.get("channel")), chat_id=origin_chat,
                                                   content=f"{title}\n\n{detail}"))


def can_answer(channel: str) -> bool:
    """A turn started from a remote chat can be asked there when chat is on."""
    name = _GATEWAY.get(channel)
    return bool(name and load_prefs()[name]["chat"])


def _same_chat(expected: str, got: str) -> bool:
    norm = lambda value: re.sub(r"[\s+\-()]|@s\.whatsapp\.net$|^mailto:", "", value.strip().lower())  # noqa: E731
    return bool(expected) and norm(expected) == norm(got)


def parse_reply(msg: Any) -> tuple[str, bool, bool] | None:
    """``(request_id, allowed, remember)`` for an approval answer from a remote chat."""
    name = _GATEWAY.get(str(getattr(msg, "channel", "") or ""))
    if not name:
        return None
    match = _REPLY.match(str(getattr(msg, "content", "") or "")[:64])
    if not match:
        return None
    word, code = match.group(1).lower(), match.group(2).lower()
    entry = _CODES.get(code)
    if entry is None:
        return None
    chat = str(getattr(msg, "chat_id", "") or "")
    sender = str(getattr(msg, "sender_id", "") or "")
    if not any(ch == name and (_same_chat(to, chat) or _same_chat(to, sender)) for ch, to in entry["chats"]):
        return None
    _CODES.pop(code, None)
    allowed = word in _ALLOW or word in _ALWAYS
    return entry["request_id"], allowed, word in _ALWAYS


def inbound_blocked(msg: Any) -> bool:
    """True when the user explicitly turned off prompts from this channel."""
    name = _GATEWAY.get(str(getattr(msg, "channel", "") or ""))
    if not name:
        return False
    row = load_prefs()[name]
    return row["saved"] and not row["chat"]


async def handle_remote_message(state: Any, msg: Any) -> bool:
    """Runtime-control hook: approval answers and disabled channels."""
    if _GATEWAY.get(str(getattr(msg, "channel", "") or "")) is None:
        return False
    reply = parse_reply(msg)
    if reply is not None:
        request_id, allowed, remember = reply
        broker = getattr(state, "approvals", None)
        ok = bool(broker and broker.resolve(request_id, allowed=allowed, remember=remember))
        bus = getattr(state, "bus", None)
        if bus is not None:
            from navin.bus.events import OutboundMessage

            text = ("Autorise." if allowed else "Refuse.") if ok else "Cette demande n'attend plus de reponse."
            try:
                await bus.publish_outbound(OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=text))
            except Exception:  # noqa: BLE001 - the decision is already applied; only the receipt is lost
                logger.opt(exception=True).warning("could not confirm the approval answer on {}", msg.channel)
        return True
    return inbound_blocked(msg)
