"""Prompt hooks: hooks whose decision comes from the model."""

from __future__ import annotations

import asyncio
import json

import pytest

from navin.agent import prompt_hooks as ph


@pytest.fixture(autouse=True)
def _clean_cache():
    ph.clear_cache()
    yield
    ph.clear_cache()


def test_render_fills_event_fields():
    payload = {
        "tool_name": "exec",
        "tool_input": {"command": "rm -rf /tmp/x", "timeout": 5},
        "prompt": "clean up",
        "cwd": "/w",
        "transcript": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "done"}],
    }
    text = ph.render(
        "Cmd: $TOOL_INPUT.command in $CWD by $TOOL_NAME. All: $TOOL_INPUT. Ask: $USER_PROMPT. Log: $TRANSCRIPT",
        "PreToolUse", payload,
    )
    assert "Cmd: rm -rf /tmp/x in /w by exec." in text
    assert '"timeout": 5' in text and "Ask: clean up." in text
    assert "user: hi" in text and "assistant: done" in text
    assert "$" not in text


def test_parse_answer_variants():
    assert ph.parse_answer('{"decision": "approve"}')["decision"] == "approve"
    fenced = 'Sure:\n```json\n{"decision": "block", "reason": "no tests", "systemMessage": "x"}\n```'
    assert ph.parse_answer(fenced) == {"decision": "block", "reason": "no tests", "systemMessage": "x"}
    assert ph.parse_answer("deny: touches /etc")["reason"] == "touches /etc"
    assert ph.parse_answer('{"ok": false, "reason": "r"}')["decision"] == "block"
    assert ph.parse_answer("I am not sure") is None
    assert ph.parse_answer("") is None


@pytest.mark.parametrize("event", ["PreToolUse", "PermissionRequest"])
def test_block_and_ask_on_tool_events_deny_the_tool(event):
    code, out, _ = ph.to_hook_result(event, {"decision": "deny", "reason": "system path", "systemMessage": ""})
    data = json.loads(out)
    assert code == 0 and data["permissionDecision"] == "deny" and data["reason"] == "system path"
    _, out, _ = ph.to_hook_result(event, {"decision": "ask", "reason": "drops a table", "systemMessage": ""})
    assert "ask_user" in json.loads(out)["permissionDecisionReason"]


def test_approve_is_silent_and_stop_block_keeps_reason():
    assert ph.to_hook_result("Stop", {"decision": "approve", "reason": "", "systemMessage": ""}) == (0, "", "")
    _, out, _ = ph.to_hook_result("Stop", {"decision": "block", "reason": "tests not run", "systemMessage": ""})
    assert json.loads(out) == {"decision": "block", "reason": "tests not run"}


def test_evaluate_calls_model_once_per_identical_event(monkeypatch):
    prompts = []

    async def fake(prompt, model, timeout):
        prompts.append((prompt, model, timeout))
        return '{"decision": "block", "reason": "no"}'

    monkeypatch.setattr(ph, "call_model", fake)
    payload = {"tool_name": "write_file", "tool_input": {"path": "/etc/passwd"}, "session_id": "a"}
    first = asyncio.run(ph.evaluate("Path $TOOL_INPUT.path", "PreToolUse", payload, timeout=5, model="m"))
    again = asyncio.run(ph.evaluate("Path $TOOL_INPUT.path", "PreToolUse", {**payload, "session_id": "b"}, timeout=5, model="m"))
    assert first == again and json.loads(first[1])["permissionDecision"] == "deny"
    assert len(prompts) == 1
    prompt, model, timeout = prompts[0]
    assert "Path /etc/passwd" in prompt and model == "m" and timeout == 5


def test_evaluate_fails_open_with_a_notice(monkeypatch):
    async def broken(prompt, model, timeout):
        return None

    monkeypatch.setattr(ph, "call_model", broken)
    code, out, _ = asyncio.run(ph.evaluate("rule", "Stop", {}, timeout=1))
    assert code == 0 and "allowed" in json.loads(out)["systemMessage"]
    assert "decision" not in json.loads(out)


def test_evaluate_ignores_events_without_decision():
    assert asyncio.run(ph.evaluate("rule", "PostToolUse", {}, timeout=1)) == (0, "", "")


def test_call_model_times_out(monkeypatch):
    from navin.config import loader
    from navin.providers import factory

    class Slow:
        async def chat(self, messages, **kwargs):
            await asyncio.sleep(1)

    monkeypatch.setattr(loader, "load_config_cached", lambda: object())
    monkeypatch.setattr(factory, "make_provider", lambda config: Slow())
    assert asyncio.run(ph.call_model("x", None, 0.05)) is None


def test_prompt_hook_type_is_parsed_and_dispatched(tmp_path):
    from navin.agent import user_hooks as uh

    data = {"Stop": [{"hooks": [{"type": "prompt", "prompt": "Is it done? $ARGUMENTS", "model": "m1"}]}]}
    hooks = uh._parse(data, "Stop", plugin_root="", source="t")
    assert [(h.kind, h.model) for h in hooks] == [("prompt", "m1")]
    assert "PreCompact" in uh.EVENTS and "SessionEnd" in uh.EVENTS
