# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Built packages and the honest delivery report of a conversion.

"Delivered" needs a package on disk AND every required test passed with its
evidence. A compiled build alone is "partial": it is not proof that the app
installs, starts, logs in or reaches its backend.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from navin.appfactory.scaffold import load_policy
from navin.appfactory.targets import STEPS, get_target

TEST_STATUSES = ("passed", "failed", "not_run")
STEP_STATUSES = ("done", "failed", "skipped", "pending")


def _size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{num} B"


def artifacts(project_root: str | Path, target_name: str) -> dict[str, Any]:
    """Packages of the target found under ``os/<target>``, newest first."""
    target = get_target(target_name)
    folder = Path(project_root).expanduser().resolve() / "os" / target.id
    patterns = (load_policy("builds").get("targets", {}).get(target.id, {}) or {}).get("artifacts") or []
    found: dict[Path, dict[str, Any]] = {}
    if folder.is_dir():
        for pattern in patterns:
            for path in folder.glob(pattern):
                if not path.exists() or path in found:
                    continue
                stat = path.stat()
                size = stat.st_size if path.is_file() else sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
                found[path] = {
                    "path": path.relative_to(folder.parent.parent).as_posix(),
                    "name": path.name,
                    "kind": path.suffix.lstrip(".") or "app",
                    "size": size,
                    "size_text": _size(size),
                    "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
                    "mtime": stat.st_mtime,
                    "debug": "debug" in path.as_posix().lower(),
                }
    items = sorted(found.values(), key=lambda item: item["mtime"], reverse=True)
    return {"target": target.id, "folder": target.folder, "artifacts": items,
            "next": "" if items else "no package yet: run the build steps of the plan, then look again"}


def required_tests(target_name: str, has_auth: bool) -> list[str]:
    policy = load_policy("builds")
    target = get_target(target_name)
    tests = list((policy.get("targets", {}).get(target.id, {}) or {}).get("required_tests") or [])
    auth = str(policy.get("auth_test") or "auth")
    if has_auth and auth not in tests:
        tests.append(auth)
    return tests


def _clean_tests(tests: list[dict[str, Any]], evidence_required: bool) -> list[dict[str, Any]]:
    out = []
    for raw in tests or []:
        if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
            raise ValueError("each test needs a name and a status (passed, failed or not_run)")
        status = str(raw.get("status") or "not_run").strip().lower()
        if status not in TEST_STATUSES:
            raise ValueError(f"test status must be passed, failed or not_run (got {status!r})")
        evidence = str(raw.get("evidence") or "").strip()
        item = {"name": str(raw["name"]).strip(), "status": status, "evidence": evidence}
        if status == "passed" and evidence_required and not evidence:
            item["status"] = "not_run"
            item["note"] = "reported as passed without evidence: counted as not run"
        out.append(item)
    return out


def _clean_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for raw in steps or []:
        if not isinstance(raw, dict) or not str(raw.get("step") or "").strip():
            raise ValueError("each step needs step= (analyze, compat, toolchain, transform, build, test, deliver) and a status")
        status = str(raw.get("status") or "pending").strip().lower()
        if status not in STEP_STATUSES:
            raise ValueError(f"step status must be one of {', '.join(STEP_STATUSES)} (got {status!r})")
        out.append({"step": str(raw["step"]).strip(), "status": status, "note": str(raw.get("note") or "").strip()})
    return out


def _covers(test_name: str, required: str) -> bool:
    name = test_name.lower()
    return name == required or name.startswith(required + " ") or name.startswith(required + ":") or name.startswith(required + "-")


