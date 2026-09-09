# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Live advice, gated (S3.5).

The advisor never blocks a call. When every gate is open it may say, in a
short runtime-context block or through the ``world_predict`` tool, what the
head expects: "useless" (denied / not found / timeout expected), "risky"
(error expected) or "already seen" (the same read-only call, same answer,
moments ago). Nothing else.

Gates, all required before ``advise`` can be switched on and re-checked
on every turn:

1. an active checkpoint exists;
2. its last exam on the current frozen set says ``up`` vs the baseline;
3. an offline A/B on that same set showed a gain: fewer useless calls
   would have been made, no more errors would have been introduced.

Live, every suggestion is compared with the observed class after the call.
When the rolling precision falls under ``KILL_PRECISION`` the advisor cuts
itself off: ``advise`` goes back to off, the head rolls back to N-1, the
journal says why. A human has to switch it on again.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from navin.world_model import checkpoints as ck
from navin.world_model.dataset import HeldoutTamperedError, load_heldout
from navin.world_model.journal import append_live, journal, read_live
from navin.world_model.model import Model, Prediction
from navin.world_model.paths import ab_path
from navin.world_model.settings import read_settings, update_settings
from navin.world_model.train import latest_score
from navin.world_model.trajectory import (
    FAILURE_CLASSES,
    NEVER_SKIP_TOOLS,
    USELESS_CLASSES,
    now_stamp,
)

HUMAN = "human"

# Offline A/B: the advice is a gain when it would have spared at least this
# share of the useless calls while staying this precise.
AB_MIN_AVOIDED_SHARE = 0.10
AB_MIN_PRECISION = 0.90
AB_REGRESS_PRECISION = 0.80

# Live kill switch: rolling window and floor.
KILL_WINDOW = 30
KILL_MIN_ADVICE = 10
KILL_PRECISION = 0.80

MAX_ADVICE_LINES = 3


# --------------------------------------------------------------------------
# Suggestion
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Suggestion:
    kind: str  # useless | risky | seen
    cls: str
    confidence: float
    support: int
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "cls": self.cls, "confidence": round(self.confidence, 3), "support": self.support, "text": self.text}


def suggest(
    prediction: Prediction,
    *,
    tool: str,
    label: str,
    threshold: float,
    seen_same_answer: bool = False,
    read_only: bool = False,
) -> Suggestion | None:
    """A short advisory line, or ``None`` when there is nothing worth saying."""
    if seen_same_answer and read_only and tool not in NEVER_SKIP_TOOLS:
        return Suggestion(
            kind="seen",
            cls=prediction.cls,
            confidence=1.0,
            support=prediction.support,
            text=f"{label}: same call, same answer moments ago; the previous result still holds.",
        )
    if prediction.confidence < threshold or prediction.support < 3:
        return None
    pct = round(prediction.confidence * 100)
    if prediction.cls in USELESS_CLASSES:
        return Suggestion(
            kind="useless",
            cls=prediction.cls,
            confidence=prediction.confidence,
            support=prediction.support,
            text=f"{label}: usually {prediction.cls} here ({pct}%, {prediction.support} calls); check the precondition first.",
        )
    if prediction.cls in FAILURE_CLASSES:
        return Suggestion(
            kind="risky",
            cls=prediction.cls,
            confidence=prediction.confidence,
            support=prediction.support,
            text=f"{label}: usually fails here ({pct}%, {prediction.support} calls); expect an error and read it.",
        )
    return None


# --------------------------------------------------------------------------
# Gate
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Gate:
    open: bool
    reasons: tuple[str, ...]
    checkpoint: int | None
    heldout_version: str | None
    exam_verdict: str | None
    ab_verdict: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "open": self.open,
            "reasons": list(self.reasons),
            "checkpoint": self.checkpoint,
            "heldout_version": self.heldout_version,
            "exam_verdict": self.exam_verdict,
            "ab_verdict": self.ab_verdict,
        }


def read_ab(workspace: Path | str, *, limit: int = 20) -> list[dict[str, Any]]:
    try:
        lines = ab_path(workspace).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-max(1, limit):]:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def latest_ab(workspace: Path | str, *, checkpoint: int | None, heldout_version: str | None) -> dict[str, Any] | None:
    for entry in reversed(read_ab(workspace, limit=50)):
        if entry.get("checkpoint") == checkpoint and entry.get("heldout_version") == heldout_version:
            return entry
    return None


