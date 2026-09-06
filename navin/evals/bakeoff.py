"""Bake-off harness: Navin agent profile vs a baseline (Claude Code style).

Runs the same corpus against two models/profiles and compares scoreboards.
The claim "agent success rate >= baseline" is only honest when both sides use
the same underlying model; this harness enforces that by taking two ``Model``
instances and never mutating the dataset between runs.

Usage (offline, mock model on both sides - harness sanity)::

    python -m navin.evals.bakeoff navin/evals/datasets/code_agent_v1.jsonl

Usage (real comparison): construct two models in code (same provider/route,
different system profiles) and call :func:`run_bakeoff`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from navin.evals.runner import (
    MockModel,
    Model,
    Scoreboard,
    evaluate_case,
    load_dataset,
    scoreboard,
)

# System prompt used for the "champion" side when callers wrap a raw model.
NAVIN_PROFILE = (
    "You are Navin Code agent. Strict loop: scope -> plan -> patch via "
    "apply_patch only -> lint/tests -> verify -> report. Never claim done "
    "while verify is red. Read-only questions use read_only tools."
)

# Baseline mirrors a plain Claude Code style prompt: capable, no Navin gates.
BASELINE_PROFILE = (
    "You are a helpful senior software engineering assistant. Solve the "
    "user's coding task well and explain what you did."
)


class ProfiledModel:
    """Wrap a raw model with a fixed system profile prefix."""

    def __init__(self, inner: Model, profile: str) -> None:
        self._inner = inner
        self._profile = profile

    def complete(self, prompt: str) -> str:
        return self._inner.complete(f"{self._profile}\n\n{prompt}")


@dataclass(frozen=True)
class BakeoffReport:
    dataset: str
    champion: Scoreboard
    baseline: Scoreboard
    delta_rate: float
    ok: bool

    def to_json(self) -> dict:
        return {
            "dataset": self.dataset,
            "champion": asdict(self.champion),
            "baseline": asdict(self.baseline),
            "delta_rate": self.delta_rate,
            "ok": self.ok,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }


def run_bakeoff(
    dataset_path: Path | str,
    *,
    champion: Model,
    baseline: Model,
) -> BakeoffReport:
    """Score both models on the same corpus. ok = champion >= baseline."""
    path = Path(dataset_path)
    cases = load_dataset(path)
    champ_board = scoreboard(evaluate_case(champion, case) for case in cases)
    base_board = scoreboard(evaluate_case(baseline, case) for case in cases)
    delta = champ_board.overall_rate - base_board.overall_rate
    return BakeoffReport(
        dataset=str(path),
        champion=champ_board,
        baseline=base_board,
        delta_rate=round(delta, 4),
        ok=champ_board.overall_rate + 1e-9 >= base_board.overall_rate,
    )


def _print_report(report: BakeoffReport) -> None:
    champ = report.champion
    base = report.baseline
    print(
        f"champion: {champ.overall_passed}/{champ.overall_total} "
        f"({champ.overall_rate:.1%})"
    )
    print(
        f"baseline: {base.overall_passed}/{base.overall_total} "
        f"({base.overall_rate:.1%})"
    )
    print(f"delta: {report.delta_rate:+.1%}")
    print("BAKEOFF PASS" if report.ok else "BAKEOFF FAIL: champion below baseline")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare Navin profile vs baseline on the same corpus",
    )
    parser.add_argument(
        "dataset",
        nargs="?",
        default=str(
            Path(__file__).resolve().parent / "datasets" / "code_agent_v1.jsonl"
        ),
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional path to write the JSON report artifact",
    )
    args = parser.parse_args(argv)
    # Offline default: same deterministic mock on both sides (harness sanity;
    # expected result is a tie with ok=True).
    report = run_bakeoff(
        args.dataset,
        champion=MockModel(),
        baseline=MockModel(),
    )
    _print_report(report)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report.to_json(), indent=2) + "\n", encoding="utf-8"
        )
        print(f"report written: {out}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
