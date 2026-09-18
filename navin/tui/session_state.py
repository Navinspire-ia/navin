# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Durable unsent work, separate from the engine's conversation history."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from navin.utils.atomic_io import atomic_write_text


class TuiSessionStore:
    def __init__(self, root: Path, workspace: Path) -> None:
        self.root = root
        self.workspace = str(workspace.expanduser().resolve())

    def path(self, key: str) -> Path:
        digest = hashlib.sha256(f"{self.workspace}\0{key}".encode()).hexdigest()
        return self.root / f"{digest}.json"

    def load(self, key: str) -> dict[str, Any]:
        try:
            data = json.loads(self.path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict) or data.get("session") != key:
            return {}
        return data

    def save(self, key: str, *, draft: str, queue: list[dict[str, Any]], paused: bool) -> None:
        atomic_write_text(self.path(key), json.dumps({
            "session": key, "draft": draft, "queue": queue, "paused": paused,
        }, ensure_ascii=False))
