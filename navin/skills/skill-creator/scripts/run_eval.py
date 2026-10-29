#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Prepare eval prompts for a skill and grade model outputs deterministically.

This script never calls a language model. Navin runs each prepared prompt
through `spawn` subagents (or sequentially in the main agent) and saves the
transcript plus output files under a results folder. This script then grades
those files with simple checks: contains, regex, file_exists, json_path.

Usage:
    run_eval.py --skill <folder> --evals <evals.json> --run-dir <dir> [--case <id>]
    run_eval.py --skill <folder> --evals <evals.json> --run-dir <dir> --results <dir>

Run folder layout written by this script:
    <run-dir>/run.json                             metadata for the run
    <run-dir>/cases/<case-id>/case.json            prompt, assertions, configurations
    <run-dir>/cases/<case-id>/with_skill/prompt.md skill body + task
    <run-dir>/cases/<case-id>/without_skill/prompt.md  task only
    <run-dir>/cases/<case-id>/<config>/grading.json    after --results
    <run-dir>/grading.json                         run-level grading summary

Results folder layout expected with --results:
    <results>/<case-id>/<config>/transcript.md     what the model wrote
    <results>/<case-id>/<config>/outputs/...       files it produced

Schemas: see references/eval-schema.md next to this script's skill.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

CONFIGURATIONS = ("with_skill", "without_skill")
TRANSCRIPT_NAMES = ("transcript.md", "transcript.txt", "output.md", "output.txt")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(value: object) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return text or "case"


def load_evals(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("evals"), list):
        raise SystemExit(f"{path}: expected an object with an 'evals' list")
    seen: set[str] = set()
    for index, case in enumerate(data["evals"]):
        if not isinstance(case, dict) or not str(case.get("prompt", "")).strip():
            raise SystemExit(f"{path}: eval #{index} needs a non-empty 'prompt'")
        case_id = _slug(case.get("id", index + 1))
        if case_id in seen:
            raise SystemExit(f"{path}: duplicate eval id {case_id!r}")
        seen.add(case_id)
        case["id"] = case_id
        case.setdefault("assertions", [])
        case.setdefault("files", [])
    return data


def read_skill(skill_dir: Path) -> tuple[str, str]:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise SystemExit(f"SKILL.md not found in {skill_dir}")
    text = skill_md.read_text(encoding="utf-8")
    name = skill_dir.name
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if match:
        found = re.search(r"^name:\s*(.+)$", match.group(1), re.MULTILINE)
        if found:
            name = found.group(1).strip().strip("\"'")
    return name, text


def build_prompt(case: dict, skill_body: str | None, skill_name: str) -> str:
    parts: list[str] = []
    if skill_body is not None:
        parts.append(
            f"The skill `{skill_name}` below applies to this task. Follow it, run its "
            "scripts when it says so, and read its references when a step needs them.\n"
        )
        parts.append("<skill>\n" + skill_body.rstrip() + "\n</skill>\n")
    parts.append("# Task\n\n" + str(case["prompt"]).strip() + "\n")
    if case.get("files"):
        parts.append("Input files:\n" + "\n".join(f"- {f}" for f in case["files"]) + "\n")
    parts.append(
        "Save every file the task asks for under `outputs/` in the working folder and "
        "write what was done, in order, to `transcript.md`."
    )
    return "\n".join(parts)