def advice_gate(workspace: Path | str) -> Gate:
    """Why advice may or may not speak for this project right now."""
    reasons: list[str] = []
    active = ck.active_checkpoint(workspace)
    if active is None:
        return Gate(False, ("no active checkpoint: train first",), None, None, None, None)
    try:
        heldout = load_heldout(workspace)
    except HeldoutTamperedError as exc:
        return Gate(False, (str(exc),), active.number, None, None, None)
    version = heldout.version if heldout is not None else None
    if heldout is None:
        reasons.append("no frozen exam set")
    score = latest_score(workspace, heldout_version=version) if version else None
    exam_verdict = None
    if score is None or score.get("checkpoint") != active.number:
        reasons.append("the active checkpoint has no score on the current exam set")
    else:
        exam_verdict = str(score.get("verdict_vs_baseline") or "flat")
        if exam_verdict != "up":
            reasons.append(f"exam verdict is {exam_verdict}, not up")
    ab = latest_ab(workspace, checkpoint=active.number, heldout_version=version)
    ab_verdict = str(ab.get("verdict")) if ab is not None else None
    if ab is None:
        reasons.append("no offline A/B for this checkpoint")
    elif ab_verdict != "gain":
        reasons.append(f"offline A/B says {ab_verdict}, not gain")
    return Gate(not reasons, tuple(reasons), active.number, version, exam_verdict, ab_verdict)


def advice_open(workspace: Path | str | None) -> bool:
    """``advise`` on *and* every gate open. What the tool and the prompt check."""
    if workspace is None:
        return False
    settings = read_settings(workspace)
    if not settings.feature("advise"):
        return False
    return advice_gate(workspace).open


# --------------------------------------------------------------------------
# Offline A/B on the frozen set
# --------------------------------------------------------------------------


