# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the skill-creator evaluation loop scripts.

The loop never calls a model: run_eval.py prepares prompts and grades result
folders, aggregate_benchmark.py compares configurations, generate_report.py
writes an HTML report and improve_description.py scores a description.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "navin" / "skills" / "skill-creator"
SCRIPTS = SKILL / "scripts"
PY = sys.executable


def _run(*args: str, expect: int | None = 0) -> subprocess.CompletedProcess:
    proc = subprocess.run([PY, *args], capture_output=True, text=True, timeout=60, env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"})
    if expect is not None:
        assert proc.returncode == expect, f"{args}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    return proc


def _load(name: str, path: Path | None = None):
    spec = importlib.util.spec_from_file_location(name, path or SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Never leave bytecode inside the packaged skills.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


@pytest.fixture()
def evals(tmp_path: Path) -> Path:
    path = tmp_path / "evals.json"
    path.write_text(json.dumps({
        "skill": "pdf",
        "evals": [
            {"id": "fill-form", "prompt": "Fill the form.", "assertions": [
                {"type": "file_exists", "path": "outputs/filled.pdf"},
                {"type": "contains", "target": "transcript", "value": "pdf_forms.py"},
                {"type": "regex", "target": "transcript", "pattern": "(?i)flatten", "negate": True},
                {"type": "json_path", "path": "outputs/report.json", "json_path": "filled.0", "equals": "Name"},
                {"type": "json_path", "path": "outputs/report.json", "json_path": "filled[0]", "equals": "Name"},
            ]},
            {"id": "near-miss", "prompt": "Write a poem.", "assertions": [{"type": "contains", "value": "pdf_", "negate": True}]},
        ],
    }), encoding="utf-8")
    return path


def _fake_results(run: Path) -> Path:
    results = run / "results"
    for case in ("fill-form", "near-miss"):
        for cfg in ("with_skill", "without_skill"):
            (results / case / cfg / "outputs").mkdir(parents=True)
    good = results / "fill-form" / "with_skill"
    (good / "transcript.md").write_text("ran pdf_forms.py fill", encoding="utf-8")
    (good / "outputs" / "filled.pdf").write_bytes(b"%PDF-1.4")
    (good / "outputs" / "report.json").write_text(json.dumps({"filled": ["Name"]}), encoding="utf-8")
    (results / "fill-form" / "without_skill" / "transcript.md").write_text("wrote pypdf code", encoding="utf-8")
    for cfg in ("with_skill", "without_skill"):
        (results / "near-miss" / cfg / "transcript.md").write_text("a poem", encoding="utf-8")
    return results


def test_skill_md_cites_every_script_and_reference():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert not re.search("[\u2013\u2014]", text)
    for script in SCRIPTS.glob("*.py"):
        assert script.name in text, f"{script.name} is not mentioned in SKILL.md"
        assert script.stat().st_mode & 0o111, f"{script.name} is not executable"
    for ref in (SKILL / "references").glob("*.md"):
        assert f"references/{ref.name}" in text, f"{ref.name} is not cited"
    validator = _load("validate_skill", ROOT / "navin/skills/skill-development/scripts/validate_skill.py")
    report = validator.lint(SKILL)
    assert report["passed"], report["critical"]


def test_run_eval_prepares_grades_and_benchmarks(evals: Path, tmp_path: Path):
    run = tmp_path / "run1"
    _run(str(SCRIPTS / "run_eval.py"), "--skill", str(ROOT / "navin/skills/pdf"), "--evals", str(evals), "--run-dir", str(run))
    prompt = (run / "cases" / "fill-form" / "with_skill" / "prompt.md").read_text(encoding="utf-8")
    assert "<skill>" in prompt and "Fill the form." in prompt
    assert "<skill>" not in (run / "cases" / "fill-form" / "without_skill" / "prompt.md").read_text(encoding="utf-8")

    results = _fake_results(run)
    _run(str(SCRIPTS / "run_eval.py"), "--skill", str(ROOT / "navin/skills/pdf"), "--evals", str(evals), "--run-dir", str(run), "--results", str(results))
    grading = json.loads((run / "grading.json").read_text(encoding="utf-8"))
    with_skill = grading["configurations"]["with_skill"]["summary"]
    without = grading["configurations"]["without_skill"]["summary"]
    assert with_skill["passed"] == with_skill["total"] == 6
    assert with_skill["cases_fully_passed"] == 2
    assert without["passed"] < with_skill["passed"]

    bench = tmp_path / "bench"
    _run(str(SCRIPTS / "aggregate_benchmark.py"), str(run / "grading.json"), "--primary", "with_skill", "--baseline", "without_skill", "--out", str(bench))
    data = json.loads((bench / "benchmark.json").read_text(encoding="utf-8"))
    assert data["configurations"]["with_skill"]["pass_rate"]["mean"] == 1.0
    assert data["delta"]["pass_rate"] > 0 and (bench / "benchmark.md").is_file()

    report = tmp_path / "report.html"
    _run(str(SCRIPTS / "generate_report.py"), "--run", str(run), "--out", str(report))
    html = report.read_text(encoding="utf-8")
    assert "with_skill" in html and "c6e88a" in html.lower()
    assert not re.search("[\u2013\u2014]", html)


def test_json_path_accepts_bare_and_bracket_indexes():
    module = _load("run_eval")
    data = {"filled": ["Name", "Agree"], "meta": {"ok": True}}
    assert module._json_path(data, "filled.0") == (True, "Name")
    assert module._json_path(data, "filled[1]") == (True, "Agree")
    assert module._json_path(data, "meta.ok") == (True, True)
    assert module._json_path(data, "filled.5") == (False, None)
    assert module._json_path(data, "nope.x") == (False, None)


def test_run_eval_rejects_broken_evals(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"evals": [{"id": "x"}]}), encoding="utf-8")
    proc = _run(str(SCRIPTS / "run_eval.py"), "--skill", str(ROOT / "navin/skills/pdf"), "--evals", str(bad), "--run-dir", str(tmp_path / "r"), expect=None)
    assert proc.returncode != 0 and "prompt" in (proc.stdout + proc.stderr)


def test_improve_description_scores_triggers(tmp_path: Path):
    triggers = tmp_path / "triggers.json"
    triggers.write_text(json.dumps([
        {"query": "fill this PDF form", "should_trigger": True},
        {"query": "merge these PDFs", "should_trigger": True},
        {"query": "write a cover letter", "should_trigger": False},
    ]), encoding="utf-8")
    proc = _run(str(SCRIPTS / "improve_description.py"), "--skill", str(ROOT / "navin/skills/pdf"), "--triggers", str(triggers), "--json")
    data = json.loads(proc.stdout)
    assert "current" in data and isinstance(data["current"].get("score"), (int, float))
    assert data["current"]["score"] > 0
