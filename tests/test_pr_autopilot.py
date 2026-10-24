# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""PR autopilot: forge thread / log / sync actions, /autopilot, skill routing.

Every network call is mocked: the suite must pass offline and must never
touch a real forge.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from navin.agent.skill_routing import action_skill_names, skill_route_for_turn
from navin.agent.skills import BUILTIN_SKILLS_DIR, SkillsLoader
from navin.bus.events import InboundMessage
from navin.command.autopilot import (
    AUTOPILOT_USAGE,
    autopilot_args_start_turn,
    autopilot_job_name,
    parse_autopilot_args,
)
from navin.command.builtin import (
    BUILTIN_COMMAND_SPECS,
    is_agent_turn_command,
    register_builtin_commands,
)
from navin.command.router import CommandContext, CommandRouter
from navin.cron.service import CronService
from navin.webui import forge_api
from navin.webui.forge_api import FORGEJO, GITHUB, GITLAB, ForgeError, ForgeRemote


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: Any = None,
        text: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else ("" if payload is None else json.dumps(payload))
        self.headers = headers or {}

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class Recorder:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected extra request: {method} {url}")
        return self.responses.pop(0)


def _remote(kind: str) -> ForgeRemote:
    host = {GITHUB: "github.com", GITLAB: "gitlab.com", FORGEJO: "forgejo.example.org"}[kind]
    return ForgeRemote(kind=kind, host=host, owner="org", repo="app")


def _run(responses: list[FakeResponse], fn, *args, **kwargs):
    recorder = Recorder(responses)
    with patch.object(forge_api.httpx, "request", recorder):
        result = fn(*args, **kwargs)
    assert not recorder.responses, "scripted responses left unused"
    return result, recorder.calls


# ---------------------------------------------------------------------------
# pr_threads
# ---------------------------------------------------------------------------


def _gh_thread(tid: str, resolved: bool, outdated: bool = False, comments: int = 1) -> dict[str, Any]:
    return {
        "id": tid,
        "isResolved": resolved,
        "isOutdated": outdated,
        "path": "src/app.py",
        "line": 12,
        "originalLine": 10,
        "comments": {
            "totalCount": comments,
            "nodes": [
                {
                    "databaseId": 100 + i,
                    "author": {"login": f"user{i}"},
                    "body": f"comment   {i}\n" + ("x" * 900 if i == 0 else "short"),
                    "url": f"https://github.com/org/app/pull/7#discussion_r{100 + i}",
                }
                for i in range(comments)
            ],
        },
    }


def test_github_threads_unresolved_by_default_and_paginated():
    page1 = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": True, "endCursor": "C1"},
                        "nodes": [_gh_thread("PRRT_a", False, comments=2), _gh_thread("PRRT_b", True)],
                    }
                }
            }
        }
    }
    page2 = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [_gh_thread("PRRT_c", False, outdated=True)],
                    }
                }
            }
        }
    }
    result, calls = _run(
        [FakeResponse(200, page1), FakeResponse(200, page2)],
        forge_api.list_review_threads,
        _remote(GITHUB),
        "tok",
        7,
    )
    assert calls[0]["url"] == "https://api.github.com/graphql"
    assert calls[0]["json"]["variables"] == {"owner": "org", "name": "app", "number": 7, "after": None}
    assert calls[1]["json"]["variables"]["after"] == "C1"
    assert result["total"] == 3 and result["unresolved"] == 2 and result["outdated_unresolved"] == 1
    ids = [row["id"] for row in result["threads"]]
    assert ids == ["PRRT_a", "PRRT_c"]
    first = result["threads"][0]
    assert first["path"] == "src/app.py" and first["line"] == 12
    assert first["author"] == "user0" and first["replies"] == 1
    assert first["comment_id"] == 100 and first["last_author"] == "user1"
    assert len(first["body"]) <= 600 and "\n" not in first["body"]
    assert first["url"].endswith("discussion_r100")


def test_github_threads_include_resolved_and_graphql_error():
    page = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [_gh_thread("PRRT_b", True)],
                    }
                }
            }
        }
    }
    result, _ = _run(
        [FakeResponse(200, page)],
        forge_api.list_review_threads,
        _remote(GITHUB),
        "tok",
        7,
        include_resolved=True,
    )
    assert [row["resolved"] for row in result["threads"]] == [True]
    error = {"errors": [{"type": "FORBIDDEN", "message": "Resource not accessible by integration"}]}
    with pytest.raises(ForgeError) as caught:
        _run([FakeResponse(200, error)], forge_api.list_review_threads, _remote(GITHUB), "tok", 7)
    assert caught.value.code == "forge_token_forbidden"


