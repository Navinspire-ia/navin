"""Plugin-dev skills: discoverable, valid frontmatter, working helper scripts."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[1] / "navin" / "skills"
NAMES = (
    "plugin-structure",
    "skill-development",
    "agent-development",
    "command-development",
    "hook-development",
    "mcp-integration",
    "plugin-settings",
)


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


validate_plugin = _load(SKILLS / "plugin-structure" / "scripts" / "validate_plugin.py")
scaffold_plugin = _load(SKILLS / "plugin-structure" / "scripts" / "scaffold_plugin.py")
validate_skill = _load(SKILLS / "skill-development" / "scripts" / "validate_skill.py")


@pytest.mark.parametrize("name", NAMES)
def test_skill_passes_own_lint_and_is_brand_clean(name: str) -> None:
    folder = SKILLS / name
    result = validate_skill.lint(folder)
    assert result["passed"], result["critical"]
    for path in folder.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            text = path.read_text(encoding="utf-8")
            assert "claude" not in text.lower(), path
            assert "\u2014" not in text and "\u2013" not in text, path


def test_skills_are_listed_by_loader(tmp_path: Path) -> None:
    from navin.agent.skills import SkillsLoader

    names = {s["name"] for s in SkillsLoader(tmp_path).list_skills(filter_unavailable=False)}
    assert set(NAMES) <= names


def test_scaffold_then_validate_passes(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    kinds = ["skills", "agents", "commands", "hooks", "mcp"]
    created = scaffold_plugin.scaffold(target, "demo", "Demo plugin", "Jane", kinds)
    assert (target / "plugin.json").is_file() and created
    report = validate_plugin.validate(target)
    assert report.critical == []
    assert report.counts == {"skills": 1, "agents": 1, "commands": 1, "hooks": 1, "mcp_servers": 1}
    # Re-running never overwrites existing files.
    assert scaffold_plugin.scaffold(target, "demo", "", "", kinds) == []


def test_validate_flags_broken_plugin(tmp_path: Path) -> None:
    root = tmp_path / "bad"
    (root / "skills" / "x").mkdir(parents=True)
    (root / "skills" / "x" / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
    (root / "plugin.json").write_text(json.dumps({"name": "Bad Name"}), encoding="utf-8")
    (root / "hooks").mkdir()
    (root / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {"PreToolUze": [], "Stop": [{"hooks": [{"type": "command"}]}]}}),
        encoding="utf-8",
    )
    (root / "mcp.json").write_text(
        json.dumps({"mcpServers": {"s": {"url": "https://x", "headers": {"A": "Bearer abcdefghijklmnopqrstu"}}}}),
        encoding="utf-8",
    )
    joined = "\n".join(validate_plugin.validate(root).critical)
    for needle in ("invalid name", "missing YAML frontmatter", "unknown event 'PreToolUze'",
                   "'command' is required", "hard-coded secret"):
        assert needle in joined


def test_validate_empty_folder_fails(tmp_path: Path) -> None:
    assert validate_plugin.main([str(tmp_path)]) == 1


def test_validate_agent_script(tmp_path: Path) -> None:
    script = SKILLS / "agent-development" / "scripts" / "validate-agent.sh"
    good = tmp_path / "reviewer-bot.md"
    good.write_text(
        "---\nname: reviewer-bot\ndescription: Use this agent when the user asks to review code. "
        "Examples:\n\n<example>\nuser: \"review\"\nassistant: \"I'll use reviewer-bot.\"\n</example>\n---\n\n"
        "You are a reviewer. Read the diff with git, check each changed function against its callers, "
        "and report every confirmed finding as a table with file, line, problem and fix.\n",
        encoding="utf-8",
    )
    bad = tmp_path / "x.md"
    bad.write_text("---\nname: X_\n---\n\nhi\n", encoding="utf-8")
    ok = subprocess.run(["bash", str(script), str(good)], capture_output=True, text=True)
    ko = subprocess.run(["bash", str(script), str(bad)], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert ko.returncode == 1 and "CRITICAL" in ko.stdout


def test_scripts_are_executable() -> None:
    for name in NAMES:
        for sub in ("scripts", "examples"):
            for path in (SKILLS / name / sub).glob("*"):
                if path.suffix in (".sh", ".py"):
                    assert path.stat().st_mode & 0o111, path
    assert sys.executable


def _bash(script: Path, *args: str, stdin: str = "", env: dict | None = None) -> subprocess.CompletedProcess:
    import os

    return subprocess.run(
        ["bash", str(script), *args], input=stdin, capture_output=True, text=True,
        env={**os.environ, **(env or {})}, timeout=30,
    )


def test_hook_examples_block_and_allow(tmp_path: Path) -> None:
    hooks = SKILLS / "hook-development"
    bash_hook = hooks / "examples" / "validate-bash.sh"
    blocked = _bash(bash_hook, stdin=json.dumps({"tool_input": {"command": "rm -rf /"}}))
    allowed = _bash(bash_hook, stdin=json.dumps({"tool_input": {"command": "ls -la"}}))
    assert blocked.returncode == 2 and "destructive" in blocked.stderr
    assert allowed.returncode == 0

    write_hook = hooks / "examples" / "validate-write.sh"
    env = {"NAVIN_PROJECT_DIR": str(tmp_path)}
    env_file = _bash(write_hook, stdin=json.dumps({"tool_input": {"path": ".env", "content": "A=1"}}), env=env)
    outside = _bash(write_hook, stdin=json.dumps({"tool_input": {"path": "../x.txt", "content": ""}}), env=env)
    fine = _bash(write_hook, stdin=json.dumps({"tool_input": {"path": "src/a.py", "content": "x = 1"}}), env=env)
    assert env_file.returncode == 2 and outside.returncode == 2
    assert fine.returncode == 0, fine.stderr


def test_hook_schema_validator_and_tester(tmp_path: Path) -> None:
    scripts = SKILLS / "hook-development" / "scripts"
    good = tmp_path / "hooks.json"
    good.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "exec", "hooks": [{"type": "command", "command": "bash x.sh", "timeout": 10}]}]}}))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"PreCompact": [], "Stop": [{"hooks": [{"type": "command"}]}]}))
    assert _bash(scripts / "validate-hook-schema.sh", str(good)).returncode == 0
    result = _bash(scripts / "validate-hook-schema.sh", str(bad))
    assert result.returncode == 1
    assert "unknown event 'PreCompact'" in result.stdout and "'command' is required" in result.stdout

    tested = _bash(scripts / "test-hook.sh", "PreToolUse",
                   str(SKILLS / "hook-development" / "examples" / "validate-bash.sh"),
                   '{"command": "mkfs.ext4 /dev/sda"}')
    assert "exit:   2 (BLOCK)" in tested.stdout
    linted = _bash(scripts / "hook-linter.sh", *map(str, (SKILLS / "hook-development" / "examples").glob("*.sh")))
    assert linted.returncode == 0, linted.stdout


def test_settings_scripts(tmp_path: Path) -> None:
    scripts = SKILLS / "plugin-settings" / "scripts"
    settings = tmp_path / ".navin" / "code-guard.local.md"
    settings.parent.mkdir()
    settings.write_text(
        "---\nenabled: false\nmode: warn\nprotected_paths:\n  - migrations/\n  - infra/\n---\n\nHouse rules here.\n",
        encoding="utf-8",
    )
    parse = scripts / "parse-frontmatter.sh"
    assert _bash(parse, str(settings), "enabled").stdout.strip() == "false"
    assert _bash(parse, str(settings), "protected_paths").stdout.split() == ["migrations/", "infra/"]
    assert "House rules" in _bash(parse, str(settings), "--body").stdout
    assert _bash(parse, str(tmp_path / "missing.md"), "enabled").stdout == ""

    validate = scripts / "validate-settings.sh"
    assert _bash(validate, str(settings), "enabled", "mode").returncode == 0
    assert _bash(validate, str(settings), "owner").returncode == 1
    broken = tmp_path / ".navin" / "broken.local.md"
    broken.write_text("---\nenabled: true\n", encoding="utf-8")
    assert _bash(validate, str(broken)).returncode == 1

    # The example hook honours the switch, then the protected list.
    hook = SKILLS / "plugin-settings" / "examples" / "read-settings-hook.sh"
    env = {"NAVIN_PROJECT_DIR": str(tmp_path), "NAVIN_PLUGIN_ROOT": str(SKILLS / "plugin-settings")}
    payload = json.dumps({"tool_input": {"path": "migrations/001.sql"}})
    assert _bash(hook, stdin=payload, env=env).returncode == 0
    settings.write_text(settings.read_text().replace("enabled: false", "enabled: true").replace("mode: warn", "mode: block"))
    assert _bash(hook, stdin=payload, env=env).returncode == 2


def test_mcp_examples_are_valid_plugin_configs(tmp_path: Path) -> None:
    for example in (SKILLS / "mcp-integration" / "examples").glob("*.json"):
        root = tmp_path / example.stem
        root.mkdir()
        (root / "plugin.json").write_text(json.dumps({"name": "demo", "version": "0.1.0", "description": "d"}))
        (root / "mcp.json").write_text(example.read_text(encoding="utf-8"))
        report = validate_plugin.validate(root)
        assert report.critical == [], (example.name, report.critical)
        assert report.counts["mcp_servers"] >= 1


@pytest.mark.parametrize(
    "plugin", sorted(p.parent.name for p in (SKILLS.parent / "builtin_plugins").glob("*/plugin.json"))
)
def test_shipped_plugins_pass_the_validator(plugin: str) -> None:
    """Bundled manifests name Navin's own agents, commands and skills."""
    report = validate_plugin.validate(SKILLS.parent / "builtin_plugins" / plugin)
    assert report.critical == [], report.critical
    assert sum(report.counts.values()) >= 2


