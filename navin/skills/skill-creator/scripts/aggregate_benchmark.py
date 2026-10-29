#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Aggregate N run-level grading.json files into benchmark.json and benchmark.md.

Usage:
    aggregate_benchmark.py <grading.json> [<grading.json> ...] [--out <dir>]
                           [--primary with_skill] [--baseline without_skill]

Each input is the run-level grading.json written by run_eval.py --results.
The benchmark reports, per configuration, the mean pass rate across runs with
its standard deviation, the per-case stability (how often each case passed
fully across runs) and the delta between the primary and baseline
configurations. Schema: references/eval-schema.md.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path


def stats(values: list[float]) -> dict:
    if not values:
        return {"mean": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0, "n": 0}
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1) if len(values) > 1 else 0.0
    return {
        "mean": round(mean, 4),
        "stddev": round(math.sqrt(var), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "n": len(values),
    }


def load_runs(paths: list[Path]) -> list[dict]:
    runs = []
    for path in paths:
        if path.is_dir():
            path = path / "grading.json"
        if not path.is_file():
            raise SystemExit(f"{path}: not found")
        data = json.loads(path.read_text(encoding="utf-8"))
        if "configurations" not in data:
            raise SystemExit(f"{path}: not a run-level grading file (missing 'configurations')")
        data["_path"] = str(path)
        runs.append(data)
    return runs


def aggregate(runs: list[dict], primary: str, baseline: str) -> dict:
    per_config: dict[str, dict] = {}
    for run in runs:
        for config, block in run["configurations"].items():
            entry = per_config.setdefault(config, {"run_pass_rates": [], "cases": {}})
            entry["run_pass_rates"].append(float(block.get("summary", {}).get("pass_rate", 0.0)))
            for case in block.get("cases", []):
                summary = case.get("summary", {})
                record = entry["cases"].setdefault(case["case_id"], {"pass_rates": [], "full_passes": 0, "runs": 0})
                record["runs"] += 1
                record["pass_rates"].append(float(summary.get("pass_rate", 0.0)))
                if summary.get("total", 0) and summary.get("failed", 1) == 0:
                    record["full_passes"] += 1

    configurations = {}
    for config, entry in per_config.items():
        cases = {}
        for case_id, record in sorted(entry["cases"].items()):
            cases[case_id] = {
                "runs": record["runs"],
                "full_passes": record["full_passes"],
                "stability": round(record["full_passes"] / record["runs"], 4) if record["runs"] else 0.0,
                "pass_rate": stats(record["pass_rates"]),
            }
        configurations[config] = {"pass_rate": stats(entry["run_pass_rates"]), "cases": cases}

    delta = None
    if primary in configurations and baseline in configurations:
        p = configurations[primary]
        b = configurations[baseline]
        per_case = {}
        for case_id in sorted(set(p["cases"]) | set(b["cases"])):
            pv = p["cases"].get(case_id, {}).get("pass_rate", {}).get("mean", 0.0)
            bv = b["cases"].get(case_id, {}).get("pass_rate", {}).get("mean", 0.0)
            per_case[case_id] = round(pv - bv, 4)
        delta = {
            "primary": primary,
            "baseline": baseline,
            "pass_rate": round(p["pass_rate"]["mean"] - b["pass_rate"]["mean"], 4),
            "per_case": per_case,
        }

    skill_names = sorted({str(r.get("skill_name", "")) for r in runs if r.get("skill_name")})
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "skill_name": skill_names[0] if len(skill_names) == 1 else ", ".join(skill_names),
        "runs": [{"run_id": r.get("run_id", ""), "path": r["_path"]} for r in runs],
        "configurations": configurations,
        "delta": delta,
    }


def to_markdown(bench: dict) -> str:
    lines = [f"# Benchmark: {bench['skill_name'] or 'skill'}", "", f"Runs: {len(bench['runs'])}. Generated {bench['generated_at']}.", ""]
    lines += ["| Configuration | Mean pass rate | Stddev | Min | Max | Runs |", "|---|---|---|---|---|---|"]
    for config, block in bench["configurations"].items():
        s = block["pass_rate"]
        lines.append(f"| {config} | {s['mean'] * 100:.1f}% | {s['stddev'] * 100:.1f} | {s['min'] * 100:.0f}% | {s['max'] * 100:.0f}% | {s['n']} |")
    delta = bench.get("delta")
    if delta:
        lines += ["", f"Delta {delta['primary']} vs {delta['baseline']}: {delta['pass_rate'] * 100:+.1f} pts", ""]
    lines += ["", "## Per-case stability", ""]
    configs = list(bench["configurations"])
    header = "| Case | " + " | ".join(f"{c} (mean / stable)" for c in configs) + (" | Delta |" if delta else " |")
    lines.append(header)
    lines.append("|---|" + "---|" * len(configs) + ("---|" if delta else ""))
    case_ids = sorted({cid for c in configs for cid in bench["configurations"][c]["cases"]})
    for case_id in case_ids:
        cells = []
        for config in configs:
            rec = bench["configurations"][config]["cases"].get(case_id)
            cells.append(f"{rec['pass_rate']['mean'] * 100:.0f}% / {rec['stability'] * 100:.0f}%" if rec else "-")
        row = f"| {case_id} | " + " | ".join(cells)
        if delta:
            row += f" | {delta['per_case'].get(case_id, 0.0) * 100:+.0f} pts"
        lines.append(row + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate run-level grading.json files into a benchmark.")
    parser.add_argument("gradings", nargs="+", type=Path, help="grading.json files (or run folders)")
    parser.add_argument("--out", type=Path, help="output folder (default: folder of the first input)")
    parser.add_argument("--primary", default="with_skill", help="configuration under test")
    parser.add_argument("--baseline", default="without_skill", help="configuration compared against")
    args = parser.parse_args(argv)

    runs = load_runs(args.gradings)
    bench = aggregate(runs, args.primary, args.baseline)
    out_dir = args.out or Path(runs[0]["_path"]).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "benchmark.json").write_text(json.dumps(bench, indent=2), encoding="utf-8")
    (out_dir / "benchmark.md").write_text(to_markdown(bench), encoding="utf-8")
    for config, block in bench["configurations"].items():
        print(f"{config}: mean pass rate {block['pass_rate']['mean'] * 100:.1f}% over {block['pass_rate']['n']} run(s)")
    if bench["delta"]:
        print(f"delta {args.primary} vs {args.baseline}: {bench['delta']['pass_rate'] * 100:+.1f} pts")
    print(f"written: {out_dir / 'benchmark.json'}, {out_dir / 'benchmark.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
