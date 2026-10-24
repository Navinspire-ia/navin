"""Security guidance: pattern warnings on edits, opt-in AI review."""

from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

from navin.agent import security_guidance as sg
from navin.agent import user_hooks


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("NAVIN_SECURITY_GUIDANCE", "NAVIN_SECURITY_PATTERNS", "NAVIN_SECURITY_REVIEW"):
        monkeypatch.delenv(name, raising=False)
    sg.STATE.reset()
    workspace = tmp_path / "proj"
    (workspace / ".navin").mkdir(parents=True)
    return workspace


@pytest.mark.parametrize(
    "path, text, rule",
    [
        ("a.py", "data = pickle.loads(blob)", "pickle_deserialization"),
        ("a.py", "yaml.load(f)", "unsafe_yaml_load"),
        ("a.py", "subprocess.run(cmd, shell=True)", "python_subprocess_shell"),
        ("a.js", "el.innerHTML = html", "innerHTML_xss"),
        ("a.py", "requests.get(u, verify=False)", "tls_verification_disabled"),
        (".github/workflows/ci.yml", "on: push", "github_actions_workflow"),
    ],
)
def test_builtin_patterns_match(path, text, rule):
    assert rule in [name for name, _ in sg.check_patterns(path, text)]


def test_safe_code_and_docs_do_not_warn():
    assert sg.check_patterns("a.py", "yaml.safe_load(f)\nsubprocess.run(['ls'])") == []
    assert "eval_injection" not in [n for n, _ in sg.check_patterns("README.md", "never eval(x)")]


def test_edit_warning_once_per_file_and_rule(ws):
    params = {"path": "app.py", "content": "pickle.loads(b)"}
    first = sg.edit_warnings("write_file", params, ws, "s")
    assert "pickle_deserialization" in first.context and first.notice
    assert sg.edit_warnings("write_file", params, ws, "s").context == ""
    assert sg.edit_warnings("write_file", params, ws, "other").context


def test_multi_file_tools_and_kill_switch(ws, monkeypatch):
    params = {"edits": [{"path": "x.js", "action": "replace", "new_text": "document.write(a)"}]}
    assert "document_write_xss" in sg.edit_warnings("apply_patch", params, ws, "s").context
    assert sg.edit_warnings("exec", {"command": "pickle.loads"}, ws, "s").context == ""
    monkeypatch.setenv("NAVIN_SECURITY_PATTERNS", "0")
    assert sg.edit_warnings("write_file", {"path": "b.py", "content": "os.system(c)"}, ws, "s").context == ""


def test_user_patterns_and_redos_guard(ws):
    (ws / ".navin" / "security-patterns.json").write_text(json.dumps({"patterns": [
        {"rule_name": "no-md5", "reminder": "Use sha256.", "regex": r"hashlib\.md5", "paths": ["*.py"]},
        {"rule_name": "slow", "reminder": "x", "regex": "(a+)+"},
    ]}), "utf-8")
    rules = sg.load_user_patterns(ws)
    assert [r.name for r in rules] == ["user:no-md5"]
    assert "user:no-md5" in [n for n, _ in sg.check_patterns("h.py", "hashlib.md5()", rules)]
    assert sg.check_patterns("h.js", "hashlib.md5()", rules) == []


def test_post_tool_use_feedback_carries_reminder(ws):
    notices: list[str] = []

    async def notify(text):
        notices.append(text)

    feedback = asyncio.run(user_hooks.post_tool_use_feedback(
        ws, "s", "write_file", {"path": "a.py", "content": "eval(x)"}, "ok", notify=notify,
    ))
    assert feedback and "eval_injection" in feedback
    assert notices and "Security reminder" in notices[0]


def test_review_is_off_by_default(ws, monkeypatch):
    calls = []
    monkeypatch.setattr(sg, "review_model", lambda p: calls.append(p))
    sg.STATE.touch("s", str(ws / "a.py"))
    assert asyncio.run(sg.stop_review(ws, "s", stop_hook_active=False)) == ("", "")
    assert calls == []


def test_parse_findings_keeps_medium_and_up():
    answer = "```json\n" + json.dumps({"hasVulnerabilities": True, "vulnerabilities": [
        {"filePath": "a.py", "category": "Low", "vulnerableCode": "x", "explanation": "", "fix": "", "severity": "low"},
        {"filePath": "a.py", "category": "SQLi", "vulnerableCode": "q", "explanation": "e", "fix": "f", "severity": "high"},
    ]}) + "\n```"
    found = sg.parse_findings(answer)
    assert [f["category"] for f in found] == ["SQLi"]
    assert sg.parse_findings("not json") == []