def test_bare_names_resolve_to_builtins_or_fail(tmp_path: Path) -> None:
    root = tmp_path / "kit"
    (root / "agents").mkdir(parents=True)
    (root / "agents" / "local-helper.md").write_text(
        "---\nname: local-helper\ndescription: Use when a local helper is needed for the kit tasks.\n---\nYou review the kit changes and report what you found.\n"
    )
    manifest = {
        "name": "kit", "version": "0.1.0", "description": "d",
        "agents": ["local-helper", "code-explorer"], "commands": ["commit"], "skills": ["plugin-structure"],
        "steps": [{"step": "explore", "use": "agent:code-explorer"}, {"step": "commit", "use": "/commit"}],
    }
    (root / "plugin.json").write_text(json.dumps(manifest))
    report = validate_plugin.validate(root)
    assert report.critical == []
    assert report.counts["agents"] == 2 and report.counts["commands"] == 1 and report.counts["skills"] == 1

    manifest["agents"].append("no-such-agent")
    manifest["steps"].append({"step": "review", "use": "agent:code-reviewer"})
    manifest["steps"].append({"step": "oops"})
    (root / "plugin.json").write_text(json.dumps(manifest))
    joined = "\n".join(validate_plugin.validate(root).critical)
    assert "'no-such-agent', which neither the plugin nor Navin ships" in joined
    assert "uses 'agent:code-reviewer', which the manifest does not list" in joined
    assert "needs 'step' and 'use'" in joined
