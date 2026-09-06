"""Train / held-out split and the frozen exam set (S3.3).

Every trajectory belongs to one bucket, decided once and forever by the
hash of its id: about one call in five is *held out*. Held-out rows are
never trained on, frozen or not, so a later freeze can only add rows the
model never saw.

``freeze_heldout`` snapshots the held-out rows into ``heldout.jsonl``: a
header line with the version (hash of the row ids) followed by the rows.
Every exam runs on that file and re-hashes it first: an edited, trimmed or
reordered set raises ``HeldoutTamperedError``. Lowering the error by
reworking the exam is the cheat S2.3 forbids for skills; the same rule holds
here. A new freeze is a new version, and scores of different versions are
never compared.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navin.world_model.paths import heldout_path
from navin.world_model.trajectory import now_stamp, record_is_valid

HELDOUT_EVERY = 5  # one row in five
HELDOUT_HEADER_VERSION = 1
MIN_HELDOUT_ROWS = 8


class HeldoutTamperedError(RuntimeError):
    """The frozen set on disk no longer matches its recorded version."""


def split_of(row_id: str) -> str:
    """``"heldout"`` or ``"train"``, stable for a given row id."""
    digest = hashlib.sha1(str(row_id).encode()).digest()
    return "heldout" if digest[0] % HELDOUT_EVERY == 0 else "train"


def heldout_version_of(rows: list[dict[str, Any]]) -> str:
    joined = "\n".join(str(r.get("id") or "") for r in rows)
    return hashlib.sha256(joined.encode()).hexdigest()[:12]


@dataclass(slots=True)
class Heldout:
    version: str
    frozen_at: str
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ids(self) -> set[str]:
        return {str(r.get("id")) for r in self.rows}

    def describe(self) -> dict[str, Any]:
        classes: dict[str, int] = {}
        tools: dict[str, int] = {}
        for row in self.rows:
            classes[str(row.get("cls"))] = classes.get(str(row.get("cls")), 0) + 1
            tools[str(row.get("tool"))] = tools.get(str(row.get("tool")), 0) + 1
        return {
            "version": self.version,
            "frozen_at": self.frozen_at,
            "rows": len(self.rows),
            "classes": dict(sorted(classes.items())),
            "tools": dict(sorted(tools.items(), key=lambda kv: -kv[1])[:8]),
        }


def heldout_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if split_of(str(r.get("id") or "")) == "heldout"]


def training_rows(rows: list[dict[str, Any]], heldout: Heldout | None) -> list[dict[str, Any]]:
    """Rows the trainer may see: train bucket only, frozen ids excluded twice over."""
    frozen = heldout.ids if heldout is not None else set()
    return [r for r in rows if split_of(str(r.get("id") or "")) == "train" and str(r.get("id")) not in frozen]


def freeze_heldout(workspace: Path | str, rows: list[dict[str, Any]], *, now: float | None = None) -> Heldout:
    """Snapshot the held-out bucket of ``rows`` into ``heldout.jsonl``."""
    chosen = heldout_candidates(rows)
    if len(chosen) < MIN_HELDOUT_ROWS:
        raise ValueError(f"not enough held-out calls to freeze: {len(chosen)} < {MIN_HELDOUT_ROWS}")
    heldout = Heldout(version=heldout_version_of(chosen), frozen_at=now_stamp(now), rows=chosen)
    path = heldout_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "heldout_header": HELDOUT_HEADER_VERSION,
        "version": heldout.version,
        "frozen_at": heldout.frozen_at,
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
    return Heldout(version=version, frozen_at=str(header.get("frozen_at") or ""), rows=rows)
