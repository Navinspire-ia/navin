# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Whether a desk loop has anything to do, without importing the desk.

The gateway supervises the Career, Leads, Marketing, Tenders, Trading and
Campaigns loops. Each used to import its desk and run a tick in a worker
thread every 20 s even while paused, which kept every desk module loaded in
the process that also serves Navin Code. Each desk is an isolated module: its
loop only wakes once the user starts it, and this check reads one small file.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from navin.config.paths import get_data_dir

#: Desks whose loop is armed by ``loop.json`` ("enabled": true), written by
#: ``start_loop`` / ``stop_loop`` (Studio Start loop, Tauri, ``navin <desk>``).
LOOP_DESKS: tuple[str, ...] = ("career", "leads", "marketing", "tenders", "trading")

#: Campaign statuses the dispatcher still has to send or read replies for.
_LIVE_CAMPAIGN_STATUSES = ("scheduled", "sending", "sent")


def desk_loop_armed(desk: str, root: Path | None = None) -> bool:
    """True when the desk's loop was started (``loop.json`` says enabled)."""
    path = (root or get_data_dir()) / desk / "loop.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(state, dict) and bool(state.get("enabled"))


def campaigns_have_work(root: Path | None = None) -> bool:
    """True when a campaign is scheduled, sending, or awaiting replies."""
    path = (root or get_data_dir()) / "campaigns" / "campaigns.sqlite"
    if not path.is_file():
        return False
    marks = ",".join("?" for _ in _LIVE_CAMPAIGN_STATUSES)
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as conn:
            row = conn.execute(
                f"SELECT 1 FROM campaigns WHERE status IN ({marks}) LIMIT 1",
                _LIVE_CAMPAIGN_STATUSES,
            ).fetchone()
    except sqlite3.Error:
        # A locked or half-migrated database: let the dispatcher decide.
        return True
    return row is not None
