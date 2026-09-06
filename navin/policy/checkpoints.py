"""Versioned adapters under ``.navin/policy/checkpoints`` and the active pointer.

A checkpoint is one JSON file, ``adapter-0007.json``: the head's counts plus
the metadata of the run that produced it (rows, battery and held-out
versions, metrics, verdicts, who asked, where it came from). Checkpoints are
never rewritten. ``active.json`` says which one serves (N) and which one
served before, so a rollback is one small write: back to N-1, the newer
adapter stays on disk as evidence. ``forced`` marks a flat adapter a human
activated on purpose (S4.5); the gate reads it.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navin.policy.model import PolicyHead
from navin.policy.paths import active_path, checkpoint_file, checkpoint_number, checkpoints_dir
from navin.policy.trajectory import now_stamp

KEEP_CHECKPOINTS = 6


@dataclass(slots=True)
class Checkpoint:
    number: int
    trained_at: str
    rows: int
    episodes: int
    battery_version: str
    heldout_version: str
    metrics: dict[str, Any]
    baseline: dict[str, Any]
    verdict_vs_baseline: dict[str, Any] | None
    verdict_vs_active: dict[str, Any] | None
    actor: str
    model: PolicyHead
    source: str = "trained"  # trained | adopted
    budget: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "trained_at": self.trained_at,
            "rows": self.rows,
            "episodes": self.episodes,
            "battery_version": self.battery_version,
            "heldout_version": self.heldout_version,
            "metrics": self.metrics,
            "baseline": self.baseline,
            "verdict_vs_baseline": self.verdict_vs_baseline,
            "verdict_vs_active": self.verdict_vs_active,
            "actor": self.actor,
            "source": self.source,
            "actions": len(self.model.known_actions()),
        }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_checkpoint_numbers(workspace: Path | str) -> list[int]:
    folder = checkpoints_dir(workspace)
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    numbers = [checkpoint_number(folder / name) for name in names]
    return sorted(n for n in numbers if n is not None)


def next_checkpoint_number(workspace: Path | str) -> int:
    numbers = list_checkpoint_numbers(workspace)
    return (numbers[-1] + 1) if numbers else 1


def checkpoint_payload(checkpoint: Checkpoint) -> dict[str, Any]:
    payload = {"v": 1, **checkpoint.summary(), "budget": checkpoint.budget, "model": checkpoint.model.to_dict()}
    payload.pop("actions", None)
    return payload


def save_checkpoint(workspace: Path | str, checkpoint: Checkpoint) -> Path:
    path = checkpoint_file(workspace, checkpoint.number)
    _write_json(path, checkpoint_payload(checkpoint))
    return path


def checkpoint_from_payload(data: dict[str, Any], *, number: int | None = None) -> Checkpoint:
    model_data = data.get("model")
    return Checkpoint(
        number=int(data.get("number") or number or 0),
        trained_at=str(data.get("trained_at") or ""),
        rows=int(data.get("rows") or 0),
        episodes=int(data.get("episodes") or 0),
        battery_version=str(data.get("battery_version") or ""),
        heldout_version=str(data.get("heldout_version") or ""),
        metrics=data.get("metrics") if isinstance(data.get("metrics"), dict) else {},
        baseline=data.get("baseline") if isinstance(data.get("baseline"), dict) else {},
        verdict_vs_baseline=data.get("verdict_vs_baseline") if isinstance(data.get("verdict_vs_baseline"), dict) else None,
        verdict_vs_active=data.get("verdict_vs_active") if isinstance(data.get("verdict_vs_active"), dict) else None,
        actor=str(data.get("actor") or "auto"),
        model=PolicyHead.from_dict(model_data if isinstance(model_data, dict) else {}),
        source=str(data.get("source") or "trained"),
        budget=data.get("budget") if isinstance(data.get("budget"), dict) else {},
    )


def load_checkpoint(workspace: Path | str, number: int) -> Checkpoint | None:
    data = _read_json(checkpoint_file(workspace, number))
    if data is None:
        return None
    return checkpoint_from_payload(data, number=number)


def checkpoint_summaries(workspace: Path | str) -> list[dict[str, Any]]:
    """Metadata of every checkpoint on disk, oldest first, without the counts."""
    out: list[dict[str, Any]] = []
    active = read_active(workspace)
    for number in list_checkpoint_numbers(workspace):
        data = _read_json(checkpoint_file(workspace, number))
        if data is None:
            continue
        model = data.get("model")
        actions = model.get("actions") if isinstance(model, dict) else None
        out.append(
            {
                "number": number,
                "trained_at": data.get("trained_at"),
                "rows": data.get("rows"),
                "episodes": data.get("episodes"),
                "battery_version": data.get("battery_version"),
                "heldout_version": data.get("heldout_version"),
                "metrics": data.get("metrics"),
                "baseline": data.get("baseline"),
                "verdict_vs_baseline": data.get("verdict_vs_baseline"),
                "verdict_vs_active": data.get("verdict_vs_active"),
                "actor": data.get("actor"),
                "source": data.get("source") or "trained",
                "actions": len(actions) if isinstance(actions, dict) else 0,
                "active": active is not None and active.get("checkpoint") == number,
                "previous": active is not None and active.get("previous") == number,
                "forced": bool(active is not None and active.get("checkpoint") == number and active.get("forced")),
            }
        )
    return out


def prune_checkpoints(workspace: Path | str, *, keep: int = KEEP_CHECKPOINTS) -> list[int]:
    """Drop the oldest checkpoints beyond ``keep``, never the active or previous one."""
    numbers = list_checkpoint_numbers(workspace)
    if len(numbers) <= keep:
        return []
    active = read_active(workspace) or {}
    protected = {active.get("checkpoint"), active.get("previous")}
    removed: list[int] = []
    for number in numbers[: len(numbers) - keep]:
        if number in protected:
            continue
        try:
            os.unlink(checkpoint_file(workspace, number))
            removed.append(number)
        except OSError:
            pass
    return removed


# --------------------------------------------------------------------------
# Active pointer
# --------------------------------------------------------------------------


def read_active(workspace: Path | str) -> dict[str, Any] | None:
    data = _read_json(active_path(workspace))
    if data is None or not isinstance(data.get("checkpoint"), int):
        return None
    return data


def activate(
    workspace: Path | str,
    number: int,
    *,
    heldout_version: str,
    actor: str,
    forced: bool = False,
) -> dict[str, Any]:
    current = read_active(workspace)
    payload = {
        "checkpoint": number,
        "previous": current.get("checkpoint") if current is not None and current.get("checkpoint") != number else None,
        "heldout_version": heldout_version,
        "activated_at": now_stamp(),
        "actor": actor,
        "forced": forced,
    }
    _write_json(active_path(workspace), payload)
    _invalidate()
    return payload


def rollback_active(workspace: Path | str, *, actor: str) -> dict[str, Any] | None:
    """Back to the previous checkpoint; ``None`` when nothing serves afterwards."""
    current = read_active(workspace)
    if current is None:
        return None
    previous = current.get("previous")
    if not isinstance(previous, int):
        candidates = [n for n in list_checkpoint_numbers(workspace) if n < int(current["checkpoint"])]
        previous = candidates[-1] if candidates else None
    if previous is None or load_checkpoint(workspace, previous) is None:
        try:
            os.unlink(active_path(workspace))
        except OSError:
            pass
        _invalidate()
        return None
    older = [n for n in list_checkpoint_numbers(workspace) if n < previous]
    checkpoint = load_checkpoint(workspace, previous)
    payload = {
        "checkpoint": previous,
        "previous": older[-1] if older else None,
        "heldout_version": checkpoint.heldout_version if checkpoint else current.get("heldout_version"),
        "activated_at": now_stamp(),
        "actor": actor,
        "forced": False,
        "rolled_back_from": current.get("checkpoint"),
    }
    _write_json(active_path(workspace), payload)
    _invalidate()
    return payload


# --------------------------------------------------------------------------
# Serving cache
# --------------------------------------------------------------------------

_ACTIVE_CACHE: dict[str, tuple[int, int, Checkpoint | None]] = {}
_ACTIVE_LOCK = threading.Lock()


def _invalidate() -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_CACHE.clear()


def active_checkpoint(workspace: Path | str) -> Checkpoint | None:
    """The serving adapter, reloaded only when ``active.json`` changes."""
    path = active_path(workspace)
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = str(path)
    cached = _ACTIVE_CACHE.get(key)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    pointer = read_active(workspace)
    checkpoint = load_checkpoint(workspace, int(pointer["checkpoint"])) if pointer is not None else None
    with _ACTIVE_LOCK:
        _ACTIVE_CACHE[key] = (stat.st_mtime_ns, stat.st_size, checkpoint)
    return checkpoint


def clear_checkpoint_cache() -> None:
    _invalidate()
