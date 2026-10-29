#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Write a self-contained HTML review report from one or two eval run folders.

Usage:
    generate_report.py --run <run-dir> [--baseline <run-dir>] [--out report.html]

Each run folder must contain the grading.json written by run_eval.py.
Without --baseline, the report compares the run's own `with_skill` and
`without_skill` configurations when both exist. With --baseline, the primary
configuration of the baseline run (an older skill version) is compared with
the primary configuration of --run.

The HTML has no external assets: Navin palette (Ink, Paper, Lime, Teal) and
a system font stack led by Inter / IBM Plex Sans.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

INK = "#17171a"
PAPER = "#f4f3ee"
LIME = "#c6e88a"
TEAL = "#0e7490"
FAIL = "#b42318"

CSS = f"""
:root {{ --ink:{INK}; --paper:{PAPER}; --lime:{LIME}; --teal:{TEAL}; --fail:{FAIL}; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--paper); color:var(--ink);
  font-family: Inter, "IBM Plex Sans", "Segoe UI", system-ui, sans-serif; line-height:1.5; }}
header {{ background:var(--ink); color:var(--paper); padding:32px 40px; }}
header h1 {{ margin:0 0 6px; font-size:26px; font-weight:600; letter-spacing:-0.01em; }}
header p {{ margin:0; opacity:.75; }}
main {{ max-width:1080px; margin:0 auto; padding:32px 40px 64px; }}
.totals {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:16px; margin-bottom:32px; }}
.tile {{ background:#fff; border:1px solid #dcdad2; border-radius:8px; padding:16px 18px; }}
.tile .label {{ font-size:12px; text-transform:none; color:#5a5a60; }}
.tile .value {{ font-size:28px; font-weight:600; margin-top:4px; }}
.tile.accent {{ background:var(--lime); border-color:var(--lime); }}
h2 {{ font-size:18px; margin:32px 0 12px; }}
table {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid #dcdad2; border-radius:8px; overflow:hidden; }}
th, td {{ text-align:left; padding:10px 14px; border-bottom:1px solid #e8e6df; vertical-align:top; font-size:14px; }}
th {{ background:#eceae2; font-weight:600; }}
tr:last-child td {{ border-bottom:none; }}
.pass {{ color:var(--teal); font-weight:600; }}
.fail {{ color:var(--fail); font-weight:600; }}
.pill {{ display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px; font-weight:600; }}
.pill.pass {{ background:var(--lime); color:var(--ink); }}
.pill.fail {{ background:#fde2de; color:var(--fail); }}
.pill.na {{ background:#e4e2da; color:#5a5a60; }}
details {{ background:#fff; border:1px solid #dcdad2; border-radius:8px; margin:10px 0; padding:0 16px; }}
summary {{ cursor:pointer; padding:12px 0; font-weight:600; }}
code {{ font-family:"IBM Plex Mono", ui-monospace, monospace; font-size:13px; background:#eceae2; padding:1px 5px; border-radius:4px; }}
.evidence {{ color:#44444a; }}
.delta-up {{ color:var(--teal); font-weight:600; }}
.delta-down {{ color:var(--fail); font-weight:600; }}
footer {{ text-align:center; color:#5a5a60; font-size:12px; padding:24px; }}
"""


def load_grading(run_dir: Path) -> dict:
    path = run_dir / "grading.json"
    if not path.is_file():
        raise SystemExit(f"{path} not found; run run_eval.py --results first")
    data = json.loads(path.read_text(encoding="utf-8"))
    if "configurations" not in data:
        raise SystemExit(f"{path}: not a run-level grading file (missing 'configurations')")
    return data


def primary_configuration(grading: dict) -> str:
    configs = list(grading["configurations"])
    if not configs:
        raise SystemExit("grading.json has no graded configuration")
    return "with_skill" if "with_skill" in configs else configs[0]


def cases_by_id(grading: dict, configuration: str) -> dict[str, dict]:
    block = grading["configurations"].get(configuration, {})
    return {c["case_id"]: c for c in block.get("cases", [])}


def pick_comparison(run: dict, baseline: dict | None) -> tuple[dict, str, dict | None, str | None]:
    primary = primary_configuration(run)
    if baseline is not None:
        return run, primary, baseline, primary_configuration(baseline)
    others = [c for c in run["configurations"] if c != primary]
    if others:
        return run, primary, run, others[0]
    return run, primary, None, None


def _pct(block: dict) -> str:
    summary = block.get("summary", {})
    return f"{summary.get('pass_rate', 0.0) * 100:.0f}%"


def _status(case: dict | None) -> str:
    if case is None:
        return '<span class="pill na">not run</span>'
    if case["summary"]["total"] == 0:
        return '<span class="pill na">no assertions</span>'
    ok = case["summary"]["failed"] == 0
    return f'<span class="pill {"pass" if ok else "fail"}">{case["summary"]["passed"]}/{case["summary"]["total"]}</span>'


