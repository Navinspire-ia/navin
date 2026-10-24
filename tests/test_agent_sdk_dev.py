"""Ports: agent-sdk-dev plugin, model-migration skill, issue-triage scripts."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "navin"
SCRIPTS = PKG / "skills" / "issue-triage" / "scripts"
SCRIPT_NAMES = ["gh_common.py", "comment_duplicates.py", "auto_close_duplicates.py",
                "edit_labels.py", "issue_lifecycle.py"]
NEW_FILES = [
    PKG / "builtin_commands" / "new-sdk-app.md",
    PKG / "builtin_commands" / "migrate-model.md",
    PKG / "builtin_agents" / "agent-sdk-verifier-py.md",
    PKG / "builtin_agents" / "agent-sdk-verifier-ts.md",
    PKG / "builtin_plugins" / "agent-sdk-dev" / "plugin.json",
    PKG / "builtin_plugins" / "agent-sdk-dev" / "README.md",
    PKG / "builtin_plugins" / "model-migration" / "plugin.json",
    PKG / "builtin_plugins" / "model-migration" / "README.md",
    PKG / "skills" / "model-migration" / "SKILL.md",
    PKG / "skills" / "model-migration" / "references" / "parameters.md",
    PKG / "skills" / "model-migration" / "references" / "prompt-snippets.md",
    *(SCRIPTS / n for n in SCRIPT_NAMES),
]
VERIFIERS = ["agent-sdk-verifier-py", "agent-sdk-verifier-ts"]


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    return yaml.safe_load(text.split("---\n", 2)[1])


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name[:-3], SCRIPTS / name)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    spec.loader.exec_module(module)
    return module


def test_files_exist_and_are_clean():
    banned = re.compile(r"claude|anthropic|opus|sonnet|haiku", re.I)
    for path in NEW_FILES:
        assert path.is_file(), path
        text = path.read_text(encoding="utf-8")
        assert not banned.search(text), path
        assert "\u2014" not in text and "\u2013" not in text, path
        if path.suffix == ".json":
            json.loads(text)
        elif path.suffix == ".md" and path.parent.name != "references" and path.name != "README.md" \
                and path.parent.name != "builtin_agents":
            assert _frontmatter(path).get("description"), path


def test_verifier_agents_are_read_only():
    from navin.agent.project_agents import find_project_agent

    for name in VERIFIERS:
        agent = find_project_agent(ROOT, name)
        assert agent is not None and agent.scope == "builtin"
        assert "Use when" in agent.description
        assert 2 <= agent.description.count("<example>") <= 4
        assert {"read_file", "grep"} <= set(agent.tools)
        assert not {"write_file", "edit_file", "apply_patch", "exec", "git", "manage_files"} & set(agent.tools)


def test_cited_sdk_names_exist():
    import navin
    from navin.agent.hook import AgentHook
    from navin.navin import Navin, RunResult, RunStream, StreamEvent
    from navin.sdk import types
    from navin.sdk.clients import MemoryClient, RuntimeClient, SessionClient

    assert navin.Navin is Navin
    for attr in ("from_config", "run", "stream", "run_streamed", "aclose", "__aenter__"):
        assert hasattr(Navin, attr), attr
    for attr in ("stream_events", "wait", "text", "cancel", "aclose"):
        assert hasattr(RunStream, attr), attr
    for attr in ("get", "list", "export", "restore", "clear", "delete", "flush", "ingest"):
        assert hasattr(SessionClient, attr), attr
    for attr in ("read", "write", "append_history", "read_history"):
        assert hasattr(MemoryClient, attr), attr
    for attr in ("model", "workspace", "compact_session"):
        assert hasattr(RuntimeClient, attr), attr
    for attr in ("before_run", "after_run", "on_error", "before_execute_tool", "after_execute_tool", "on_stream"):
        assert hasattr(AgentHook, attr), attr
    fields = RunResult.__dataclass_fields__
    assert {"content", "tools_used", "messages", "usage", "stop_reason", "error", "metadata"} <= set(fields)
    assert {"type", "delta", "content", "name", "arguments", "result", "usage"} <= set(StreamEvent.__dataclass_fields__)
    for event in ("text.delta", "tool.started", "tool.completed", "tool.failed", "run.completed", "run.failed"):
        assert event in types.STREAM_EVENT_TYPES
    params = Navin.from_config.__code__.co_varnames
    assert {"config_path", "workspace", "model", "model_preset"} <= set(params)
    assert {"session_key", "ephemeral", "hooks", "media"} <= set(Navin.run.__code__.co_varnames)


def test_model_migration_skill_validates():
    proc = subprocess.run(
        [sys.executable, str(PKG / "skills/skill-development/scripts/validate_skill.py"),
         str(PKG / "skills/model-migration")], capture_output=True, text=True, check=False)
    assert proc.returncode == 0 and "PASS" in proc.stdout, proc.stdout + proc.stderr
    assert "WARNING" not in proc.stdout, proc.stdout


@pytest.mark.parametrize("plugin", ["agent-sdk-dev", "model-migration"])
def test_plugins_validate(plugin):
    proc = subprocess.run(
        [sys.executable, str(PKG / "skills/plugin-structure/scripts/validate_plugin.py"),
         str(PKG / "builtin_plugins" / plugin)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_scripts_executable_with_help(name):
    path = SCRIPTS / name
    assert os.access(path, os.X_OK), path
    assert path.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3\n")
    proc = subprocess.run([sys.executable, str(path), "--help"], capture_output=True, text=True, check=False)
    assert proc.returncode == 0 and "usage" in proc.stdout.lower(), proc.stderr


class FakeGitHub:
    """In-memory REST layer: routes GET paths to payloads, records writes."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, object]] = []

    def __call__(self, method: str, path: str, body=None):
        self.calls.append((method, path, body))
        if method != "GET":
            raise AssertionError(f"write reached the network in dry run: {method} {path}")
        base = path.split("?")[0]
        if "page=" in path and not path.endswith("page=1"):
            return []
        return self.routes.get(base, self.routes.get(path))