def report(project_root: str | Path, target_name: str, steps: list[dict[str, Any]], tests: list[dict[str, Any]], *,
           has_auth: bool = False, notes: str = "", limitations: list[str] | None = None) -> dict[str, Any]:
    """Write ``.navin/appfactory/<target>/report.{json,md}`` and return the verdict."""
    target = get_target(target_name)
    root = Path(project_root).expanduser().resolve()
    policy = load_policy("builds")
    clean_steps = _clean_steps(steps)
    clean_tests = _clean_tests(tests, bool(policy.get("evidence_required", True)))
    packages = artifacts(root, target.id)["artifacts"]
    required = required_tests(target.id, has_auth)
    coverage = []
    for name in required:
        matching = [test for test in clean_tests if _covers(test["name"], name)]
        status = "not_run"
        if matching and all(test["status"] == "passed" for test in matching):
            status = "passed"
        elif any(test["status"] == "failed" for test in matching):
            status = "failed"
        coverage.append({"test": name, "status": status})
    failed_steps = [step["step"] for step in clean_steps if step["status"] == "failed"]
    failed_tests = [test["name"] for test in clean_tests if test["status"] == "failed"]
    missing = [item["test"] for item in coverage if item["status"] != "passed"]
    reasons = []
    if not packages:
        reasons.append("no package was built")
    if failed_steps:
        reasons.append("failed steps: " + ", ".join(failed_steps))
    if failed_tests:
        reasons.append("failed tests: " + ", ".join(failed_tests))
    if missing:
        reasons.append("required tests not passed: " + ", ".join(missing))
    if not reasons:
        status = "delivered"
    elif not packages and (failed_steps or failed_tests):
        status = "failed"
    else:
        status = "partial"
    data = {
        "target": target.id,
        "label": target.label,
        "status": status,
        "reasons": reasons,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "steps": clean_steps,
        "tests": clean_tests,
        "required_tests": coverage,
        "executed": [test["name"] for test in clean_tests if test["status"] != "not_run"],
        "not_executed": [test["name"] for test in clean_tests if test["status"] == "not_run"],
        "artifacts": [{key: item[key] for key in ("path", "name", "size_text", "modified", "debug")} for item in packages],
        "limitations": [str(item) for item in (limitations or []) if str(item).strip()],
        "notes": notes.strip(),
    }
    out_dir = root / ".navin" / "appfactory" / target.id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(_markdown(data), encoding="utf-8")
    data["report"] = (out_dir / "report.md").relative_to(root).as_posix()
    return data


_MARK = {"passed": "[x]", "failed": "[!]", "not_run": "[ ]", "done": "[x]", "skipped": "[-]", "pending": "[ ]"}


def _markdown(data: dict[str, Any]) -> str:
    title = {"delivered": "Delivered", "partial": "Partial delivery", "failed": "Not delivered"}[data["status"]]
    lines = [f"# {data['label']} app: {title}", "", f"Generated {data['generated']}.", ""]
    if data["reasons"]:
        lines += ["Why it is not fully delivered:", ""] + [f"- {reason}" for reason in data["reasons"]] + [""]
    lines += ["## Packages", ""]
    lines += [f"- `{item['path']}` ({item['size_text']}, {item['modified']}){' - debug build' if item['debug'] else ''}"
              for item in data["artifacts"]] or ["- none"]
    order = {name: index for index, name in enumerate(STEPS)}
    lines += ["", "## Steps", ""]
    lines += [f"- {_MARK[step['status']]} {step['step']}{(': ' + step['note']) if step['note'] else ''}"
              for step in sorted(data["steps"], key=lambda step: order.get(step["step"], 99))] or ["- none reported"]
    lines += ["", "## Tests executed", ""]
    executed = [test for test in data["tests"] if test["status"] != "not_run"]
    lines += [f"- {_MARK[test['status']]} {test['name']}: {test['evidence'] or 'no evidence'}" for test in executed] or ["- none"]
    lines += ["", "## Tests not executed", ""]
    skipped = [test for test in data["tests"] if test["status"] == "not_run"]
    lines += [f"- {test['name']}{(': ' + test.get('note', '') + ' ' + test['evidence']).rstrip() if test.get('note') or test['evidence'] else ''}"
              for test in skipped] or ["- none"]
    lines += ["", "## Required before delivery", ""]
    lines += [f"- {_MARK[item['status']]} {item['test']}" for item in data["required_tests"]]
    if data["limitations"]:
        lines += ["", "## Limitations", ""] + [f"- {item}" for item in data["limitations"]]
    if data["notes"]:
        lines += ["", "## Notes", "", data["notes"]]
    return "\n".join(lines) + "\n"