def _git(ws, *args):
    subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True)


def test_stop_review_blocks_on_findings(ws, monkeypatch):
    monkeypatch.setenv("NAVIN_SECURITY_REVIEW", "1")
    _git(ws, "init", "-q")
    _git(ws, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
    (ws / "app.py").write_text("cursor.execute('select ' + q)\n", "utf-8")
    prompts = []

    async def fake(prompt):
        prompts.append(prompt)
        return json.dumps({"hasVulnerabilities": True, "vulnerabilities": [{
            "filePath": "app.py", "category": "SQL injection", "vulnerableCode": "execute",
            "explanation": "string concat", "fix": "use params", "severity": "high"}]})

    monkeypatch.setattr(sg, "review_model", fake)
    (ws / ".navin" / "security-guidance.md").write_text("Use the db helper.", "utf-8")
    sg.edit_warnings("write_file", {"path": "app.py", "content": "x"}, ws, "s")
    reason = asyncio.run(user_hooks.stop_hook_reason(ws, "s", "done", stop_hook_active=False))
    assert reason and "SQL injection" in reason
    assert "app.py" in prompts[0] and "Use the db helper." in prompts[0]
    # The same finding is not raised twice in a session.
    assert asyncio.run(sg.stop_review(ws, "s", stop_hook_active=False)) == ("", "")


def test_commit_detection():
    assert sg.is_commit_command("git add . && git commit -m x")
    assert sg.is_commit_command("git -C repo commit -m x")
    assert not sg.is_commit_command("git commit --dry-run")
    assert not sg.is_commit_command("git log")


def test_summary_line(ws, monkeypatch):
    # Defaults alone add nothing to the system prompt.
    assert user_hooks.hooks_summary(ws) == ""
    (ws / ".navin" / "security-patterns.json").write_text(json.dumps({"patterns": [
        {"rule_name": "no-md5", "reminder": "Use sha256.", "substrings": ["md5("]},
    ]}), "utf-8")
    assert "25 rules + 1 project" in user_hooks.hooks_summary(ws)
    monkeypatch.setenv("NAVIN_SECURITY_REVIEW", "1")
    assert "AI review" in sg.summary(ws)
    monkeypatch.setenv("NAVIN_SECURITY_GUIDANCE", "0")
    assert sg.summary(ws) == ""


def test_call_model_uses_configured_provider(monkeypatch):
    from navin.config import loader
    from navin.providers import factory

    seen = {}

    class Provider:
        async def chat(self, messages, **kwargs):
            seen.update(kwargs, prompt=messages[0]["content"])
            return type("R", (), {"content": "{}", "finish_reason": "stop"})()

    monkeypatch.setattr(loader, "load_config_cached", lambda: object())
    monkeypatch.setattr(factory, "make_provider", lambda config: Provider())
    monkeypatch.setenv("NAVIN_SECURITY_REVIEW_MODEL", "small-model")
    assert asyncio.run(sg.call_model("review this")) == "{}"
    assert seen["model"] == "small-model" and seen["prompt"] == "review this"

    def broken(config):
        raise RuntimeError("no provider")

    monkeypatch.setattr(factory, "make_provider", broken)
    assert asyncio.run(sg.call_model("x")) is None


def test_commit_review_after_git_commit(ws, monkeypatch):
    monkeypatch.setenv("NAVIN_SECURITY_REVIEW", "1")
    _git(ws, "init", "-q")
    (ws / "run.py").write_text("os.system(user)\n", "utf-8")
    _git(ws, "add", "run.py")
    _git(ws, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "add runner")
    prompts = []

    async def fake(prompt):
        prompts.append(prompt)
        return json.dumps({"hasVulnerabilities": True, "vulnerabilities": [{
            "filePath": "run.py", "category": "Command injection", "vulnerableCode": "os.system",
            "explanation": "shell", "fix": "argv list", "severity": "critical"}]})

    monkeypatch.setattr(sg, "review_model", fake)
    feedback = asyncio.run(user_hooks.post_tool_use_feedback(
        ws, "s", "exec", {"command": "git commit -m 'add runner'"}, "[main 1a2b] add runner",
    ))
    assert feedback and "Command injection" in feedback and "[CRITICAL]" in feedback
    assert "+os.system(user)" in prompts[0]
    # A failed commit or another command triggers no review.
    prompts.clear()
    asyncio.run(sg.commit_review("exec", {"command": "git commit"}, "nothing to commit", ws, "s2"))
    asyncio.run(sg.commit_review("exec", {"command": "git status"}, "ok", ws, "s2"))
    assert prompts == []