def test_github_enterprise_graphql_url():
    remote = ForgeRemote(kind=GITHUB, host="ghe.corp.example", owner="org", repo="app")
    assert forge_api._github_graphql_url(remote) == "https://ghe.corp.example/api/graphql"


def test_gitlab_threads_only_resolvable_discussions():
    discussions = [
        {
            "id": "d1",
            "notes": [
                {
                    "id": 11,
                    "body": "please rename",
                    "author": {"username": "rev"},
                    "resolvable": True,
                    "resolved": False,
                    "position": {"new_path": "a.py", "new_line": 4},
                },
                {"id": 12, "body": "done?", "author": {"username": "dev"}, "resolvable": True, "resolved": False},
            ],
        },
        {"id": "d2", "notes": [{"id": 21, "body": "lgtm", "resolvable": False}]},
        {"id": "d3", "notes": [{"id": 31, "body": "x", "resolvable": True, "resolved": True}]},
        {"id": "d4", "notes": [{"id": 41, "body": "changed the line", "system": True, "resolvable": True}]},
    ]
    result, calls = _run(
        [FakeResponse(200, discussions)], forge_api.list_review_threads, _remote(GITLAB), "tok", 3
    )
    assert calls[0]["url"].endswith("/projects/org%2Fapp/merge_requests/3/discussions")
    assert result["total"] == 2 and result["unresolved"] == 1
    (thread,) = result["threads"]
    assert thread["id"] == "d1" and thread["path"] == "a.py" and thread["line"] == 4
    assert thread["outdated"] is None and thread["replies"] == 1
    assert thread["url"] == "https://gitlab.com/org/app/-/merge_requests/3#note_11"


def test_forgejo_threads_best_effort():
    reviews = [{"id": 5, "comments_count": 2}, {"id": 6, "comments_count": 0}]
    comments = [
        {"id": 50, "body": "nit", "path": "b.go", "position": 9, "user": {"login": "r"}, "resolver": None,
         "html_url": "https://forgejo.example.org/org/app/pulls/2#issuecomment-50"},
        {"id": 51, "body": "ok", "path": "b.go", "position": 3, "resolver": {"login": "r"}},
    ]
    result, calls = _run(
        [FakeResponse(200, reviews), FakeResponse(200, comments)],
        forge_api.list_review_threads,
        _remote(FORGEJO),
        "tok",
        2,
    )
    assert calls[1]["url"].endswith("/repos/org/app/pulls/2/reviews/5/comments")
    assert result["unresolved"] == 1
    assert result["threads"][0]["id"] == "50" and result["threads"][0]["line"] == 9


# ---------------------------------------------------------------------------
# pr_reply / pr_resolve_thread
# ---------------------------------------------------------------------------


def test_github_reply_by_thread_id_and_by_comment_number():
    reply = {"data": {"addPullRequestReviewThreadReply": {"comment": {"url": "https://u/1"}}}}
    result, calls = _run(
        [FakeResponse(200, reply)], forge_api.reply_review_thread, _remote(GITHUB), "tok", 7, "PRRT_a", "Fixed in abc"
    )
    assert result == {"ok": True, "thread_id": "PRRT_a", "url": "https://u/1"}
    assert calls[0]["json"]["variables"] == {"id": "PRRT_a", "body": "Fixed in abc"}
    result, calls = _run(
        [FakeResponse(201, {"html_url": "https://u/2"})],
        forge_api.reply_review_thread,
        _remote(GITHUB),
        "tok",
        7,
        "100",
        "Fixed",
    )
    assert calls[0]["url"] == "https://api.github.com/repos/org/app/pulls/7/comments/100/replies"
    assert result["url"] == "https://u/2"


def test_reply_refuses_empty_body():
    with pytest.raises(ForgeError):
        forge_api.reply_review_thread(_remote(GITHUB), "tok", 7, "PRRT_a", "   ")


