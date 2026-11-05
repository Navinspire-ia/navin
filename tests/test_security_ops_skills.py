"""The security and operations skills added in 2.4.9 are complete and their scripts run.

Eleven built-in skills ship a SKILL.md, references, standard-library scripts
and the sample files those scripts run on. This test keeps that contract:
the validator passes without a warning, no long dash slips in, every script
stays standard-library only and never spawns a process (a skill script runs
on the user's machine, in a frozen Navin, without extra dependencies), and
every command documented in SKILL.md runs on the bundled sample with one of
its documented exit codes (0 clean, 1 findings; 2 would mean the documented
command is wrong).
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "navin" / "skills"
NEW_SKILLS = [
    "agent-runtime-guard",
    "ai-llm-telemetry",
    "data-dlp-monitor",
    "data-lineage-observer",
    "disaster-recovery-planner",
    "identity-access-review",
    "sbom-attestation",
    "siem-edr-connector",
    "slo-error-budget",
    "third-party-risk-register",
    "threat-intelligence",
]
REQUIRED_SECTIONS = ("## Overview", "## Workflow", "## Rules", "## Deliverable", "## Resources")
DOCUMENTED_COMMAND = re.compile(r"(python3 scripts/\S+\.py[^`\n]*)")
SHELL_PIPE = re.compile(r"\s(\||>|<|&&|;)\s")
# A checker suppression marker in a skill script; the scripts must pass ruff and mypy as written.
SILENCED_CHECKER = re.compile(r"#\s*(noqa\b|type:\s*ignore\b)")


def _validator():
    path = SKILLS / "skill-development" / "scripts" / "validate_skill.py"
    spec = importlib.util.spec_from_file_location("validate_skill_new_skills", path)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = False
    return module


def _text_files(folder: Path):
    for path in folder.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            yield path


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_skill_is_complete_and_validates_without_a_warning(name):
    folder = SKILLS / name
    assert folder.is_dir(), f"{name}: skill folder missing"
    result = _validator().lint(folder)
    assert result["passed"], result["critical"]
    assert result["warnings"] == [], result["warnings"]
    body = (folder / "SKILL.md").read_text(encoding="utf-8")
    missing = [section for section in REQUIRED_SECTIONS if section not in body]
    assert not missing, f"{name}: SKILL.md lacks {missing}"
    assert result["stats"]["body_words"] <= 3000, result["stats"]
    assert result["stats"].get("scripts", 0) >= 1 and result["stats"].get("references", 0) >= 1
    for path in _text_files(folder):
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "\u2014" not in text and "\u2013" not in text, f"{path.relative_to(ROOT)}: long dash"


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_scripts_are_standard_library_only_and_never_spawn(name):
    scripts = sorted((SKILLS / name / "scripts").glob("*.py"))
    assert scripts, f"{name}: no scripts"
    for script in scripts:
        assert script.stat().st_mode & 0o111, f"{script.name} is not executable"
        tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
        dotted: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                dotted.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                dotted.add(node.module)
        modules = {m.split(".")[0] for m in dotted}
        # A sibling script in the same folder may be imported (shared parser); anything else must be stdlib.
        foreign = sorted(m for m in modules if m not in sys.stdlib_module_names and not (script.parent / f"{m}.py").is_file())
        assert not foreign, f"{script.relative_to(ROOT)} imports {foreign}"
        # urllib.parse only parses; the request and http modules would reach the network.
        reaching = sorted(dotted & {"subprocess", "socket", "urllib.request", "http.client", "ssl", "ftplib", "smtplib"})
        assert not reaching, f"{script.name} spawns or connects: {reaching}"
        text = script.read_text(encoding="utf-8")
        silenced = SILENCED_CHECKER.search(text)
        assert silenced is None, f"{script.name} silences a checker: {silenced.group(0) if silenced else ''}"
        assert "except Exception" not in text, f"{script.name} catches every error"


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_documented_commands_run_on_the_bundled_examples(name, tmp_path):
    folder = SKILLS / name
    body = (folder / "SKILL.md").read_text(encoding="utf-8")
    # The documented sample runs are the commands that name a bundled example; a
    # command with a placeholder file (drill.json) is a template, not a run.
    commands = sorted({c.strip() for c in DOCUMENTED_COMMAND.findall(body) if "examples/" in c and not SHELL_PIPE.search(c)})
    assert commands, f"{name}: SKILL.md documents no `python3 scripts/... examples/...` command"
    # Run in a copy so a command that writes (--out) never touches the packaged skill.
    work = tmp_path / name
    shutil.copytree(folder, work, ignore=shutil.ignore_patterns("__pycache__"))
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    scripts_run: set[str] = set()
    for command in commands:
        argv = shlex.split(command)
        argv[0] = sys.executable
        done = subprocess.run(argv, cwd=work, capture_output=True, text=True, timeout=60, env=env)
        assert done.returncode in (0, 1), f"{name}: `{command}` exited {done.returncode}\n{done.stderr[-800:]}"
        assert done.stdout.strip() or "--out" in command, f"{name}: `{command}` printed nothing"
        scripts_run.add(argv[1])
    documented = {f"scripts/{p.name}" for p in (folder / "scripts").glob("*.py")}
    assert documented <= scripts_run, f"{name}: no documented command for {sorted(documented - scripts_run)}"