def prepare(skill_dir: Path, evals: dict, run_dir: Path, only_case: str | None) -> list[dict]:
    skill_name, skill_body = read_skill(skill_dir)
    cases = [c for c in evals["evals"] if only_case is None or c["id"] == _slug(only_case)]
    if not cases:
        raise SystemExit(f"no eval case matches {only_case!r}")
    run_dir.mkdir(parents=True, exist_ok=True)
    for case in cases:
        case_dir = run_dir / "cases" / case["id"]
        for config in CONFIGURATIONS:
            target = case_dir / config
            target.mkdir(parents=True, exist_ok=True)
            body = skill_body if config == "with_skill" else None
            (target / "prompt.md").write_text(build_prompt(case, body, skill_name), encoding="utf-8")
        (case_dir / "case.json").write_text(
            json.dumps(
                {
                    "case_id": case["id"],
                    "prompt": case["prompt"],
                    "expected_behaviour": case.get("expected_behaviour", ""),
                    "files": case["files"],
                    "assertions": case["assertions"],
                    "configurations": list(CONFIGURATIONS),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    run_meta = {
        "run_id": run_dir.name,
        "skill_name": skill_name,
        "skill_path": str(skill_dir),
        "evals_file": evals.get("_source", ""),
        "prepared_at": _now(),
        "configurations": list(CONFIGURATIONS),
        "cases": [c["id"] for c in cases],
        "grader": "run_eval.py deterministic checks; no model call",
    }
    (run_dir / "run.json").write_text(json.dumps(run_meta, indent=2), encoding="utf-8")
    return cases


# ---------------------------------------------------------------- grading


def _read_target(result_dir: Path, target: str) -> tuple[str | None, str]:
    """Return (text, label) for an assertion target; text is None when missing."""
    target = (target or "transcript").strip()
    if target == "transcript":
        for name in TRANSCRIPT_NAMES:
            candidate = result_dir / name
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8", errors="replace"), name
        return None, "transcript"
    if target == "outputs":
        outputs = result_dir / "outputs"
        if not outputs.is_dir():
            return None, "outputs/"
        chunks = [
            p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(outputs.rglob("*"))
            if p.is_file()
        ]
        return "\n".join(chunks), "outputs/"
    candidate = result_dir / target
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8", errors="replace"), target
    return None, target


def _json_path(data: object, path: str) -> tuple[bool, object]:
    """Walk a dotted path; list items accept ``[n]`` or a bare numeric token."""
    current = data
    for token in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if token.startswith("[") or (token.isdigit() and isinstance(current, list)):
            index = int(token.strip("[]"))
            if not isinstance(current, list) or index >= len(current):
                return False, None
            current = current[index]
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            return False, None
    return True, current


def check_assertion(assertion: dict, result_dir: Path) -> tuple[bool, str]:
    kind = str(assertion.get("type", "contains")).lower()
    negate = bool(assertion.get("negate", False))
    passed, evidence = False, ""
    if kind == "file_exists":
        rel = str(assertion.get("path", ""))
        passed = bool(rel) and (result_dir / rel).is_file()
        evidence = f"{rel} {'exists' if passed else 'is missing'}"
    elif kind in ("contains", "regex"):
        text, label = _read_target(result_dir, str(assertion.get("target", "transcript")))
        if text is None:
            return (False if not negate else True), f"{label} not found in results"
        if kind == "contains":
            needle = str(assertion.get("value", ""))
            hay = text
            if assertion.get("case_insensitive"):
                needle, hay = needle.lower(), text.lower()
            passed = bool(needle) and needle in hay
            evidence = f"{label} {'contains' if passed else 'does not contain'} {needle!r}"
        else:
            pattern = str(assertion.get("pattern", ""))
            flags = re.IGNORECASE if assertion.get("case_insensitive") else 0
            try:
                found = re.search(pattern, text, flags | re.MULTILINE) if pattern else None
            except re.error as exc:
                return False, f"invalid regex {pattern!r}: {exc}"
            passed = found is not None
            evidence = (
                f"{label} matches /{pattern}/ at {found.group(0)[:80]!r}"
                if found
                else f"{label} has no match for /{pattern}/"
            )
    elif kind == "json_path":
        rel = str(assertion.get("path", ""))
        candidate = result_dir / rel
        if not candidate.is_file():
            return (negate, f"{rel} is missing")
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return (negate, f"{rel} is not valid JSON: {exc}")
        found, value = _json_path(data, str(assertion.get("json_path", "")))
        if "equals" in assertion:
            passed = found and value == assertion["equals"]
            evidence = f"{rel}:{assertion.get('json_path')} = {value!r} (expected {assertion['equals']!r})"
        else:
            passed = found
            evidence = f"{rel}:{assertion.get('json_path')} {'found' if found else 'missing'}"
    else:
        return False, f"unknown assertion type {kind!r}"
    if negate:
        passed = not passed
        evidence = f"negated: {evidence}"
    return passed, evidence


def grade_case(case: dict, result_dir: Path, configuration: str) -> dict:
    graded = []
    for index, assertion in enumerate(case.get("assertions", [])):
        passed, evidence = check_assertion(assertion, result_dir)
        graded.append(
            {
                "id": str(assertion.get("id", f"a{index + 1}")),
                "type": assertion.get("type", "contains"),
                "description": assertion.get("description", ""),
                "passed": passed,
                "evidence": evidence,
            }
        )
    passed = sum(1 for g in graded if g["passed"])
    total = len(graded)
    return {
        "case_id": case["id"],
        "configuration": configuration,
        "graded_at": _now(),
        "results_dir": str(result_dir),
        "assertions": graded,
        "summary": {
            "passed": passed,
            "failed": total - passed,
            "total": total,
            "pass_rate": round(passed / total, 4) if total else 0.0,
        },
    }


def grade(cases: list[dict], run_dir: Path, results_dir: Path, skill_name: str) -> dict:
    configurations: dict[str, dict] = {}
    for case in cases:
        for config in CONFIGURATIONS:
            result_dir = results_dir / case["id"] / config
            if not result_dir.is_dir():
                continue
            grading = grade_case(case, result_dir, config)
            target = run_dir / "cases" / case["id"] / config
            target.mkdir(parents=True, exist_ok=True)
            (target / "grading.json").write_text(json.dumps(grading, indent=2), encoding="utf-8")
            configurations.setdefault(config, {"cases": []})["cases"].append(grading)
    for config, block in configurations.items():
        passed = sum(c["summary"]["passed"] for c in block["cases"])
        total = sum(c["summary"]["total"] for c in block["cases"])
        block["summary"] = {
            "cases": len(block["cases"]),
            "cases_fully_passed": sum(
                1 for c in block["cases"] if c["summary"]["total"] and c["summary"]["failed"] == 0
            ),
            "passed": passed,
            "failed": total - passed,
            "total": total,
            "pass_rate": round(passed / total, 4) if total else 0.0,
        }
    report = {
        "run_id": run_dir.name,
        "skill_name": skill_name,
        "graded_at": _now(),
        "results_dir": str(results_dir),
        "configurations": configurations,
    }
    (run_dir / "grading.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare one prompt per eval case (with and without the skill body) and, "
            "when --results is given, grade model outputs with deterministic checks. "
            "No language model is called here: Navin runs the prompts through spawn."
        ),
        epilog="Assertion types: contains, regex, file_exists, json_path (all accept negate: true).",
    )
    parser.add_argument("--skill", required=True, type=Path, help="skill folder containing SKILL.md")
    parser.add_argument("--evals", required=True, type=Path, help="evals.json file")
    parser.add_argument("--run-dir", required=True, type=Path, help="folder that receives prompts and grading")
    parser.add_argument("--case", help="only this case id")
    parser.add_argument("--results", type=Path, help="folder with <case-id>/<config>/ model outputs to grade")
    args = parser.parse_args(argv)

    evals = load_evals(args.evals)
    evals["_source"] = str(args.evals)
    cases = prepare(args.skill, evals, args.run_dir, args.case)
    print(f"prepared {len(cases)} case(s) in {args.run_dir}")
    if args.results:
        if not args.results.is_dir():
            print(f"results folder not found: {args.results}", file=sys.stderr)
            return 1
        skill_name, _ = read_skill(args.skill)
        report = grade(cases, args.run_dir, args.results, skill_name)
        for config, block in report["configurations"].items():
            summary = block["summary"]
            print(f"{config}: {summary['passed']}/{summary['total']} assertions passed ({summary['pass_rate']:.0%})")
        if not report["configurations"]:
            print("no <case-id>/<config>/ folders found in results; nothing graded", file=sys.stderr)
            return 1
        print(f"grading written to {args.run_dir / 'grading.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
