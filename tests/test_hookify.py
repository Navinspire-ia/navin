"""Hookify rules: parsing, matching on Navin tool params, wiring into user hooks."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from navin.agent import hookify, user_hooks


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("NAVIN_HOOKIFY", raising=False)
    workspace = tmp_path / "proj"
    (workspace / ".navin").mkdir(parents=True)
    return workspace


def _rule(ws: Path, name: str, text: str) -> Path:
    path = ws / ".navin" / f"hookify.{name}.local.md"
    path.write_text(text, encoding="utf-8")
    return path


RM_RULE = """---
name: block-dangerous-rm
enabled: true
event: bash
pattern: rm\\s+-rf
action: block
---

Dangerous rm command detected.
"""

CONSOLE_RULE = """---
name: warn-console-log
enabled: true
event: file
pattern: console\\.log\\(
action: warn
---

Console.log detected.
"""

SENSITIVE_RULE = """---
name: warn-sensitive-files
enabled: true
event: file
action: warn
conditions:
  - field: file_path
    operator: regex_match
    pattern: \\.env$|\\.env\\.|credentials|secrets
---

Sensitive file detected.
"""

TESTS_STOP_RULE = """---
name: require-tests-run
enabled: true
event: stop
action: block
conditions:
  - field: transcript
    operator: not_regex_match
    pattern: npm test|pytest|cargo test
---

Run the tests before stopping.
"""


def test_short_pattern_and_conditions_parse(ws):
    _rule(ws, "rm", RM_RULE)
    _rule(ws, "env", SENSITIVE_RULE)
    rules = {r.name: r for r in hookify.load_rules(ws)}
    assert rules["block-dangerous-rm"].conditions[0].field == "command"
    assert rules["block-dangerous-rm"].action == "block"
    cond = rules["warn-sensitive-files"].conditions[0]
    assert (cond.field, cond.operator) == ("file_path", "regex_match")
    assert rules["warn-sensitive-files"].message == "Sensitive file detected."


def test_bash_block_on_exec_command(ws):
    _rule(ws, "rm", RM_RULE)
    verdict = hookify.evaluate(
        "PreToolUse", {"tool_name": "exec", "tool_input": {"command": "RM -rf build/"}}, ws
    )
    assert verdict.blocked
    assert "**[block-dangerous-rm]**" in verdict.message
    safe = hookify.evaluate("PreToolUse", {"tool_name": "exec", "tool_input": {"command": "ls"}}, ws)
    assert not safe.blocked and not safe.warning


@pytest.mark.parametrize(
    "tool, params",
    [
        ("write_file", {"path": "a.js", "content": "console.log(1)"}),
        ("write_file", {"files": [{"path": "b.js", "content": "x\nconsole.log(2)"}]}),
        ("edit_file", {"path": "a.js", "old_text": "x", "new_text": "console.log(x)"}),
        ("edit_file", {"path": "a.js", "edits": [{"old_text": "x", "new_text": "console.log(y)"}]}),
        ("apply_patch", {"edits": [{"path": "a.js", "action": "replace", "new_text": "console.log(z)"}]}),
    ],
)
def test_file_rules_read_navin_tool_params(ws, tool, params):
    _rule(ws, "console", CONSOLE_RULE)
    verdict = hookify.evaluate("PreToolUse", {"tool_name": tool, "tool_input": params}, ws)
    assert [r.name for r in verdict.warning] == ["warn-console-log"]
    assert not verdict.blocked


def test_old_text_is_not_new_text(ws):
    _rule(ws, "console", CONSOLE_RULE)
    removing = {"path": "a.js", "old_text": "console.log(x)", "new_text": ""}
    verdict = hookify.evaluate("PreToolUse", {"tool_name": "edit_file", "tool_input": removing}, ws)
    assert not verdict.warning


def test_file_path_condition_and_tool_matcher(ws):
    _rule(ws, "env", SENSITIVE_RULE)
    hit = hookify.evaluate(
        "PreToolUse", {"tool_name": "write_file", "tool_input": {"path": "app/.env", "content": "A=1"}}, ws
    )
    assert hit.warning
    _rule(ws, "only-bash", RM_RULE.replace("event: bash", "event: all\ntool_matcher: Bash"))
    # tool_matcher accepts the common alias of Navin's exec tool.
    alias = hookify.evaluate("PreToolUse", {"tool_name": "exec", "tool_input": {"command": "rm -rf x"}}, ws)
    assert alias.blocked


def test_disabled_broken_and_unsafe_rules_are_skipped(ws):
    _rule(ws, "off", RM_RULE.replace("enabled: true", "enabled: false"))
    _rule(ws, "redos", RM_RULE.replace("rm\\s+-rf", "(a+)+$").replace("block-dangerous-rm", "slow"))
    _rule(ws, "nomsg", "---\nname: empty\nevent: bash\npattern: ls\n---\n")
    assert hookify.load_rules(ws) == []
    every = {r.name: r for r in hookify.load_rules(ws, include_disabled=True)}
    assert any("nested quantifier" in p for p in every["slow"].problems)
    assert every["empty"].problems == ["empty message body"]
    assert hookify.main(["check", str(ws)]) == 1


def test_project_rule_overrides_personal_rule(ws):
    personal = Path.home() / ".navin"
    personal.mkdir(parents=True)
    (personal / "hookify.rm.local.md").write_text(RM_RULE.replace("action: block", "action: warn"), "utf-8")
    assert hookify.load_rules(ws)[0].action == "warn"
    _rule(ws, "rm", RM_RULE)
    assert [r.action for r in hookify.load_rules(ws)] == ["block"]


def test_kill_switch(ws, monkeypatch):
    _rule(ws, "rm", RM_RULE)
    monkeypatch.setenv("NAVIN_HOOKIFY", "0")
    verdict = hookify.evaluate("PreToolUse", {"tool_name": "exec", "tool_input": {"command": "rm -rf /"}}, ws)
    assert not verdict.blocked


def test_pre_tool_use_block_and_warning_notice(ws):
    _rule(ws, "rm", RM_RULE)
    _rule(ws, "console", CONSOLE_RULE)
    notices: list[str] = []

    async def notify(text: str) -> None:
        notices.append(text)

    async def run():
        blocked = await user_hooks.pre_tool_use_block(ws, "s", "exec", {"command": "rm -rf dist"}, notify=notify)
        allowed = await user_hooks.pre_tool_use_block(
            ws, "s", "write_file", {"path": "a.js", "content": "console.log(1)"}, notify=notify,
        )
        feedback = await user_hooks.post_tool_use_feedback(
            ws, "s", "write_file", {"path": "a.js", "content": "console.log(1)"}, "ok", notify=notify,
        )
        return blocked, allowed, feedback

    blocked, allowed, feedback = asyncio.run(run())
    assert blocked and "Dangerous rm command detected." in blocked
    assert allowed is None
    # The user sees the warning once (before the call), the model with the result.
    assert len(notices) == 1 and "warn-console-log" in notices[0]
    assert feedback and "Console.log detected." in feedback


def test_stop_rule_reads_the_transcript(ws):
    _rule(ws, "tests", TESTS_STOP_RULE)
    notices: list[str] = []

    async def notify(text: str) -> None:
        notices.append(text)

    no_tests = [{"role": "user", "content": "fix it"}, {"role": "assistant", "content": "done"}]
    with_tests = no_tests + [{
        "role": "assistant", "content": "",
        "tool_calls": [{"function": {"name": "exec", "arguments": '{"command": "python -m pytest -q"}'}}],
    }]

    async def run(messages):
        return await user_hooks.stop_hook_reason(
            ws, "s", "done", stop_hook_active=False, notify=notify, transcript=messages,
        )

    reason = asyncio.run(run(no_tests))
    assert reason and "Run the tests before stopping." in reason
    assert notices and "require-tests-run" in notices[0]
    assert asyncio.run(run(with_tests)) is None


def test_prompt_rule_warns_with_context(ws):
    _rule(ws, "prompt", "---\nname: deploy-check\nevent: prompt\npattern: deploy\n---\nCheck the release notes.\n")
    outcome = asyncio.run(user_hooks.prompt_hooks(ws, "s", "please Deploy now", new_session=False))
    assert not outcome.blocked
    assert "Check the release notes." in outcome.additional_context
    assert outcome.system_messages


def test_summary_lists_active_rules(ws):
    _rule(ws, "rm", RM_RULE)
    assert "block-dangerous-rm (bash, block)" in user_hooks.hooks_summary(ws)


def test_hookify_examples_parse_and_fire(ws):
    """The shipped example rules are valid and behave as documented."""
    import navin

    examples = Path(navin.__file__).parent / "skills" / "hookify-rules" / "examples"
    files = sorted(examples.glob("*.local.md"))
    assert len(files) == 4
    for path in files:
        (ws / ".navin" / f"hookify.{path.name}").write_text(path.read_text("utf-8"), "utf-8")
    rules = {r.name: r for r in hookify.load_rules(ws, include_disabled=True)}
    assert all(not r.problems for r in rules.values()), {n: r.problems for n, r in rules.items()}
    assert not rules["require-tests-run"].enabled
    rm = hookify.evaluate("PreToolUse", {"tool_name": "exec", "tool_input": {"command": "rm -rf x"}}, ws)
    assert rm.blocked
    env = hookify.evaluate(
        "PreToolUse", {"tool_name": "write_file", "tool_input": {"path": ".env", "content": "A=1"}}, ws
    )
    assert [r.name for r in env.warning] == ["warn-sensitive-files"]


def test_plugin_style_names_reach_builtin_hookify_commands(ws):
    from navin.agent.custom_commands import find_custom_command

    for token in ("/hookify", "/hookify-list", "/hookify:list", "/hookify:configure", "/hookify:help"):
        cmd = find_custom_command(ws, token)
        assert cmd is not None, token
        assert cmd.name.startswith("hookify")
    assert find_custom_command(ws, "/hookify:nope") is None