def test_gitlab_reply_and_resolve():
    result, calls = _run(
        [FakeResponse(201, {"id": 99})], forge_api.reply_review_thread, _remote(GITLAB), "tok", 3, "d1", "Done"
    )
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/merge_requests/3/discussions/d1/notes")
    assert result["url"].endswith("#note_99")
    result, calls = _run(
        [FakeResponse(200, {"id": "d1"})], forge_api.resolve_review_thread, _remote(GITLAB), "tok", 3, "d1"
    )
    assert calls[0]["method"] == "PUT" and calls[0]["json"] == {"resolved": True}
    assert result["resolved"] is True


def test_github_resolve_and_unresolve():
    payload = {"data": {"resolveReviewThread": {"thread": {"id": "PRRT_a", "isResolved": True}}}}
    result, calls = _run(
        [FakeResponse(200, payload)], forge_api.resolve_review_thread, _remote(GITHUB), "tok", 7, "PRRT_a"
    )
    assert "resolveReviewThread" in calls[0]["json"]["query"] and result["resolved"] is True
    payload = {"data": {"unresolveReviewThread": {"thread": {"id": "PRRT_a", "isResolved": False}}}}
    result, calls = _run(
        [FakeResponse(200, payload)],
        forge_api.resolve_review_thread,
        _remote(GITHUB),
        "tok",
        7,
        "PRRT_a",
        resolved=False,
    )
    assert "unresolveReviewThread" in calls[0]["json"]["query"] and result["resolved"] is False
    with pytest.raises(ValueError):
        forge_api.resolve_review_thread(_remote(GITHUB), "tok", 7, "123")


def test_forgejo_reply_falls_back_to_comment_and_resolve_is_unsupported():
    result, calls = _run(
        [FakeResponse(201, {"html_url": "https://f/c/1"})],
        forge_api.reply_review_thread,
        _remote(FORGEJO),
        "tok",
        2,
        "50",
        "Fixed",
    )
    assert calls[0]["url"].endswith("/repos/org/app/issues/2/comments")
    assert calls[0]["json"]["body"].startswith("> Re: review comment #50")
    assert result["mode"] == "pr_comment"
    with pytest.raises(ForgeError) as caught:
        forge_api.resolve_review_thread(_remote(FORGEJO), "tok", 2, "50")
    assert caught.value.code == "forge_unsupported"


# ---------------------------------------------------------------------------
# ci_logs
# ---------------------------------------------------------------------------


def test_github_ci_logs_follow_redirect_without_token_and_redact():
    runs = {
        "check_runs": [
            {"id": 1, "name": "lint", "conclusion": "success", "app": {"slug": "github-actions"}},
            {"id": 2, "name": "tests", "conclusion": "failure", "app": {"slug": "github-actions"},
             "html_url": "https://github.com/org/app/actions/runs/9/job/2"},
            {"id": 3, "name": "external", "conclusion": "failure", "app": {"slug": "circleci"},
             "output": {"title": "Build failed", "summary": "step 3"}},
        ]
    }
    log = "\x1b[31mERROR\x1b[0m test_x failed\nGITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz123456\nusing tok-secret-value\n"
    result, calls = _run(
        [
            FakeResponse(200, runs),
            FakeResponse(302, None, text="", headers={"location": "https://blob.example/log?sig=1"}),
            FakeResponse(200, None, text=log),
        ],
        forge_api.ci_failed_logs,
        _remote(GITHUB),
        "tok-secret-value",
        ref="feature/x",
        limit=5,
    )
    assert calls[0]["url"].endswith("/commits/feature%2Fx/check-runs")
    assert calls[1]["url"].endswith("/actions/jobs/2/logs")
    assert calls[1]["follow_redirects"] is False
    assert calls[2]["url"] == "https://blob.example/log?sig=1"
    assert "Authorization" not in calls[2]["headers"]
    assert result["failing"] == 2
    tests_job, external = result["jobs"]
    assert tests_job["source"] == "job_log"
    assert "test_x failed" in tests_job["log"] and "\x1b" not in tests_job["log"]
    assert "ghp_" not in tests_job["log"] and "tok-secret-value" not in tests_job["log"]
    assert external["source"] == "check_output" and "Build failed" in external["log"]


