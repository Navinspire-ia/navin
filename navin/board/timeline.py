# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Time Machine: one chronological timeline per mission.

Merges registry versions (decisions), board activity (tool operations, tests)
into a single ordered list, and finds the last coherent state: the latest
registry version when every acceptance criterion is proven, else the latest
checkpoint.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from navin.board.ledger import (
    MissionLedgerStore,
    checkpoint_id_for_version,
    outcome_of,
    unproven_criteria,
)
from navin.board.store import BoardError, ProjectBoardStore

_TEST_HINTS = ("test", "pytest", "verify", "lint")
_ACTIVITY_TEXT_KEYS = ("text", "summary", "detail", "action", "kind")


def _activity_at(entry: Mapping[str, Any]) -> str:
    return str(entry.get("at") or entry.get("ts") or entry.get("timestamp") or "")


def _activity_title(entry: Mapping[str, Any]) -> str:
    return next((str(entry[k]) for k in _ACTIVITY_TEXT_KEYS if entry.get(k)), "")


def _kind_of(entry: Mapping[str, Any]) -> str:
    blob = " ".join(
        str(entry.get(k) or "") for k in ("action", "type", "kind", "text", "summary", "detail")
    ).lower()
    if any(h in blob for h in _TEST_HINTS):
        return "test"
    return "tool"


