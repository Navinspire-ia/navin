"""Live steering, gated and soft (S4.4).

The steerer never executes anything and never blocks a call. When every
gate is open it may say, in a short runtime-context block or through the
``policy_next`` tool, what the successful eval runs did next in a state like
this one, and which action only ever showed up in failed runs. The chat LLM
stays the generator; a write, delete, mail or payment keeps its approval.

Gates, all required before ``steer`` can be switched on and re-checked on
every turn:

1. an active adapter exists and was judged on the **current** frozen set;
2. that judgement is eligible (better than its reference, no suite down),
   or a human forced a flat adapter on purpose (traced);
3. an offline A/B on the same frozen set showed a gain: the confident
   suggestions agree with the successful steps clearly more often than the
   no-steer baseline would.

Live, each confident suggestion is compared with the tool the agent really
called next. When the rolling precision falls under ``KILL_PRECISION`` the
steerer cuts itself off: ``steer`` back to off, adapter back to N-1, one
journal line. A human has to switch it on again.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from navin.policy import checkpoints as ck
from navin.policy.dataset import HeldoutTamperedError, load_heldout, training_rows
from navin.policy.journal import append_live, journal, read_live, read_steps
from navin.policy.model import ActionDistribution, MajorityPolicy, Policy, Verdict, evaluate
from navin.policy.paths import ab_path
from navin.policy.settings import read_settings, update_settings
from navin.policy.trajectory import ASK, GUARDED_TOOLS, STOP, StateKey, intent_of, now_stamp

HUMAN = "human"

# Offline A/B: the steer is a gain when its confident suggestions cover at
# least this share of the steps and beat the no-steer baseline by this much.
AB_MIN_COVERAGE = 0.20
AB_MIN_PRECISION = 0.70
AB_MIN_MARGIN = 0.10
AB_REGRESS_FLOOR = 0.50

# Live kill switch: rolling window and floor.
KILL_WINDOW = 30
KILL_MIN_SUGGESTIONS = 10
KILL_PRECISION = 0.60

MIN_SUPPORT = 3
MAX_BLOCK_LINES = 3


# --------------------------------------------------------------------------
# Suggestion
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Suggestion:
    action: str
    confidence: float
    support: int
    avoid: tuple[str, ...]
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "confidence": round(self.confidence, 3),
            "support": self.support,
            "avoid": list(self.avoid),
            "text": self.text,
        }


def _label(action: str) -> str:
    if action == STOP:
        return "answer (no more tool calls)"
    if action == ASK:
        return "ask the user a question"
    return f"`{action}`"


def suggest(distribution: ActionDistribution, *, threshold: float, intent: str) -> Suggestion | None:
    """A short advisory line, or ``None`` when the head has nothing confident to say."""
    confident = distribution.confidence >= threshold and distribution.support >= MIN_SUPPORT
    if not confident and not distribution.avoid:
        return None
    parts: list[str] = []
    if confident:
        pct = round(distribution.confidence * 100)
        head = f"for a request like this ({intent}), successful runs next used {_label(distribution.action)} ({pct}%, {distribution.support} runs)"
        if distribution.action in GUARDED_TOOLS:
            head += "; approval for that call is unchanged"
        parts.append(head)
    if distribution.avoid:
        parts.append("only failed runs used " + ", ".join(f"`{a}`" for a in distribution.avoid[:3]) + " here")
    return Suggestion(
        action=distribution.action if confident else "",
        confidence=distribution.confidence if confident else 0.0,
        support=distribution.support,
        avoid=distribution.avoid,
        text="; ".join(parts) + ".",
    )


def suggest_next(
    workspace: Path | str,
    *,
    intent: str,
    prev: tuple[str, ...] | list[str] = (),
    s3: str | None = None,
) -> Suggestion | None:
    """The active head's proposal for this state, if any. Does not check the gate."""
    active = ck.active_checkpoint(workspace)
    if active is None:
        return None
    settings = read_settings(workspace)
    state = StateKey(intent=intent, prev=tuple(prev), s3=s3)
    return suggest(active.model.predict(state), threshold=settings.confidence_threshold, intent=intent)


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
    forced: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "open": self.open,
            "reasons": list(self.reasons),
            "checkpoint": self.checkpoint,
            "heldout_version": self.heldout_version,
            "exam_verdict": self.exam_verdict,
            "ab_verdict": self.ab_verdict,
            "forced": self.forced,
        }