def test_github_ci_logs_check_filter_and_expired_log():
    runs = {
        "check_runs": [
            {"id": 2, "name": "tests (3.12)", "conclusion": "failure", "app": {"slug": "github-actions"}},
            {"id": 4, "name": "build", "conclusion": "failure", "app": {"slug": "github-actions"}},
        ]
    }
    result, _ = _run(
        [FakeResponse(200, runs), FakeResponse(410, None, text="gone")],
        forge_api.ci_failed_logs,
        _remote(GITHUB),
        "tok",
        ref="main",
        check="TESTS",
    )
    assert [job["name"] for job in result["jobs"]] == ["tests (3.12)"]
    assert result["jobs"][0]["source"] == "none"


def test_gitlab_ci_logs_trace_tail_is_bounded():
    trace = "\n".join(f"line {i}" for i in range(5000)) + "\nFAILED test_y\n"
    result, calls = _run(
        [
            FakeResponse(200, [{"id": 77, "status": "failed"}]),
            FakeResponse(200, [{"id": 8, "name": "pytest", "status": "failed", "web_url": "https://gl/j/8"},
                               {"id": 9, "name": "lint", "status": "success"}]),
            FakeResponse(200, None, text=trace),
        ],
        forge_api.ci_failed_logs,
        _remote(GITLAB),
        "tok",
        ref="feature",
        max_chars=1000,
    )
    assert calls[1]["params"]["scope[]"] == "failed"
    assert calls[2]["url"].endswith("/jobs/8/trace")
    (job,) = result["jobs"]
    assert job["truncated"] is True and len(job["log"]) <= 1000
    assert job["log"].rstrip().endswith("FAILED test_y")


def test_forgejo_ci_logs_lists_failures_with_note():
    statuses = [
        {"context": "ci/test", "status": "failure", "target_url": "https://f/a/1"},
        {"context": "ci/lint", "status": "success"},
    ]
    result, _ = _run([FakeResponse(200, statuses)], forge_api.ci_failed_logs, _remote(FORGEJO), "tok", ref="x")
    assert [job["name"] for job in result["jobs"]] == ["ci/test"]
    assert "no job log API" in result["note"]


def test_log_tail_never_keeps_a_half_cut_line():
    text = "A" * 3000 + "\nkeep me\n"
    tail, truncated = forge_api.log_tail(text, max_chars=600)
    assert truncated and tail == "keep me\n"


# ---------------------------------------------------------------------------
# pr_sync_base
# ---------------------------------------------------------------------------


def test_github_sync_state_behind_and_conflicts():
    pr = {"number": 7, "state": "open", "head": {"ref": "feat", "sha": "abc"}, "base": {"ref": "main"},
          "mergeable": False, "mergeable_state": "dirty", "html_url": "https://github.com/org/app/pull/7"}
    result, calls = _run(
        [FakeResponse(200, pr), FakeResponse(200, {"ahead_by": 3, "behind_by": 5})],
        forge_api.request_sync_state,
        _remote(GITHUB),
        "tok",
        7,
    )
    assert calls[1]["url"].endswith("/compare/main...abc")
    assert result["ahead_by"] == 3 and result["behind_by"] == 5
    assert result["has_conflicts"] is True and result["mergeable"] is False
    assert result["needs_base_merge"] is True


def test_github_sync_state_still_computing():
    pr = {"number": 7, "head": {"ref": "feat", "sha": "abc"}, "base": {"ref": "main"}, "mergeable": None}
    result, _ = _run(
        [FakeResponse(200, pr), FakeResponse(200, {"ahead_by": 1, "behind_by": 0})],
        forge_api.request_sync_state,
        _remote(GITHUB),
        "tok",
        7,
    )
    assert result["mergeable"] is None and result["needs_base_merge"] is False


def test_gitlab_sync_state():
    mr = {"iid": 3, "source_branch": "feat", "target_branch": "main", "sha": "s1",
          "detailed_merge_status": "mergeable", "has_conflicts": False, "diverged_commits_count": 2}
    result, calls = _run(
        [FakeResponse(200, mr), FakeResponse(200, {"commits": [{}, {}, {}, {}]})],
        forge_api.request_sync_state,
        _remote(GITLAB),
        "tok",
        3,
    )
    assert calls[0]["params"] == {"include_diverged_commits_count": "true"}
    assert calls[1]["params"]["from"] == "main" and calls[1]["params"]["to"] == "feat"
    assert result["behind_by"] == 2 and result["ahead_by"] == 4
    assert result["mergeable"] is True and result["needs_base_merge"] is True