def _replay(model: Model, rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    """Would the advice have helped on calls the head never saw?

    A = no advice: every call is made; the useless ones (denied, not found,
    timeout) are paid in full. B = advice: a call flagged "useless" with
    enough confidence is spared when it really was useless (avoided) and is
    a false alarm otherwise (an error the agent would have been talked into).
    """
    useless = flagged = avoided = false_alarm = 0
    for record in rows:
        actual = str(record.get("cls") or "")
        if actual in USELESS_CLASSES:
            useless += 1
        prev = record.get("prev")
        prediction = model.predict(
            str(record.get("tool") or ""),
            str(record.get("key") or ""),
            str(record.get("args") or ""),
            [str(p) for p in prev] if isinstance(prev, list) else [],
        )
        if prediction.confidence >= threshold and prediction.support >= 3 and prediction.cls in USELESS_CLASSES:
            flagged += 1
            if actual in USELESS_CLASSES:
                avoided += 1
            else:
                false_alarm += 1
    precision = (avoided / flagged) if flagged else None
    avoided_share = (avoided / useless) if useless else 0.0
    if flagged == 0 or useless == 0:
        verdict = "no_gain"
    elif precision is not None and precision < AB_REGRESS_PRECISION:
        verdict = "regress"
    elif precision is not None and precision >= AB_MIN_PRECISION and avoided_share >= AB_MIN_AVOIDED_SHARE:
        verdict = "gain"
    else:
        verdict = "no_gain"
    return {
        "n": len(rows),
        "useless_calls": useless,
        "flagged": flagged,
        "avoided": avoided,
        "false_alarms": false_alarm,
        "precision": None if precision is None else round(precision, 4),
        "avoided_share": round(avoided_share, 4),
        "verdict": verdict,
    }


def run_ab(workspace: Path | str, *, actor: str = "auto") -> dict[str, Any]:
    """Offline A/B of the active head on the frozen set; one line in ``ab.jsonl``."""
    workspace = Path(workspace)
    settings = read_settings(workspace)
    if not settings.enabled:
        return {"status": "skipped", "reason": "world model is off for this project"}
    active = ck.active_checkpoint(workspace)
    if active is None:
        return {"status": "no_active", "reason": "no active checkpoint"}
    try:
        heldout = load_heldout(workspace)
    except HeldoutTamperedError as exc:
        return {"status": "tampered", "reason": str(exc)}
    if heldout is None:
        return {"status": "no_heldout", "reason": "nothing frozen yet"}
    result = _replay(active.model, heldout.rows, settings.confidence_threshold)
    entry = {
        "ts": now_stamp(),
        "checkpoint": active.number,
        "heldout_version": heldout.version,
        "threshold": settings.confidence_threshold,
        "actor": actor,
        **result,
    }
    path = ab_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
    journal(workspace, "ab", checkpoint=active.number, verdict=result["verdict"], avoided=result["avoided"], false_alarms=result["false_alarms"], actor=actor)
    return {"status": "scored", **entry}


# --------------------------------------------------------------------------
# Live tracking and kill switch
# --------------------------------------------------------------------------


@dataclass(slots=True)
class _LiveStats:
    outcomes: deque[bool] = field(default_factory=lambda: deque(maxlen=KILL_WINDOW))
    advised: int = 0
    cut: bool = False


class LiveTracker:
    """Rolling precision of the live advice, per project, process-wide."""

    def __init__(self) -> None:
        self._stats: dict[str, _LiveStats] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._stats.clear()

    def snapshot(self, workspace: Path | str) -> dict[str, Any]:
        with self._lock:
            stats = self._stats.get(str(workspace))
            if stats is None:
                return {"advised": 0, "window": 0, "precision": None}
            window = len(stats.outcomes)
            hits = sum(1 for hit in stats.outcomes if hit)
            return {"advised": stats.advised, "window": window, "precision": (hits / window) if window else None}

    def record(self, workspace: Path | str, *, suggestion: Suggestion, actual_cls: str) -> bool:
        """Record one advice vs outcome. Returns True when the kill switch fired."""
        hit = actual_cls == suggestion.cls or (suggestion.kind == "useless" and actual_cls in USELESS_CLASSES)
        key = str(workspace)
        with self._lock:
            stats = self._stats.setdefault(key, _LiveStats())
            stats.advised += 1
            stats.outcomes.append(hit)
            window = len(stats.outcomes)
            hits = sum(1 for h in stats.outcomes if h)
            precision = hits / window if window else 1.0
            fire = window >= KILL_MIN_ADVICE and precision < KILL_PRECISION and not stats.cut
            if fire:
                stats.cut = True
        append_live(
            workspace,
            {
                "ts": now_stamp(),
                "kind": suggestion.kind,
                "predicted": suggestion.cls,
                "actual": actual_cls,
                "confidence": round(suggestion.confidence, 3),
                "hit": hit,
            },
        )
        if fire:
            cut_advice(workspace, reason=f"live precision {precision:.2f} under {KILL_PRECISION:.2f} on {window} advices")
        return fire


_LIVE = LiveTracker()


def live_tracker() -> LiveTracker:
    return _LIVE


def live_summary(workspace: Path | str) -> dict[str, Any]:
    """In-memory window plus the tail of ``live.jsonl`` for the panel."""
    memory = _LIVE.snapshot(workspace)
    rows = read_live(workspace, limit=200)
    hits = sum(1 for row in rows if row.get("hit") is True)
    return {
        **memory,
        "logged": len(rows),
        "logged_precision": round(hits / len(rows), 4) if rows else None,
    }


def cut_advice(workspace: Path | str, *, reason: str) -> None:
    """The kill switch: advise off, head back to N-1, one journal line."""
    workspace = Path(workspace)
    try:
        update_settings(workspace, {"advise": False})
    except (OSError, ValueError) as exc:
        logger.warning("world-model advise could not be switched off: {}", exc)
    from navin.world_model.train import rollback

    rolled = rollback(workspace, actor="kill_switch", reason=reason)
    journal(workspace, "advise_cut", reason=reason, rollback=rolled)
    logger.warning("world-model advice cut for {}: {}", workspace, reason)


# --------------------------------------------------------------------------
# Advice for a turn
# --------------------------------------------------------------------------


def advice_lines(workspace: Path | str, *, limit: int = MAX_ADVICE_LINES) -> list[str]:
    """The confident beliefs a turn may hear, or nothing. Checks every gate."""
    if not advice_open(workspace):
        return []
    from navin.world_model.beliefs import current_beliefs

    settings = read_settings(workspace)
    lines: list[str] = []
    for belief in current_beliefs(workspace):
        if belief.confidence < settings.confidence_threshold:
            continue
        lines.append(f"- {belief.text}")
        if len(lines) >= limit:
            break
    return lines


def advice_block_text(workspace: Path | str) -> str | None:
    lines = advice_lines(workspace)
    if not lines:
        return None
    return "\n".join(
        [
            "World model (advisory, learned from this project's past tool calls; never skip a write, delete, mail or payment call because of it):",
            *lines,
        ]
    )