def last_coherent_state(ledger: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Latest version with all criteria proven, else the latest checkpoint."""
    if not ledger:
        return None
    history = [h for h in (ledger.get("history") or []) if isinstance(h, dict)]
    if not history:
        return None
    criteria = ledger.get("acceptance_criteria") or []
    last = history[-1]
    version = int(last.get("version") or 0)
    proven = bool(criteria) and not unproven_criteria(ledger)
    return {
        "version": version,
        "checkpoint_id": checkpoint_id_for_version(version),
        "all_criteria_proven": proven,
        "reason": "all_criteria_proven" if proven else "last_checkpoint",
    }


def build_timeline(project_path: Path | str, *, activity_limit: int = 200) -> dict[str, Any]:
    ledger = MissionLedgerStore(project_path).load()
    events: list[dict[str, Any]] = []
    if ledger:
        for h in ledger.get("history") or []:
            if not isinstance(h, dict):
                continue
            version = int(h.get("version") or 0)
            events.append(
                {
                    "kind": "decision",
                    "at": h.get("at") or "",
                    "version": version,
                    "checkpoint_id": checkpoint_id_for_version(version),
                    "title": h.get("reason") or "",
                    "actor": h.get("actor") or "",
                    "changes": list(h.get("changes") or []),
                    "restorable": str(version) in (ledger.get("snapshots") or {}),
                }
            )
    # read_activity already logs and returns [] on an unreadable file.
    activity = ProjectBoardStore(project_path).read_activity(activity_limit)
    for a in activity:
        events.append(
            {
                "kind": _kind_of(a),
                "at": _activity_at(a),
                "version": None,
                "checkpoint_id": None,
                "title": _activity_title(a),
                "actor": str(a.get("actor") or ""),
                "changes": [],
                "restorable": False,
            }
        )
    events.sort(key=lambda e: e["at"])
    steps = [
        {
            "id": s.get("id"),
            "title": s.get("title") or s.get("id"),
            "status": s.get("status") or "",
            "retry_count": int(s.get("retry_count") or 0),
        }
        for s in ((ledger or {}).get("steps") or [])
        if isinstance(s, dict) and s.get("id")
    ]
    return {
        "mission": bool(ledger),
        "events": events,
        "steps": steps,
        "last_coherent": last_coherent_state(ledger),
    }


def coherent_version(ledger: Mapping[str, Any] | None) -> int | None:
    """Latest snapshotted version whose criteria were all proven, else None."""
    if not ledger:
        return None
    snaps = ledger.get("snapshots") or {}
    for key in sorted(snaps, key=int, reverse=True):
        snap = snaps[key]
        if snap.get("acceptance_criteria") and not unproven_criteria(snap):
            return int(key)
    return None


def restore_mission(
    project_path: Path | str, target: str | int, *, reason: str = "", actor: str = "user"
) -> dict[str, Any]:
    """Restore the registry to version N or ``coherent``; returns the new version."""
    store = MissionLedgerStore(project_path)
    if str(target).strip().lower() == "coherent":
        version = coherent_version(store.load())
        if version is None:
            raise BoardError("no coherent version (all criteria proven) to restore")
    else:
        try:
            version = int(str(target).strip().lstrip("v"))
        except ValueError as exc:
            raise BoardError(f"invalid version: {target!r}") from exc
    led = store.restore_version(version, reason=reason, actor=actor)
    return {"restored_from": version, "version": led["version"], "checkpoint_id": checkpoint_id_for_version(led["version"])}


def _list_diff(a: Any, b: Any) -> dict[str, list[Any]]:
    a, b = list(a or []), list(b or [])
    return {"added": [x for x in b if x not in a], "removed": [x for x in a if x not in b]}


def diff_versions(project_path: Path | str, a: int, b: int) -> dict[str, Any]:
    """Structured diff between two registry versions (goal, criteria, proofs, steps)."""
    led = MissionLedgerStore(project_path).load()
    snaps = (led or {}).get("snapshots") or {}
    sa, sb = snaps.get(str(int(a))), snaps.get(str(int(b)))
    missing = [v for v, s in ((a, sa), (b, sb)) if not isinstance(s, dict)]
    if missing:
        raise BoardError(f"no snapshot for version(s): {', '.join(str(v) for v in missing)}")
    ev_a, ev_b = sa.get("criteria_evidence") or {}, sb.get("criteria_evidence") or {}
    steps_a = {s.get("id"): s for s in sa.get("steps") or [] if isinstance(s, dict)}
    steps_b = {s.get("id"): s for s in sb.get("steps") or [] if isinstance(s, dict)}
    return {
        "from": int(a),
        "to": int(b),
        "goal": None if sa.get("goal") == sb.get("goal") else {"from": sa.get("goal"), "to": sb.get("goal")},
        "status": None if sa.get("status") == sb.get("status") else {"from": sa.get("status"), "to": sb.get("status")},
        "criteria": _list_diff(sa.get("acceptance_criteria"), sb.get("acceptance_criteria")),
        "facts": _list_diff(sa.get("facts"), sb.get("facts")),
        "constraints": _list_diff(sa.get("constraints"), sb.get("constraints")),
        "evidence": {
            k: {"from": ev_a.get(k), "to": ev_b.get(k)}
            for k in sorted(set(ev_a) | set(ev_b))
            if ev_a.get(k) != ev_b.get(k)
        },
        "steps": {
            "added": [steps_b[k].get("title") or k for k in steps_b if k not in steps_a],
            "removed": [steps_a[k].get("title") or k for k in steps_a if k not in steps_b],
            "changed": [
                {"id": k, "title": steps_b[k].get("title") or k, "from": steps_a[k].get("status"), "to": steps_b[k].get("status")}
                for k in steps_b
                if k in steps_a and steps_a[k].get("status") != steps_b[k].get("status")
            ],
        },
    }


def replay_step(
    project_path: Path | str,
    step_id: str,
    *,
    from_version: int | None = None,
    reason: str = "",
    actor: str = "user",
) -> dict[str, Any]:
    """Run one step again: it and every step depending on it go back to planned.

    With ``from_version`` the step definition (title, acceptance, validation) is
    taken from that version's snapshot, so a step changed or dropped by a later
    replan is replayed as it was. Steps upstream keep their results. The replay
    is a new registry version: the earlier attempt stays in the history and can
    be compared with :func:`compare_strategies`.
    """
    step_id = (step_id or "").strip()
    if not step_id:
        raise BoardError("replay needs a step id")
    store = MissionLedgerStore(project_path)
    ledger = store.load()
    if not ledger:
        raise BoardError("no mission ledger")
    new_steps: list[dict[str, Any]] | None = None
    if from_version is not None:
        snap = (ledger.get("snapshots") or {}).get(str(int(from_version)))
        if not isinstance(snap, dict):
            raise BoardError(f"version {int(from_version)} has no snapshot to replay from")
        old = next(
            (s for s in snap.get("steps") or [] if isinstance(s, dict) and s.get("id") == step_id),
            None,
        )
        if old is None:
            raise BoardError(f"step {step_id} is not in version {int(from_version)}")
        new_steps = [{**old, "status": "planned", "retry_count": 0, "evidence": ""}]
        if all(s.get("id") != step_id for s in ledger.get("steps") or []):
            ledger["steps"] = [*(ledger.get("steps") or []), dict(new_steps[0])]
    origin = f" from v{int(from_version)}" if from_version is not None else ""
    note = (reason or "").strip() or "no reason given"
    store.local_replan(
        ledger, step_id, reason=f"replay {step_id}{origin}: {note}", new_steps=new_steps, actor=actor
    )
    ledger["history"][-1]["changes"].append(f"replay:{step_id}")
    invalidated = list(ledger["progress"].get("last_invalidated") or [])
    saved = store.save(ledger)
    return {
        "replayed": step_id,
        "from_version": from_version,
        "version": saved["version"],
        "checkpoint_id": checkpoint_id_for_version(saved["version"]),
        "invalidated": invalidated,
    }


def _parse_at(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _strategy(
    ledger: Mapping[str, Any], version: int, activity: list[dict[str, Any]]
) -> dict[str, Any]:
    history = sorted(
        (h for h in ledger.get("history") or [] if isinstance(h, dict)),
        key=lambda h: int(h.get("version") or 0),
    )
    entry = next((h for h in history if int(h.get("version") or 0) == version), None)
    if entry is None:
        raise BoardError(f"version {version} is not in the mission history")
    later = [h for h in history if int(h.get("version") or 0) > version]
    start = str(entry.get("at") or "")
    end = str(later[0].get("at") or "") if later else ""
    outcome = (ledger.get("outcomes") or {}).get(str(version))
    measured = "live"
    if outcome is None:
        snap = (ledger.get("snapshots") or {}).get(str(version))
        if not isinstance(snap, dict):
            raise BoardError(f"version {version} has no recorded outcome or snapshot")
        # Older registries only kept the state a version started from.
        outcome, measured = outcome_of(snap), "snapshot"
    window = [a for a in activity if start <= _activity_at(a) and (not end or _activity_at(a) < end)]
    t0, t1 = _parse_at(start), _parse_at(end)
    return {
        "version": version,
        "decision": entry.get("reason") or "",
        "actor": entry.get("actor") or "",
        "started_at": start,
        "ended_at": end or None,
        "duration_s": int((t1 - t0).total_seconds()) if t0 and t1 else None,
        "tool_events": sum(1 for a in window if _kind_of(a) == "tool"),
        "test_events": sum(1 for a in window if _kind_of(a) == "test"),
        "measured": measured,
        **{k: v for k, v in outcome.items() if k != "updated_at"},
    }


def _ratio(done: int, total: int) -> float:
    return done / total if total else 0.0


def compare_strategies(project_path: Path | str, a: int, b: int) -> dict[str, Any]:
    """Compare what two registry versions (two plans) actually achieved.

    Ranked by proven criteria, then completed steps, then fewer retries, then
    the shorter run. Each strategy says whether it was measured live or only
    from the snapshot the version started from.
    """
    ledger = MissionLedgerStore(project_path).load()
    if not ledger:
        raise BoardError("no mission ledger")
    activity = ProjectBoardStore(project_path).read_activity(1000)
    sa, sb = _strategy(ledger, int(a), activity), _strategy(ledger, int(b), activity)
    reasons: list[str] = []
    better: int | None = None
    checks = (
        ("criteria proven", _ratio(sa["criteria_proven"], sa["criteria_total"]),
         _ratio(sb["criteria_proven"], sb["criteria_total"]), True),
        ("steps completed", _ratio(sa["steps_completed"], sa["steps_total"]),
         _ratio(sb["steps_completed"], sb["steps_total"]), True),
        ("retries", sa["retries"], sb["retries"], False),
    )
    for label, va, vb, higher_wins in checks:
        if va == vb:
            continue
        a_wins = va > vb if higher_wins else va < vb
        better = sa["version"] if a_wins else sb["version"]
        reasons.append(f"{label}: v{sa['version']}={va:g} v{sb['version']}={vb:g}")
        break
    if better is None and sa["duration_s"] is not None and sb["duration_s"] is not None:
        if sa["duration_s"] != sb["duration_s"]:
            better = sa["version"] if sa["duration_s"] < sb["duration_s"] else sb["version"]
            reasons.append(f"duration: v{sa['version']}={sa['duration_s']}s v{sb['version']}={sb['duration_s']}s")
    if better is None:
        reasons.append("same measured result")
    return {"a": sa, "b": sb, "better": better, "reasons": reasons}