def test_forgejo_sync_state_compare_failure_is_unknown():
    pr = {"number": 2, "head": {"ref": "feat", "sha": "s"}, "base": {"ref": "main"}, "mergeable": True}
    result, _ = _run(
        [FakeResponse(200, pr), FakeResponse(500, {"message": "boom"})],
        forge_api.request_sync_state,
        _remote(FORGEJO),
        "tok",
        2,
    )
    assert result["mergeable"] is True and result["ahead_by"] is None and result["behind_by"] is None


# ---------------------------------------------------------------------------
# Forge tool wiring
# ---------------------------------------------------------------------------


def _forge_tool(tmp_path: Path, monkeypatch, kind: str = GITHUB):
    from navin.agent.tools import forge as forge_tool

    remote = _remote(kind)
    monkeypatch.setattr(forge_tool.ForgeTool, "_resolve", lambda self, root: (remote, "tok"))
    tool = object.__new__(forge_tool.ForgeTool)
    tool._root_or_error = lambda: (tmp_path, "")
    return tool


def _allow(monkeypatch, allowed: bool = True) -> list[Any]:
    from navin.agent import approval

    asked: list[Any] = []

    async def fake(request):
        asked.append(request)
        return SimpleNamespace(allowed=allowed, reason="denied by user")

    monkeypatch.setattr(approval, "request_approval", fake)
    return asked


@pytest.mark.parametrize(
    "action,kwargs,target,ret",
    [
        ("pr_threads", {"number": 7}, "list_review_threads", {"threads": [], "unresolved": 0}),
        ("pr_sync_base", {"number": 7}, "request_sync_state", {"behind_by": 0, "needs_base_merge": False}),
        ("ci_logs", {"branch": "feat", "check": "tests"}, "ci_failed_logs", {"jobs": [], "failing": 0}),
    ],
)
def test_forge_tool_autopilot_reads_need_no_approval(tmp_path, monkeypatch, action, kwargs, target, ret):
    asked = _allow(monkeypatch)
    tool = _forge_tool(tmp_path, monkeypatch)
    with patch.object(forge_api, target, return_value=ret) as fn:
        out = json.loads(asyncio.run(tool.execute(action=action, **kwargs)))
    assert fn.called and not asked
    assert tool.call_read_only({"action": action})
    assert out["forge"] == GITHUB
    if action == "ci_logs":
        assert fn.call_args.kwargs["ref"] == "feat" and fn.call_args.kwargs["check"] == "tests"
        assert fn.call_args.kwargs["limit"] == 2
    if action == "pr_threads":
        assert fn.call_args.kwargs["include_resolved"] is False


def test_forge_tool_sync_base_suggests_git_merge(tmp_path, monkeypatch):
    _allow(monkeypatch)
    tool = _forge_tool(tmp_path, monkeypatch)
    with patch.object(forge_api, "request_sync_state", return_value={"base": "main", "needs_base_merge": True}):
        out = json.loads(asyncio.run(tool.execute(action="pr_sync_base", number=7)))
    assert "git action=merge name=origin/main" in out["sync"]["next"]
    assert "never force" in out["sync"]["next"]


def test_forge_tool_reply_and_resolve_ask_with_unattended_policy(tmp_path, monkeypatch):
    asked = _allow(monkeypatch)
    tool = _forge_tool(tmp_path, monkeypatch)
    with patch.object(forge_api, "reply_review_thread", return_value={"ok": True}) as reply, \
            patch.object(forge_api, "resolve_review_thread", return_value={"ok": True}) as resolve:
        asyncio.run(tool.execute(action="pr_reply", number=7, thread_id="PRRT_a", body="Fixed in abc"))
        asyncio.run(tool.execute(action="pr_resolve_thread", number=7, thread_id="PRRT_a"))
    assert reply.call_args.args[2:] == (7, "PRRT_a", "Fixed in abc")
    assert resolve.call_args.kwargs["resolved"] is True
    assert [req.allow_when_unattended for req in asked] == [True, False]
    assert [req.scope for req in asked] == ["forge:pr_reply", "forge:pr_resolve_thread"]
    assert not tool.call_read_only({"action": "pr_reply"})