def read_ab(workspace: Path | str, *, limit: int = 20) -> list[dict[str, Any]]:
    try:
        lines = ab_path(workspace).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
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


def exam_verdict_of(checkpoint: ck.Checkpoint) -> Verdict | None:
    """The judgement that decided this adapter's fate: vs N when N existed, else vs the baseline."""
    return Verdict.from_dict(checkpoint.verdict_vs_active) or Verdict.from_dict(checkpoint.verdict_vs_baseline)


def steer_gate(workspace: Path | str) -> Gate:
    """Why the steerer may or may not speak for this project right now."""
    reasons: list[str] = []
    active = ck.active_checkpoint(workspace)
    if active is None:
        return Gate(False, ("no active adapter: train first",), None, None, None, None)
    pointer = ck.read_active(workspace) or {}
    forced = bool(pointer.get("forced"))
    try:
        heldout = load_heldout(workspace)
    except HeldoutTamperedError as exc:
        return Gate(False, (str(exc),), active.number, None, None, None, forced)
    version = heldout.version if heldout is not None else None
    if heldout is None:
        reasons.append("no frozen exam set")
    elif active.heldout_version != version:
        reasons.append("the active adapter was judged on an older exam set; train again")
    verdict = exam_verdict_of(active)
    exam_verdict = verdict.overall if verdict is not None else None
    if verdict is None:
        reasons.append("the active adapter has no exam verdict")
    elif verdict.regressed:
        reasons.append("the active adapter regressed on a suite")
    elif not verdict.eligible and not forced:
        reasons.append(f"exam verdict is {verdict.overall}, not up (a human may force a flat adapter)")
    ab = latest_ab(workspace, checkpoint=active.number, heldout_version=version)
    ab_verdict = str(ab.get("verdict")) if ab is not None else None
    if ab is None:
        reasons.append("no offline A/B for this adapter")
    elif ab_verdict != "gain":
        reasons.append(f"offline A/B says {ab_verdict}, not gain")
    return Gate(not reasons, tuple(reasons), active.number, version, exam_verdict, ab_verdict, forced)


def steer_open(workspace: Path | str | None) -> bool:
    """``steer`` on *and* every gate open. What the tool and the prompt check."""
    if workspace is None:
        return False
    settings = read_settings(workspace)
    if not settings.feature("steer"):
        return False
    return steer_gate(workspace).open


# --------------------------------------------------------------------------
# Offline A/B on the frozen set
# --------------------------------------------------------------------------


