# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Read back the desktop audit trail written by ``navin.computer.safety.AuditLog``.

One folder per desktop session under the computer artifact directory, holding
``session.json`` (who, when, under which policy) and ``actions.jsonl`` (one row
per step). Shared by the ``navin computer audit`` CLI view and the WebUI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_SESSIONS = 200
MAX_STEPS = 2000


class AuditViewError(ValueError):
    """A request for a session that is not valid or does not exist."""


def _read_rows(log: Path) -> tuple[list[dict[str, Any]], int]:
    """Rows of ``actions.jsonl`` plus the count of unreadable lines."""
    rows: list[dict[str, Any]] = []
    broken = 0
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            broken += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            broken += 1
    return rows, broken


def _read_meta(folder: Path) -> dict[str, Any]:
    path = folder / "session.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("computer audit: unreadable {}: {}", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _step(row: dict[str, Any]) -> dict[str, Any]:
    shot = row.get("screenshot")
    return {
        "step": row.get("step"),
        "t": row.get("t"),
        "action": str(row.get("action") or ""),
        "args": row.get("args") if isinstance(row.get("args"), dict) else {},
        "result": str(row.get("result") or "")[:500],
        "denied": str(row.get("denied") or "")[:500],
        "rule": str(row.get("rule") or ""),
        "screenshot": Path(str(shot)).name if shot else "",
    }


def list_sessions(root: Path, limit: int = 20) -> list[dict[str, Any]]:
    """Recorded sessions, most recent first, with their step and denial counts."""
    if not root.is_dir():
        return []
    folders = sorted(
        (p for p in root.iterdir() if p.is_dir() and (p / "actions.jsonl").is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[: max(1, min(limit, MAX_SESSIONS))]
    out: list[dict[str, Any]] = []
    for folder in folders:
        try:
            rows, broken = _read_rows(folder / "actions.jsonl")
        except OSError as exc:
            logger.warning("computer audit: unreadable {}: {}", folder, exc)
            continue
        meta = _read_meta(folder)
        out.append({
            "id": folder.name,
            "chat": str(meta.get("chat") or ""),
            "started": str(meta.get("started") or ""),
            "ask": str(meta.get("ask") or ""),
            "backend": str(meta.get("backend") or ""),
            "steps": len(rows),
            "denied": sum(1 for r in rows if r.get("denied")),
            "broken": broken,
        })
    return out


def read_session(root: Path, session: str, last: int = 200) -> dict[str, Any]:
    """Meta and the last ``last`` steps of one session. ``last <= 0`` reads all (capped)."""
    if not _SESSION_ID.match(session or ""):
        raise AuditViewError(f"invalid session id {session!r}")
    folder = (root / session).resolve()
    if folder.parent != root.resolve():
        raise AuditViewError(f"invalid session id {session!r}")
    log = folder / "actions.jsonl"
    if not log.is_file():
        raise AuditViewError(f"no such session: {session}")
    rows, broken = _read_rows(log)
    count = MAX_STEPS if last <= 0 else min(last, MAX_STEPS)
    return {
        "id": session,
        "meta": _read_meta(folder),
        "total": len(rows),
        "broken": broken,
        "steps": [_step(r) for r in rows[-count:]],
    }