NOW = datetime(2026, 5, 20, tzinfo=timezone.utc)
BOT = {"login": "navin-bot[bot]", "type": "Bot"}
HUMAN = {"login": "alice", "type": "User"}


def test_auto_close_duplicates_dry_run():
    gh = _load("gh_common.py")
    mod = _load("auto_close_duplicates.py")
    notice = "Found 1 possible duplicate issue:\n" + gh.MARKER + " targets=7"
    routes = {
        "/user": {"login": "navin-bot[bot]"},
        "/repos/o/r/issues": [
            {"number": 10, "created_at": "2026-05-01T00:00:00Z", "user": HUMAN},
            {"number": 11, "created_at": "2026-05-01T00:00:00Z", "user": HUMAN},
            {"number": 12, "created_at": "2026-05-01T00:00:00Z", "user": HUMAN},
        ],
        "/repos/o/r/issues/10/comments": [{"id": 1, "body": notice, "user": BOT, "created_at": "2026-05-10T00:00:00Z"}],
        "/repos/o/r/issues/11/comments": [
            {"id": 2, "body": notice, "user": BOT, "created_at": "2026-05-10T00:00:00Z"},
            {"id": 3, "body": "not a dup", "user": HUMAN, "created_at": "2026-05-11T00:00:00Z"},
        ],
        "/repos/o/r/issues/12/comments": [{"id": 4, "body": notice, "user": BOT, "created_at": "2026-05-10T00:00:00Z"}],
        "/repos/o/r/issues/comments/1/reactions": [],
        "/repos/o/r/issues/comments/4/reactions": [{"content": "-1", "user": HUMAN}],
    }
    client = gh.Client("o/r", transport=FakeGitHub(routes))
    assert mod.main(["--repo", "o/r"], client=client, now=NOW) == 0
    patches = [(m, p, b) for m, p, b in client.writes if m == "PATCH"]
    assert patches == [("PATCH", "/repos/o/r/issues/10", {"state": "closed", "state_reason": "duplicate"})]


