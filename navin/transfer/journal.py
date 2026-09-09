# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The S5 journal and scoreboard: append-only JSONL under ``.navin/transfer/``.

S5 has no per-turn writer: campaigns and dossiers are jobs, so writes are
synchronous and rare. Nothing here is ever read by a prompt.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from navin.transfer.paths import campaigns_path, journal_path

_MAX_JOURNAL_BYTES = 2 * 1024 * 1024
_MAX_CAMPAIGNS_BYTES = 8 * 1024 * 1024


def now_stamp(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


def _append(path: Path, record: dict[str, Any], *, cap: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    try:
        if path.exists() and path.stat().st_size > cap:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line)


def _read(path: Path, *, limit: int) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def journal(workspace: Path | str, event: str, **fields: Any) -> None:
    _append(journal_path(workspace), {"ts": now_stamp(), "event": event, **fields}, cap=_MAX_JOURNAL_BYTES)


def read_journal(workspace: Path | str, *, limit: int = 50) -> list[dict[str, Any]]:
    return _read(journal_path(workspace), limit=limit)


def append_campaign(workspace: Path | str, record: dict[str, Any]) -> None:
    _append(campaigns_path(workspace), record, cap=_MAX_CAMPAIGNS_BYTES)


def read_campaigns(workspace: Path | str, *, limit: int = 20) -> list[dict[str, Any]]:
    return _read(campaigns_path(workspace), limit=limit)


def latest_campaign(workspace: Path | str) -> dict[str, Any] | None:
    rows = read_campaigns(workspace, limit=1)
    return rows[-1] if rows else None