def test_forge_tool_validates_thread_arguments(tmp_path, monkeypatch):
    asked = _allow(monkeypatch)
    tool = _forge_tool(tmp_path, monkeypatch)
    assert "needs number" in str(asyncio.run(tool.execute(action="pr_threads")))
    assert "thread_id" in str(asyncio.run(tool.execute(action="pr_reply", number=7, body="x")))
    assert "body" in str(asyncio.run(tool.execute(action="pr_reply", number=7, thread_id="t")))
    assert "thread_id" in str(asyncio.run(tool.execute(action="pr_resolve_thread", number=7)))
    assert not asked
    _allow(monkeypatch, allowed=False)
    with patch.object(forge_api, "resolve_review_thread") as resolve:
        out = asyncio.run(tool.execute(action="pr_resolve_thread", number=7, thread_id="t"))
    assert "refused" in str(out) and not resolve.called


def test_forge_tool_surfaces_unsupported_resolve(tmp_path, monkeypatch):
    _allow(monkeypatch)
    tool = _forge_tool(tmp_path, monkeypatch, kind=FORGEJO)
    out = asyncio.run(tool.execute(action="pr_resolve_thread", number=2, thread_id="50"))
    assert "Forgejo has no API to resolve" in str(out)


# ---------------------------------------------------------------------------
# /autopilot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,number,url,stop",
    [
        ("42", 42, "", False),
        ("#42", 42, "", False),
        ("!7", 7, "", False),
        ("42 stop", 42, "", True),
        ("stop 42", 42, "", True),
        ("https://github.com/org/app/pull/42", 42, "https://github.com/org/app/pull/42", False),
        ("https://gitlab.com/g/sub/app/-/merge_requests/9 stop", 9, "https://gitlab.com/g/sub/app/-/merge_requests/9", True),
    ],
)
def test_parse_autopilot_args(text, number, url, stop):
    request = parse_autopilot_args(text)
    assert request is not None
    assert (request.number, request.url, request.stop) == (number, url, stop)
    assert request.job_name == f"autopilot-pr-{number}"


@pytest.mark.parametrize(
    "text", ["", "stop", "abc", "42 43", "https://github.com/org/app/issues/4", "0"]
)
def test_parse_autopilot_args_rejects(text):
    assert parse_autopilot_args(text) is None


def test_autopilot_spec_and_lifecycle():
    assert any(spec.command == "/autopilot" for spec in BUILTIN_COMMAND_SPECS)
    assert is_agent_turn_command("/autopilot 42")
    assert not is_agent_turn_command("/autopilot")
    assert autopilot_args_start_turn("42") and not autopilot_args_start_turn("42 stop")
    assert autopilot_job_name(42) == "autopilot-pr-42"


def _harness(tmp_path):
    cron = CronService(tmp_path / "cron" / "jobs.json")
    loop = SimpleNamespace(cron_service=cron, context=SimpleNamespace(timezone="UTC"))
    router = CommandRouter()
    register_builtin_commands(router)
    return cron, loop, router


async def _send(router, loop, text, *, session=True, chat_id="c1"):
    msg = InboundMessage(channel="websocket", sender_id="u", chat_id=chat_id, content=text)
    ctx = CommandContext(
        msg=msg,
        session=object() if session else None,
        key=msg.session_key,
        raw=text,
        loop=loop,
        is_user_turn=True,
    )
    return await router.dispatch(ctx), msg


@pytest.mark.asyncio
async def test_autopilot_usage(tmp_path):
    _cron, loop, router = _harness(tmp_path)
    reply, _ = await _send(router, loop, "/autopilot")
    assert reply is not None and reply.content == AUTOPILOT_USAGE


@pytest.mark.asyncio
async def test_autopilot_start_arms_named_one_shot_job_and_runs_now(tmp_path):
    from navin.command.modules import PRELOAD_SKILLS_METADATA_KEY

    cron, loop, router = _harness(tmp_path)
    reply, msg = await _send(router, loop, "/autopilot 42")
    assert reply is None  # the first pass runs as the normal agent turn
    assert "PR #42" in msg.content and "pr-autopilot" in msg.content
    assert msg.metadata["original_command"] == "/autopilot"
    assert msg.metadata["original_content"] == "/autopilot 42"
    assert msg.metadata[PRELOAD_SKILLS_METADATA_KEY] == ["pr-autopilot", "cron"]

    (job,) = cron.list_jobs()
    assert job.name == "autopilot-pr-42"
    assert job.schedule.kind == "at" and job.delete_after_run is True
    assert job.payload.session_key == "websocket:c1"
    assert 'name="autopilot-pr-42"' in job.payload.message
    assert "never merge" in job.payload.message and "untrusted" in job.payload.message


