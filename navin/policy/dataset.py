# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The frozen exam set of the policy (S4.3).

The battery already says which cases are held out; this module freezes the
steps those cases produced into ``heldout.jsonl`` (header + rows) with a
version (hash of the step ids). Every exam runs on that file and re-hashes
it first: an edited, trimmed or reordered set raises
``HeldoutTamperedError``. Training rows are the ``train`` split minus any id
that ever landed in the frozen set, so N and N+1 are always judged on steps
neither saw.

A new freeze is a new version (human action, or the first training run);
scores of different versions are never compared.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navin.policy.paths import heldout_path
from navin.policy.trajectory import now_stamp, record_is_valid

HELDOUT_HEADER_VERSION = 1
MIN_HELDOUT_ROWS = 4


class HeldoutTamperedError(RuntimeError):
    """The frozen set on disk no longer matches its recorded version."""


def heldout_version_of(rows: list[dict[str, Any]]) -> str:
    """Hash of the whole content of the frozen rows, not only their ids.

    Editing one action, one reward or one state in the exam set (to make an
    adapter look better) changes the version, and the file is refused.
    """
    joined = "\n".join(json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False) for r in rows)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


@dataclass(slots=True)
class Heldout:
    version: str
    frozen_at: str
    battery_version: str
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ids(self) -> set[str]:
        return {str(r.get("id")) for r in self.rows}

    def describe(self) -> dict[str, Any]:
        suites: dict[str, int] = {}
        episodes: set[str] = set()
        passed: set[str] = set()
        for row in self.rows:
            suites[str(row.get("suite"))] = suites.get(str(row.get("suite")), 0) + 1
            episodes.add(str(row.get("episode")))
            if float(row.get("reward") or 0.0) > 0:
                passed.add(str(row.get("episode")))
        return {
            "version": self.version,
            "frozen_at": self.frozen_at,
            "battery_version": self.battery_version,
            "rows": len(self.rows),
            "episodes": len(episodes),
            "passed_episodes": len(passed),
            "suites": dict(sorted(suites.items())),
        }


def heldout_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("split") == "heldout"]


def latest_episodes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One episode per case: the most recent run. The battery is deterministic,
    so re-running it must not count the same trajectory twice."""
    latest: dict[str, str] = {}
    for row in rows:
        latest[str(row.get("case"))] = str(row.get("episode"))
    return [r for r in rows if str(r.get("episode")) == latest.get(str(r.get("case")))]


def training_rows(rows: list[dict[str, Any]], heldout: Heldout | None) -> list[dict[str, Any]]:
    """Rows the trainer may see: train split only, latest run per case, frozen
    ids and frozen cases excluded twice over."""
    frozen = heldout.ids if heldout is not None else set()
    frozen_cases = {str(r.get("case")) for r in heldout.rows} if heldout is not None else set()
    return [
        r
        for r in latest_episodes(rows)
        if r.get("split") == "train" and str(r.get("id")) not in frozen and str(r.get("case")) not in frozen_cases
    ]


def freeze_heldout(
    workspace: Path | str,
    rows: list[dict[str, Any]],
    *,
    battery_version: str,
    now: float | None = None,
) -> Heldout:
    """Snapshot the latest held-out episodes of ``rows`` into ``heldout.jsonl``.

    Only the most recent run of each held-out case is kept, so re-running the
    battery does not inflate the exam with copies of the same episode.
    """
    chosen = latest_episodes(heldout_candidates(rows))
    if len(chosen) < MIN_HELDOUT_ROWS:
        raise ValueError(f"not enough held-out steps to freeze: {len(chosen)} < {MIN_HELDOUT_ROWS}")
    heldout = Heldout(version=heldout_version_of(chosen), frozen_at=now_stamp(now), battery_version=battery_version, rows=chosen)
    path = heldout_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "heldout_header": HELDOUT_HEADER_VERSION,
        "version": heldout.version,
        "frozen_at": heldout.frozen_at,
        "battery_version": battery_version,
        "rows": len(chosen),
    }
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(header, separators=(",", ":")) + "\n")
        for row in chosen:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(tmp, path)
    return heldout


def load_heldout(workspace: Path | str) -> Heldout | None:
    """The frozen set, verified. ``None`` when nothing was frozen yet."""
    path = heldout_path(workspace)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if not lines:
        return None
    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise HeldoutTamperedError("held-out header unreadable") from exc
    if not isinstance(header, dict) or "heldout_header" not in header:
        raise HeldoutTamperedError("held-out header missing")
    rows: list[dict[str, Any]] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HeldoutTamperedError("held-out row unreadable") from exc
        if not record_is_valid(record):
            raise HeldoutTamperedError("held-out row invalid")
        rows.append(record)
    version = str(header.get("version") or "")
    if header.get("rows") != len(rows) or heldout_version_of(rows) != version:
        raise HeldoutTamperedError(
            f"held-out set {version} was modified on disk; freeze a new version instead of editing the exam"
        )
    return Heldout(
        version=version,
        frozen_at=str(header.get("frozen_at") or ""),
        battery_version=str(header.get("battery_version") or ""),
        rows=rows,
    )
