# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin Bench results API: read the latest mission_bench JSON report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class BenchError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _root() -> Path:
    from navin.config.paths import get_runtime_subdir

    return get_runtime_subdir("bench")


def handle_bench_action(action: str, body: dict[str, Any], root: Path | None = None) -> dict:
    base = root if root is not None else _root()
    if action not in ("latest", "snapshot"):
        raise BenchError(f"unknown bench action: {action}")
    path = base / "latest.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"report": None}
    return {"report": report if isinstance(report, dict) else None}