def _replay(model: Policy, rows: list[dict[str, Any]], train_rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    """Would the steer have pointed to the right next action on steps the head never saw?

    A = no steer: the agent picks by itself; the no-steer reference is the
    majority action of the training split. B = steer: when the head is
    confident it names an action; a hit is the action the successful run
    really took next.
    """
    baseline = evaluate(MajorityPolicy(train_rows), rows)
    n = flagged = hits = 0
    for record in rows:
        reward = record.get("reward")
        actual = str(record.get("action") or "")
        if not actual or not isinstance(reward, (int, float)) or reward <= 0:
            continue
        n += 1
        distribution = model.predict(StateKey.from_record(record))
        if distribution.confidence >= threshold and distribution.support >= MIN_SUPPORT:
            flagged += 1
            hits += 1 if distribution.action == actual else 0
    precision = (hits / flagged) if flagged else None
    coverage = (flagged / n) if n else 0.0
    if n == 0 or flagged == 0 or precision is None:
        verdict = "no_gain"
    elif precision < max(AB_REGRESS_FLOOR, baseline.accuracy):
        verdict = "regress"
    elif precision >= AB_MIN_PRECISION and coverage >= AB_MIN_COVERAGE and precision >= baseline.accuracy + AB_MIN_MARGIN:
        verdict = "gain"
    else:
        verdict = "no_gain"
    return {
        "n": n,
        "flagged": flagged,
        "hits": hits,
        "precision": None if precision is None else round(precision, 4),
        "coverage": round(coverage, 4),
        "baseline_accuracy": round(baseline.accuracy, 4),
        "verdict": verdict,
    }


def run_ab(workspace: Path | str, *, actor: str = "auto") -> dict[str, Any]:
    """Offline A/B of the active adapter on the frozen set; one line in ``ab.jsonl``."""
    workspace = Path(workspace)
    settings = read_settings(workspace)
    if not settings.enabled:
        return {"status": "skipped", "reason": "policy learning is off for this project"}
    active = ck.active_checkpoint(workspace)
    if active is None:
        return {"status": "no_active", "reason": "no active adapter"}
    try:
        heldout = load_heldout(workspace)
    except HeldoutTamperedError as exc:
        return {"status": "tampered", "reason": str(exc)}
    if heldout is None:
        return {"status": "no_heldout", "reason": "nothing frozen yet"}
    train_rows = training_rows(read_steps(workspace), heldout)
    result = _replay(active.model, heldout.rows, train_rows, settings.confidence_threshold)
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
    journal(workspace, "ab", checkpoint=active.number, verdict=result["verdict"], precision=result["precision"], coverage=result["coverage"], actor=actor)
    return {"status": "scored", **entry}


# --------------------------------------------------------------------------
# Live tracking and kill switch
# --------------------------------------------------------------------------


@dataclass(slots=True)
class _LiveStats:
    outcomes: deque[bool] = field(default_factory=lambda: deque(maxlen=KILL_WINDOW))
    suggested: int = 0
    cut: bool = False


class LiveTracker:
    """Rolling precision of the live suggestions, per project, process-wide."""

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
                return {"suggested": 0, "window": 0, "precision": None}
            window = len(stats.outcomes)
            hits = sum(1 for hit in stats.outcomes if hit)
            return {"suggested": stats.suggested, "window": window, "precision": (hits / window) if window else None}

    def record(self, workspace: Path | str, *, suggestion: Suggestion, actual: str) -> bool:
        """Record one confident suggestion vs the action really taken. True when the kill switch fired."""
        if not suggestion.action:
            return False
        hit = actual == suggestion.action
        key = str(workspace)
        with self._lock:
            stats = self._stats.setdefault(key, _LiveStats())
            stats.suggested += 1
            stats.outcomes.append(hit)
            window = len(stats.outcomes)
            hits = sum(1 for h in stats.outcomes if h)
            precision = hits / window if window else 1.0
            fire = window >= KILL_MIN_SUGGESTIONS and precision < KILL_PRECISION and not stats.cut
            if fire:
                stats.cut = True
        append_live(
            workspace,
            {
                "ts": now_stamp(),
                "predicted": suggestion.action,
                "actual": actual,
                "confidence": round(suggestion.confidence, 3),
                "hit": hit,
            },
        )
        if fire:
            cut_steer(workspace, reason=f"live precision {precision:.2f} under {KILL_PRECISION:.2f} on {window} suggestions")
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


def cut_steer(workspace: Path | str, *, reason: str) -> None:
    """The kill switch: steer off, adapter back to N-1, one journal line."""
    workspace = Path(workspace)
    try:
        update_settings(workspace, {"steer": False})
    except (OSError, ValueError) as exc:
        logger.warning("policy steer could not be switched off: {}", exc)
    from navin.policy.train import rollback

    rolled = rollback(workspace, actor="kill_switch", reason=reason)
    journal(workspace, "steer_cut", reason=reason, rollback=rolled)
    logger.warning("policy steer cut for {}: {}", workspace, reason)


# --------------------------------------------------------------------------
# The soft channel: one block at the start of a turn
# --------------------------------------------------------------------------


def steer_block_text(workspace: Path | str, *, user_text: str | None) -> str | None:
    """The sidecar suggestion for this request, or ``None``. Checks every gate."""
    if not steer_open(workspace):
        return None
    intent = intent_of(user_text)
    suggestion = suggest_next(workspace, intent=intent, prev=())
    if suggestion is None:
        return None
    lines = [
        "Policy head (advisory, learned from this project's own eval runs; it never runs a tool for you, "
        "and a write, delete, mail or payment still needs its usual approval):",
        f"- {suggestion.text}",
        "- Use `policy_next` with the tools you already called to ask what successful runs did next.",
    ]
    return "\n".join(lines[:MAX_BLOCK_LINES])