def render(run: dict, primary: str, base: dict | None, base_config: str | None, title: str) -> str:
    esc = html.escape
    run_cases = cases_by_id(run, primary)
    base_cases = cases_by_id(base, base_config) if base and base_config else {}
    ordered_ids = list(run_cases) + [i for i in base_cases if i not in run_cases]
    run_block = run["configurations"][primary]
    base_block = base["configurations"][base_config] if base and base_config else None
    base_label = f"{esc(base.get('run_id', ''))} / {esc(base_config)}" if base_block else "none"

    improved = [i for i in ordered_ids if i in base_cases and i in run_cases
                and run_cases[i]["summary"]["passed"] > base_cases[i]["summary"]["passed"]]
    regressed = [i for i in ordered_ids if i in base_cases and i in run_cases
                 and run_cases[i]["summary"]["passed"] < base_cases[i]["summary"]["passed"]]
    unchanged = [i for i in ordered_ids if i in base_cases and i in run_cases
                 and i not in improved and i not in regressed]

    tiles = [
        ("Skill", esc(run.get("skill_name", "")), ""),
        ("Primary pass rate", _pct(run_block), "accent"),
        ("Assertions passed", f"{run_block['summary']['passed']}/{run_block['summary']['total']}", ""),
        ("Cases fully passed", f"{run_block['summary']['cases_fully_passed']}/{run_block['summary']['cases']}", ""),
    ]
    if base_block:
        delta = run_block["summary"]["pass_rate"] - base_block["summary"]["pass_rate"]
        tiles.append(("Baseline pass rate", _pct(base_block), ""))
        tiles.append(("Delta", f"{delta * 100:+.0f} pts", ""))

    out = [
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        f"<title>{esc(title)}</title><style>{CSS}</style></head><body>",
        f"<header><h1>{esc(title)}</h1><p>Run <code>{esc(run.get('run_id', ''))}</code> / "
        f"<code>{esc(primary)}</code> graded {esc(run.get('graded_at', ''))}. Baseline: {base_label}.</p></header>",
        "<main><div class=\"totals\">",
    ]
    for label, value, klass in tiles:
        out.append(f'<div class="tile {klass}"><div class="label">{label}</div><div class="value">{value}</div></div>')
    out.append("</div>")

    out.append("<h2>Cases</h2><table><thead><tr><th>Case</th><th>Primary</th><th>Baseline</th><th>Change</th></tr></thead><tbody>")
    for case_id in ordered_ids:
        run_case = run_cases.get(case_id)
        base_case = base_cases.get(case_id)
        change = ""
        if run_case and base_case:
            diff = run_case["summary"]["passed"] - base_case["summary"]["passed"]
            klass = "delta-up" if diff > 0 else "delta-down" if diff < 0 else ""
            change = f'<span class="{klass}">{diff:+d}</span>'
        out.append(
            f"<tr><td><code>{esc(case_id)}</code></td><td>{_status(run_case)}</td>"
            f"<td>{_status(base_case)}</td><td>{change}</td></tr>"
        )
    out.append("</tbody></table>")

    if base_block:
        out.append("<h2>Diff summary</h2><table><thead><tr><th>Improved</th><th>Regressed</th><th>Unchanged</th></tr></thead>")
        out.append(
            "<tbody><tr>"
            f"<td class=\"pass\">{len(improved)}<br><small>{esc(', '.join(improved)) or '-'}</small></td>"
            f"<td class=\"fail\">{len(regressed)}<br><small>{esc(', '.join(regressed)) or '-'}</small></td>"
            f"<td>{len(unchanged)}<br><small>{esc(', '.join(unchanged)) or '-'}</small></td>"
            "</tr></tbody></table>"
        )

    out.append("<h2>Assertion details</h2>")
    for case_id in ordered_ids:
        for label, case in (("primary", run_cases.get(case_id)), ("baseline", base_cases.get(case_id))):
            if case is None:
                continue
            out.append(f"<details><summary><code>{esc(case_id)}</code> / {label} ({esc(case['configuration'])}) {_status(case)}</summary>")
            out.append("<table><thead><tr><th>Assertion</th><th>Type</th><th>Verdict</th><th>Evidence</th></tr></thead><tbody>")
            for item in case.get("assertions", []):
                verdict = '<span class="pass">pass</span>' if item["passed"] else '<span class="fail">fail</span>'
                out.append(
                    f"<tr><td><code>{esc(item.get('id', ''))}</code> {esc(item.get('description', ''))}</td>"
                    f"<td>{esc(str(item.get('type', '')))}</td><td>{verdict}</td>"
                    f"<td class=\"evidence\">{esc(item.get('evidence', ''))}</td></tr>"
                )
            out.append("</tbody></table></details>")

    out.append("</main><footer>Generated by skill-creator / generate_report.py. Deterministic grading only.</footer></body></html>")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a self-contained HTML eval report from run folders.")
    parser.add_argument("--run", required=True, type=Path, help="run folder with grading.json (primary)")
    parser.add_argument("--baseline", type=Path, help="older run folder to compare against")
    parser.add_argument("--out", type=Path, help="output HTML path (default: <run>/report.html)")
    parser.add_argument("--title", help="report title")
    args = parser.parse_args(argv)

    run = load_grading(args.run)
    baseline = load_grading(args.baseline) if args.baseline else None
    run, primary, base, base_config = pick_comparison(run, baseline)
    title = args.title or f"Eval report: {run.get('skill_name', args.run.name)}"
    out = args.out or (args.run / "report.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(run, primary, base, base_config, title), encoding="utf-8")
    print(f"report written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
