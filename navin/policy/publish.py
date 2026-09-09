# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Publishing an adapter outside its project (S4.5): a human click, never automatic.

``publish`` copies the project's **active** adapter into the machine-level
folder ``<data_dir>/policy/published/`` under a stable name and records it
in ``index.json``. Nothing else changes: no other project picks it up by
itself. ``adopt`` is the other half, also human: it saves a published
adapter as a new checkpoint of the current project and puts it through the
same exam as a trained one; it serves only when it beats the reference with
no suite down.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from navin.policy import checkpoints as ck
from navin.policy.dataset import HeldoutTamperedError, load_heldout, training_rows
from navin.policy.journal import journal, read_steps
from navin.policy.model import compare, evaluate
from navin.policy.paths import published_dir
from navin.policy.settings import read_settings
from navin.policy.trajectory import now_stamp

HUMAN = "human"
INDEX_NAME = "index.json"
MAX_PUBLISHED = 50


def _index_path() -> Path:
    return published_dir() / INDEX_NAME


def read_published() -> list[dict[str, Any]]:
    try:
        with open(_index_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    items = data.get("adapters") if isinstance(data, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _write_index(items: list[dict[str, Any]]) -> None:
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"schema_version": 1, "adapters": items[:MAX_PUBLISHED]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def publish_name(workspace: Path, number: int) -> str:
    digest = hashlib.sha1(str(workspace.resolve()).encode()).hexdigest()[:8]
    return f"{workspace.name or 'project'}-{digest}-{number:04d}"


def publish(workspace: Path | str, *, actor: str, note: str = "") -> dict[str, Any]:
    """Copy the active adapter to the machine folder. Refused without a human."""
    workspace = Path(workspace)
    if actor != HUMAN:
        return {"status": "refused", "reason": "publishing an adapter outside this project needs a human"}
    if not read_settings(workspace).enabled:
        return {"status": "skipped", "reason": "policy learning is off for this project"}
    active = ck.active_checkpoint(workspace)
    if active is None:
        return {"status": "no_active", "reason": "no active adapter to publish"}
    name = publish_name(workspace, active.number)
    folder = published_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{name}.json"
    payload = {
        **ck.checkpoint_payload(active),
        "published_at": now_stamp(),
        "published_from": str(workspace.resolve()),
        "name": name,
        "note": note[:200],
    }
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    items = [item for item in read_published() if item.get("name") != name]
    items.insert(
        0,
        {
            "name": name,
            "file": target.name,
            "published_at": payload["published_at"],
            "from": payload["published_from"],
            "checkpoint": active.number,
            "battery_version": active.battery_version,
            "heldout_version": active.heldout_version,
            "metrics": active.metrics,
            "note": payload["note"],
        },
    )
    _write_index(items)
    journal(workspace, "published", checkpoint=active.number, name=name, actor=actor)
    return {"status": "published", "name": name, "file": str(target), "checkpoint": active.number}


def unpublish(name: str, *, actor: str, workspace: Path | str | None = None) -> dict[str, Any]:
    if actor != HUMAN:
        return {"status": "refused", "reason": "unpublishing needs a human"}
    items = read_published()
    kept = [item for item in items if item.get("name") != name]
    if len(kept) == len(items):
        return {"status": "not_found", "name": name}
    try:
        os.unlink(published_dir() / f"{name}.json")
    except OSError:
        pass
    _write_index(kept)
    if workspace is not None:
        journal(workspace, "unpublished", name=name, actor=actor)
    return {"status": "unpublished", "name": name}


def adopt(workspace: Path | str, name: str, *, actor: str) -> dict[str, Any]:
    """Save a published adapter as a new checkpoint here; serve it only if it passes the exam."""
    workspace = Path(workspace)
    if actor != HUMAN:
        return {"status": "refused", "reason": "adopting an adapter needs a human"}
    if not read_settings(workspace).enabled:
        return {"status": "skipped", "reason": "policy learning is off for this project"}
    path = published_dir() / f"{name}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {"status": "not_found", "name": name}
    if not isinstance(data, dict):
        return {"status": "not_found", "name": name}
    number = ck.next_checkpoint_number(workspace)
    imported = ck.checkpoint_from_payload(data, number=number)
    imported.number = number
    imported.actor = actor
    imported.source = f"adopted:{name}"
    imported.trained_at = now_stamp()
    verdict_vs_baseline = None
    verdict_vs_active = None
    activated = False
    heldout_version = ""
    try:
        heldout = load_heldout(workspace)
    except HeldoutTamperedError as exc:
        return {"status": "tampered", "reason": str(exc)}
    if heldout is not None:
        from navin.policy.train import _best_baseline

        heldout_version = heldout.version
        rows = read_steps(workspace)
        train_rows = training_rows(rows, heldout)
        metrics = evaluate(imported.model, heldout.rows)
        _, baseline = _best_baseline(train_rows, heldout.rows)
        verdict_vs_baseline = compare(metrics, baseline)
        active = ck.active_checkpoint(workspace)
        if active is not None:
            verdict_vs_active = compare(metrics, evaluate(active.model, heldout.rows))
        imported.metrics = metrics.as_dict()
        imported.baseline = baseline.as_dict()
        imported.verdict_vs_baseline = verdict_vs_baseline.as_dict()
        imported.verdict_vs_active = verdict_vs_active.as_dict() if verdict_vs_active else None
        imported.heldout_version = heldout_version
        reference = verdict_vs_active or verdict_vs_baseline
        activated = reference.eligible
    else:
        imported.metrics = {}
        imported.verdict_vs_baseline = None
        imported.verdict_vs_active = None
        imported.heldout_version = ""
    ck.save_checkpoint(workspace, imported)
    if activated:
        ck.activate(workspace, number, heldout_version=heldout_version, actor=actor)
    journal(
        workspace,
        "adopted",
        name=name,
        checkpoint=number,
        activated=activated,
        verdict=(verdict_vs_active or verdict_vs_baseline).overall if (verdict_vs_active or verdict_vs_baseline) else None,
        actor=actor,
    )
    return {
        "status": "adopted",
        "name": name,
        "checkpoint": number,
        "activated": activated,
        "verdict_vs_baseline": verdict_vs_baseline.as_dict() if verdict_vs_baseline else None,
        "verdict_vs_active": verdict_vs_active.as_dict() if verdict_vs_active else None,
        "reason": None if heldout is not None else "no frozen exam set here yet: saved, not judged, not active",
    }