@pytest.mark.asyncio
async def test_autopilot_restart_replaces_and_stop_removes(tmp_path):
    cron, loop, router = _harness(tmp_path)
    await _send(router, loop, "/autopilot 42")
    await _send(router, loop, "/autopilot 8", chat_id="c2")
    first = cron.list_jobs()
    _reply, msg = await _send(router, loop, "/autopilot https://github.com/org/app/pull/42")
    assert "Restart" in msg.content
    names = sorted(j.name for j in cron.list_jobs())
    assert names == ["autopilot-pr-42", "autopilot-pr-8"]
    assert {j.id for j in cron.list_jobs()} != {j.id for j in first}

    reply, _ = await _send(router, loop, "/autopilot 42 stop", session=False)
    assert "Autopilot stopped for PR #42" in reply.content
    assert [j.name for j in cron.list_jobs()] == ["autopilot-pr-8"]  # other chat untouched

    reply, _ = await _send(router, loop, "/autopilot 42 stop")
    assert "No autopilot is scheduled" in reply.content


@pytest.mark.asyncio
async def test_autopilot_start_refused_while_a_turn_runs(tmp_path):
    cron, loop, router = _harness(tmp_path)
    reply, _ = await _send(router, loop, "/autopilot 42", session=False)
    assert "already running" in reply.content
    assert cron.list_jobs() == []


# ---------------------------------------------------------------------------
# Skill routing and the bundled playbook
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "keep PR 42 merge-ready",
        "autopilot sur la PR 42",
        "Lance l'autopilote sur la pull request 12",
        "/autopilot 42",
        "babysit MR !7 until it is green",
        "garde la PR 42 prête à merger",
    ],
)
def test_pr_autopilot_intent_routes_to_skill(message):
    route = skill_route_for_turn({}, message)
    assert route == ("code", "pr-autopilot")
    assert action_skill_names(*route) == ("pr-autopilot",)


def test_pr_autopilot_routes_in_code_module_but_not_unrelated_text():
    assert skill_route_for_turn({"product_module": "code"}, "keep PR 42 merge-ready") == ("code", "pr-autopilot")
    assert skill_route_for_turn({}, "review PR 42") is None
    assert skill_route_for_turn({}, "fix the autopilot feature in the car app") is None
    assert skill_route_for_turn({"product_module": "code"}, "fix the login bug") is None


def test_desk_routes_are_unchanged():
    assert skill_route_for_turn({"product_module": "montage"}, "translate my demo video") == ("montage", "localize")


def test_pr_autopilot_skill_is_bundled_with_valid_frontmatter():
    loader = SkillsLoader(Path(BUILTIN_SKILLS_DIR).parent, builtin_skills_dir=Path(BUILTIN_SKILLS_DIR))
    names = {entry["name"] for entry in loader.list_skills(filter_unavailable=False)}
    assert "pr-autopilot" in names
    meta = loader.get_skill_metadata("pr-autopilot") or {}
    assert meta.get("name") == "pr-autopilot"
    assert "merge-ready" in str(meta.get("description"))
    available, _reason = loader.get_skill_availability("pr-autopilot")
    assert available
    body = (Path(BUILTIN_SKILLS_DIR) / "pr-autopilot" / "SKILL.md").read_text(encoding="utf-8")
    for needle in ("pr_threads", "ci_logs", "pr_sync_base", "Never force-push", "untrusted", "autopilot-pr-<N>"):
        assert needle in body
    assert "—" not in body and "–" not in body


def test_action_skill_context_loads_the_playbook(tmp_path):
    from navin.agent.skill_routing import build_action_skill_context

    loader = SkillsLoader(tmp_path, builtin_skills_dir=Path(BUILTIN_SKILLS_DIR))
    context = build_action_skill_context("code", "pr-autopilot", workspace=tmp_path, loader=loader)
    assert context.loaded == ("pr-autopilot",)
    assert "# PR Autopilot" in context.prompt
