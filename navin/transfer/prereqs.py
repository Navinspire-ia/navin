"""S5.0 - the hard prerequisites. Without them S5 is a marketing demo.

* S2 finished: the skills battery loads unchanged and at least one draft was
  examined with a verdict (up / flat / down means the exam exists);
* S3.3 up: the world model radar the policy already relies on;
* S4.3 up: an adapter N+1 has beaten N on the seen split, with no suite
  down (the policy gate's "eligible" verdict), and an adapter serves.

Every item is a fact read from disk; nothing here writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Check ids stay short and stable for the API; people read the labels.
LABELS: dict[str, str] = {"s2": "skills evolution", "s3": "world model", "s4": "policy"}


@dataclass(frozen=True, slots=True)
class Check:
    id: str
    ok: bool
    detail: str

    @property
    def label(self) -> str:
        return LABELS.get(self.id, self.id)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class Prereqs:
    ok: bool
    checks: tuple[Check, ...] = field(default_factory=tuple)

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(f"{c.label}: {c.detail}" for c in self.checks if not c.ok)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": [c.as_dict() for c in self.checks], "reasons": list(self.reasons)}


def _s2_finished(workspace: Path) -> Check:
    try:
        from navin.skills_evolve.battery import BatteryInvalidError, load_battery
        from navin.skills_evolve.drafts import list_drafts
    except Exception as exc:  # noqa: BLE001 - S2 missing is a fact, not a crash
        return Check("s2", False, f"skills evolution unavailable: {exc}")
    try:
        battery = load_battery()
        battery.verify_unchanged()
    except BatteryInvalidError as exc:
        return Check("s2", False, f"skills battery invalid: {exc}")
    except Exception as exc:  # noqa: BLE001
        return Check("s2", False, f"skills battery unreadable: {exc}")
    examined = [d for d in list_drafts(workspace) if isinstance(d.verdict, dict) and d.verdict.get("overall") in ("up", "flat", "down")]
    if not examined:
        return Check("s2", False, "no skill draft was ever examined here (no up / flat / down yet)")
    return Check("s2", True, f"battery {battery.version}, {len(examined)} examined draft(s)")


def _s3_up(workspace: Path) -> Check:
    try:
        from navin.policy.radar import radar
    except Exception as exc:  # noqa: BLE001
        return Check("s3", False, f"world model unavailable: {exc}")
    signal = radar(workspace)
    if not signal.up:
        return Check("s3", False, "; ".join(signal.reasons) or "world model exam is not up")
    return Check("s3", True, f"world model checkpoint {signal.checkpoint} is {signal.verdict}")


def _s4_up(workspace: Path) -> Check:
    try:
        from navin.policy import checkpoints as ck
        from navin.policy.settings import read_settings
    except Exception as exc:  # noqa: BLE001
        return Check("s4", False, f"policy unavailable: {exc}")
    if not read_settings(workspace).enabled:
        return Check("s4", False, "policy learning is off for this project")
    active = ck.read_active(workspace)
    if not active:
        return Check("s4", False, "no policy adapter serves")
    beat = [
        s["number"]
        for s in ck.checkpoint_summaries(workspace)
        if isinstance(s.get("verdict_vs_active"), dict) and s["verdict_vs_active"].get("eligible")
    ]
    if not beat:
        return Check("s4", False, "no policy adapter has beaten its predecessor on the seen split yet")
    return Check("s4", True, f"adapter {beat[-1]} beat its predecessor with no suite down; adapter {active.get('checkpoint')} serves")


def prereqs(workspace: Path | str) -> Prereqs:
    workspace = Path(workspace)
    checks = (_s2_finished(workspace), _s3_up(workspace), _s4_up(workspace))
    return Prereqs(ok=all(c.ok for c in checks), checks=checks)