def test_comment_duplicates_dry_run(capsys):
    gh = _load("gh_common.py")
    mod = _load("comment_duplicates.py")
    routes = {f"/repos/o/r/issues/{n}": {"number": n} for n in (5, 1, 2)}
    routes["/repos/o/r/issues/5/comments"] = []
    client = gh.Client("o/r", transport=FakeGitHub(routes))
    assert mod.main(["5", "--duplicates", "1", "2"], client=client) == 0
    (method, path, body), = client.writes
    assert (method, path) == ("POST", "/repos/o/r/issues/5/comments")
    assert gh.MARKER + " targets=1,2" in body["body"]
    assert "would post" in capsys.readouterr().out


def test_edit_labels_dry_run():
    gh = _load("gh_common.py")
    mod = _load("edit_labels.py")
    routes = {
        "/repos/o/r/issues/3": {"labels": [{"name": "stale"}, {"name": "bug"}]},
        "/repos/o/r/labels": [{"name": "bug"}, {"name": "needs-repro"}, {"name": "stale"}],
    }
    client = gh.Client("o/r", transport=FakeGitHub(routes))
    assert mod.main(["3", "--add", "needs-repro", "bug", "ghost", "--remove", "stale"], client=client) == 0
    assert client.writes == [
        ("POST", "/repos/o/r/issues/3/labels", {"labels": ["needs-repro"]}),
        ("DELETE", "/repos/o/r/issues/3/labels/stale", None),
    ]


def test_lifecycle_sweep_dry_run_with_custom_days():
    gh = _load("gh_common.py")
    mod = _load("issue_lifecycle.py")
    old = {"number": 20, "updated_at": "2026-03-01T00:00:00Z", "labels": [], "user": HUMAN}
    fresh = {"number": 21, "updated_at": "2026-05-19T00:00:00Z", "labels": [], "user": HUMAN}
    info = {"number": 22, "updated_at": "2026-05-01T00:00:00Z", "labels": [{"name": "needs-info"}], "user": HUMAN}
    routes = {
        "/repos/o/r/issues?state=open&sort=updated&direction=asc&per_page=100&page=1": [old, info, fresh],
        "/repos/o/r/issues?state=open&labels=needs-info&per_page=100&page=1": [info],
        "/repos/o/r/issues/22/events": [{"event": "labeled", "label": {"name": "needs-info"},
                                         "created_at": "2026-05-12T00:00:00Z"}],
        "/repos/o/r/issues/22/comments": [],
    }

    class Router(FakeGitHub):
        def __call__(self, method, path, body=None):
            if method == "GET" and path in self.routes:
                self.calls.append((method, path, body))
                return self.routes[path]
            if "labels=" in path:
                return []
            return super().__call__(method, path, body)

    client = gh.Client("o/r", transport=Router(routes))
    assert mod.main(["sweep", "--repo", "o/r", "--stale-days", "30", "--days", "needs-info=5"],
                    client=client, now=NOW) == 0
    labeled = [p for m, p, b in client.writes if m == "POST" and p.endswith("/labels")]
    closed = [p for m, p, b in client.writes if m == "PATCH"]
    assert labeled == ["/repos/o/r/issues/20/labels"]
    assert closed == ["/repos/o/r/issues/22"]


def test_lifecycle_nudge_unknown_label_is_noop():
    gh = _load("gh_common.py")
    mod = _load("issue_lifecycle.py")
    client = gh.Client("o/r", transport=FakeGitHub({}))
    assert mod.main(["nudge", "4", "--label", "bug", "--repo", "o/r"], client=client) == 0
    assert client.writes == []
