# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Deliverability guards: domains that accept mail, and the sending window.

The MX check is a tiny DNS query over UDP to public resolvers (no extra
dependency). It answers ``True`` (the domain takes mail), ``False`` (it
cannot: no MX and no address) or ``None`` (could not tell). Only a clear
``False`` skips a contact; a network problem never blocks a campaign.
"""

from __future__ import annotations

import random
import socket
import struct
import time
from datetime import datetime
from typing import Any

_RESOLVERS = ("1.1.1.1", "8.8.8.8")
_CACHE: dict[str, tuple[float, bool | None]] = {}
_TTL = 24 * 3600


def _query(domain: str, qtype: int, server: str, timeout: float = 2.5) -> int | None:
    """Number of answers for (domain, qtype), or None when unanswered. -1 = NXDOMAIN."""
    ident = random.randint(0, 0xFFFF)
    header = struct.pack(">HHHHHH", ident, 0x0100, 1, 0, 0, 0)
    question = b"".join(bytes([len(p)]) + p.encode("idna") for p in domain.strip(".").split(".")) + b"\x00"
    packet = header + question + struct.pack(">HH", qtype, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendto(packet, (server, 53))
            data, _ = sock.recvfrom(4096)
        except OSError:
            return None
    if len(data) < 12:
        return None
    rid, flags, _qd, answers, _ns, _ar = struct.unpack(">HHHHHH", data[:12])
    if rid != ident:
        return None
    rcode = flags & 0x000F
    if rcode == 3:
        return -1
    if rcode != 0:
        return None
    return answers


def domain_accepts_mail(domain: str) -> bool | None:
    domain = (domain or "").strip().lower()
    if not domain or "." not in domain:
        return False
    cached = _CACHE.get(domain)
    if cached and time.time() - cached[0] < _TTL:
        return cached[1]
    verdict: bool | None = None
    for server in _RESOLVERS:
        mx = _query(domain, 15, server)
        if mx is None:
            continue
        if mx == -1:
            verdict = False
        elif mx > 0:
            verdict = True
        else:
            address = _query(domain, 1, server)
            verdict = None if address is None else bool(address and address > 0)
        break
    _CACHE[domain] = (time.time(), verdict)
    return verdict


def in_send_window(window: dict[str, Any] | None, now: datetime | None = None) -> bool:
    """True when sending is allowed now (window off, or inside days and hours)."""
    if not window or not window.get("enabled"):
        return True
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(window.get("timezone") or "UTC")
    except Exception:  # noqa: BLE001 - unknown zone: fall back to UTC
        from datetime import timezone as _tz

        tz = _tz.utc
    local = (now or datetime.now(tz)).astimezone(tz)
    days = [int(d) for d in window.get("days") or [1, 2, 3, 4, 5]]
    if local.isoweekday() not in days:
        return False
    try:
        start_h, start_m = (int(x) for x in str(window.get("start") or "09:00").split(":"))
        end_h, end_m = (int(x) for x in str(window.get("end") or "18:00").split(":"))
    except ValueError:
        return True
    minutes = local.hour * 60 + local.minute
    return start_h * 60 + start_m <= minutes < end_h * 60 + end_m
