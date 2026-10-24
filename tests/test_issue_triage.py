# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Issue-triage autopilot: forge pagination, labels, rate limits, rules, safety.

Every HTTP call goes to an in-memory fake forge; nothing touches the network.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

from navin.github import triage, triage_runner
from navin.github.forge_issues import ForgeIssueClient
from navin.github.triage import (
    CLOSE_DUPLICATE,
    CLOSE_EXPIRED,
    DISMISS_DUPLICATE,
    FLAG_DUPLICATE,
    MARK_STALE,
    NUDGE,
    UNSTALE,
    TriageRules,
    apply_plan,
    build_plan,
    duplicate_target,
    marker,
)
from navin.github.triage_command import parse_triage_args, triage_brief
from navin.webui.forge_api import FORGEJO, GITHUB, GITLAB, ForgeError, ForgeRemote

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
API = "https://api.github.com"
ME = "navin-maintainer"


def ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")


class Resp:
    def __init__(self, status: int, payload: Any = None, headers: dict[str, str] | None = None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = "" if payload is None else json.dumps(payload)

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeGitHub:
    """Enough of the GitHub REST API for triage, with real pagination."""

    def __init__(self) -> None:
        self.issues: dict[int, dict[str, Any]] = {}
        self.comments: dict[int, list[dict[str, Any]]] = {}
        self.events: dict[int, list[dict[str, Any]]] = {}
        self.reactions: dict[int, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, str, Any]] = []
        self.writes: list[tuple[str, str, Any]] = []
        self.queued: list[Resp] = []
        self._comment_id = 1000

    # -- fixtures --
    def issue(self, number: int, *, title: str = "", body: str = "", labels=(), updated: float = 1,
              created: float = 30, assignees=(), locked: bool = False, upvotes: int = 0,
              author: str = "reporter", pr: bool = False) -> dict[str, Any]:
        row = {
            "number": number,
            "title": title or f"issue {number}",
            "body": body,
            "html_url": f"https://github.com/o/r/issues/{number}",
            "state": "open",
            "labels": [{"name": n} for n in labels],
            "user": {"login": author, "type": "User"},
            "assignees": [{"login": a} for a in assignees],
            "created_at": ago(created),
            "updated_at": ago(updated),
            "comments": 0,
            "locked": locked,
            "reactions": {"+1": upvotes},
        }
        if pr:
            row["pull_request"] = {"url": "x"}
        self.issues[number] = row
        return row

    def comment(self, number: int, author: str, body: str, days: float, *, bot: bool = False) -> dict[str, Any]:
        self._comment_id += 1
        row = {
            "id": self._comment_id,
            "user": {"login": author, "type": "Bot" if bot else "User"},
            "body": body,
            "created_at": ago(days),
        }
        self.comments.setdefault(number, []).append(row)
        return row

    def labeled(self, number: int, label: str, days: float) -> None:
        self.events.setdefault(number, []).append(
            {"event": "labeled", "label": {"name": label}, "created_at": ago(days), "actor": {"login": "maint"}}
        )

    # -- transport --
    def _page(self, url: str, rows: list[Any], params: dict[str, Any] | None) -> Resp:
        query = dict(params or {})
        split = urlsplit(url)
        for key, values in parse_qs(split.query).items():
            query[key] = values[0]
        per = int(query.get("per_page") or 30)
        page = int(query.get("page") or 1)
        chunk = rows[(page - 1) * per : page * per]
        headers = {}
        if page * per < len(rows):
            base = f"{split.scheme}://{split.netloc}{split.path}"
            headers["Link"] = f'<{base}?per_page={per}&page={page + 1}>; rel="next", <{base}?page=99>; rel="last"'
        return Resp(200, chunk, headers)

    def __call__(self, method: str, url: str, **kw: Any) -> Resp:
        params = kw.get("params")
        body = kw.get("json")
        self.calls.append((method, url, params))
        if self.queued:
            return self.queued.pop(0)
        path = unquote(urlsplit(url).path).replace("/repos/o/r", "")
        if method != "GET":
            self.writes.append((method, path, body))
        if method == "GET" and path == "/user":
            return Resp(200, {"login": ME})
        if method == "GET" and path == "/issues":
            rows = sorted(self.issues.values(), key=lambda r: r["updated_at"])
            return self._page(url, rows, params)
        parts = path.strip("/").split("/")
        if parts[:2] == ["issues", "comments"] and parts[-1] == "reactions":
            return self._page(url, self.reactions.get(int(parts[2]), []), params)
        if parts[0] == "issues" and len(parts) >= 2 and parts[1].isdigit():
            number = int(parts[1])
            if number not in self.issues:
                return Resp(404, {"message": "Not Found"})
            issue = self.issues[number]
            if len(parts) == 2 and method == "GET":
                return Resp(200, issue)
            if len(parts) == 2 and method == "PATCH":
                issue.update({k: v for k, v in body.items()})
                return Resp(200, issue)
            if parts[2] == "comments" and method == "GET":
                return self._page(url, self.comments.get(number, []), params)
            if parts[2] == "comments" and method == "POST":
                row = self.comment(number, ME, body["body"], 0)
                return Resp(201, row)
            if parts[2] == "events":
                return self._page(url, self.events.get(number, []), params)
            if parts[2] == "labels" and method == "POST":
                names = {item["name"] for item in issue["labels"]}
                for name in body["labels"]:
                    if name not in names:
                        issue["labels"].append({"name": name})
                return Resp(200, issue["labels"])
            if parts[2] == "labels" and method == "DELETE":
                before = len(issue["labels"])
                issue["labels"] = [item for item in issue["labels"] if item["name"] != parts[3]]
                return Resp(200 if len(issue["labels"]) < before else 404, issue["labels"])
        return Resp(404, {"message": "Not Found"})


def remote(kind: str = GITHUB) -> ForgeRemote:
    host = {GITHUB: "github.com", GITLAB: "gitlab.com", FORGEJO: "code.example.org"}[kind]
    return ForgeRemote(kind=kind, host=host, owner="o", repo="r")


def client(fake: FakeGitHub, sleeps: list[float] | None = None) -> ForgeIssueClient:
    sink = sleeps if sleeps is not None else []
    return ForgeIssueClient(remote(), "tok", sleep=sink.append, clock=lambda: NOW.timestamp())


def plan_for(fake: FakeGitHub, rules: TriageRules | None = None, **kw: Any) -> triage.TriagePlan:
    with patch("navin.github.forge_issues.httpx.request", fake):
        return build_plan(client(fake), rules or TriageRules(), now=NOW, **kw)


def kinds(plan: triage.TriagePlan) -> dict[int, str]:
    return {a.number: a.kind for a in plan.actions}


def skipped(plan: triage.TriagePlan) -> dict[int, str]:
    return {s["number"]: s["reason"] for s in plan.skipped}


# ---------------------------------------------------------------------------
# Forge API extensions
# ---------------------------------------------------------------------------


class ForgeIssuesTest(unittest.TestCase):
    def test_pagination_beyond_100_and_prs_excluded(self) -> None:
        fake = FakeGitHub()
        for n in range(1, 251):
            fake.issue(n, pr=(n % 50 == 0), updated=300 - n)
        with patch("navin.github.forge_issues.httpx.request", fake):
            issues = client(fake).list_open_issues()
        self.assertEqual(len(issues), 245)
        self.assertEqual(sum(1 for c in fake.calls if urlsplit(c[1]).path.endswith("/issues")), 3)
        self.assertEqual(issues[0]["number"], 1)  # oldest update first

    def test_comments_and_events_fully_paginated(self) -> None:
        fake = FakeGitHub()
        fake.issue(1)
        for i in range(230):
            fake.comment(1, "u", f"c{i}", 200 - i * 0.5)
            fake.labeled(1, f"l{i}", 200 - i * 0.5)
        with patch("navin.github.forge_issues.httpx.request", fake):
            c = client(fake)
            self.assertEqual(len(c.comments(1)), 230)
            self.assertEqual(len(c.label_events(1)), 230)

    def test_404_subresources_are_empty(self) -> None:
        fake = FakeGitHub()
        with patch("navin.github.forge_issues.httpx.request", fake):
            c = client(fake)
            self.assertEqual(c.comments(77), [])
            self.assertEqual(c.label_events(77), [])
            self.assertEqual(c.comment_reactions(5), [])
            self.assertIsNone(c.get_issue(77))

    def test_link_to_other_host_is_not_followed(self) -> None:
        fake = FakeGitHub()
        fake.queued = [Resp(200, [{"number": 1, "title": "a"}], {"Link": '<https://evil.example/x?page=2>; rel="next"'})]
        with patch("navin.github.forge_issues.httpx.request", fake):
            issues = client(fake).list_open_issues()
        self.assertEqual(len(issues), 1)
        self.assertEqual(len(fake.calls), 1)

    def test_add_labels_preserves_existing(self) -> None:
        fake = FakeGitHub()
        fake.issue(3, labels=["bug", "area:ui"])
        with patch("navin.github.forge_issues.httpx.request", fake):
            c = client(fake)
            c.add_labels(3, ["stale"])
            c.remove_label(3, "nope")  # absent label: no error
        self.assertEqual([lab["name"] for lab in fake.issues[3]["labels"]], ["bug", "area:ui", "stale"])
        method, path, body = fake.writes[0]
        self.assertEqual((method, path), ("POST", "/issues/3/labels"))
        self.assertEqual(body, {"labels": ["stale"]})
        self.assertFalse(any(m == "PATCH" and "labels" in (b or {}) for m, _p, b in fake.writes))

    def test_close_sends_state_reason_without_labels(self) -> None:
        fake = FakeGitHub()
        fake.issue(4, labels=["bug"])
        with patch("navin.github.forge_issues.httpx.request", fake):
            client(fake).close(4, reason="duplicate")
        self.assertEqual(fake.writes[-1][2], {"state": "closed", "state_reason": "duplicate"})
        self.assertEqual(fake.issues[4]["labels"], [{"name": "bug"}])

    def test_rate_limit_retry_after_backoff(self) -> None:
        fake = FakeGitHub()
        fake.issue(1)
        fake.queued = [
            Resp(429, {"message": "slow down"}, {"Retry-After": "7"}),
            Resp(403, {"message": "API rate limit exceeded"},
                 {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(NOW.timestamp()) + 30)}),
        ]
        sleeps: list[float] = []
        with patch("navin.github.forge_issues.httpx.request", fake):
            issues = client(fake, sleeps).list_open_issues()
        self.assertEqual(len(issues), 1)
        self.assertEqual(sleeps, [7.0, 31.0])

    def test_rate_limit_exponential_then_gives_up(self) -> None:
        fake = FakeGitHub()
        fake.queued = [Resp(503, None)] * 2 + [Resp(429, {"message": "x"})] * 10
        sleeps: list[float] = []
        with patch("navin.github.forge_issues.httpx.request", fake):
            with self.assertRaises(ForgeError) as caught:
                client(fake, sleeps).list_open_issues()
        self.assertEqual(caught.exception.code, "forge_rate_limited")
        self.assertEqual(sleeps, [1.0, 2.0, 4.0, 8.0])

    def test_rate_limit_too_far_away_stops(self) -> None:
        fake = FakeGitHub()
        fake.queued = [Resp(403, {"message": "rate limit"}, {"X-RateLimit-Remaining": "0",
                                                               "X-RateLimit-Reset": str(int(NOW.timestamp()) + 3000)})]
        sleeps: list[float] = []
        with patch("navin.github.forge_issues.httpx.request", fake):
            with self.assertRaises(ForgeError):
                client(fake, sleeps).list_open_issues()
        self.assertEqual(sleeps, [])

    def test_gitlab_labels_and_pagination_shapes(self) -> None:
        calls: list[tuple[str, str, Any, Any]] = []

        def fake(method: str, url: str, **kw: Any) -> Resp:
            calls.append((method, url, kw.get("params"), kw.get("json")))
            if url.endswith("/notes"):
                page = (kw.get("params") or {}).get("page", 1)
                rows = [{"id": page * 10 + i, "author": {"username": "u"}, "body": "x",
                         "created_at": ago(5), "system": i == 0} for i in range(3)]
                return Resp(200, rows, {"X-Next-Page": "2" if page == 1 else ""})
            return Resp(200, {})

        c = ForgeIssueClient(remote(GITLAB), "tok", sleep=lambda s: None)
        with patch("navin.github.forge_issues.httpx.request", fake):
            self.assertEqual(len(c.comments(9)), 4)  # system notes dropped, 2 pages
            c.add_labels(9, ["stale"])
            c.remove_label(9, "needs-info")
            c.close(9)
        self.assertEqual(calls[2][3], {"add_labels": "stale"})
        self.assertEqual(calls[3][3], {"remove_labels": "needs-info"})
        self.assertEqual(calls[4][3], {"state_event": "close"})


# ---------------------------------------------------------------------------
# Lifecycle rules
# ---------------------------------------------------------------------------


class LifecycleTest(unittest.TestCase):
    def test_mark_stale_and_skip_conditions(self) -> None:
        fake = FakeGitHub()
        fake.issue(1, updated=40)
        fake.issue(2, updated=20)
        fake.issue(3, updated=5)  # fresh
        fake.issue(4, updated=40, pr=True)
        fake.issue(5, updated=40, locked=True)
        fake.issue(6, updated=40, assignees=["dev"])
        fake.issue(7, updated=40, labels=["stale"])
        fake.issue(8, updated=40, labels=["autoclose"])
        fake.issue(9, updated=40, upvotes=10)
        fake.issue(10, updated=40, labels=["pinned"])
        for n in (7, 8):
            fake.labeled(n, fake.issues[n]["labels"][0]["name"], 1)
        plan = plan_for(fake, TriageRules(duplicates=triage.DuplicateRules(enabled=False)))
        stale = [a for a in plan.actions if a.kind == MARK_STALE]
        self.assertEqual([a.number for a in stale], [1, 2])  # oldest update first
        reasons = skipped(plan)
        self.assertEqual(reasons[5], "locked")
        self.assertEqual(reasons[6], "assigned to someone")
        self.assertIn("upvotes", reasons[9])
        self.assertEqual(reasons[10], "exempt label")
        self.assertNotIn(4, kinds(plan))
        self.assertEqual(stale[0].add_labels, ["stale"])
        self.assertIn(marker("nudge", "stale"), stale[0].comment)

    def _labeled_issue(self, fake: FakeGitHub, number: int, label: str, days: float, **kw: Any) -> None:
        fake.issue(number, labels=[label], updated=days, **kw)
        fake.labeled(number, label, days)

    def test_each_lifecycle_label_expires(self) -> None:
        fake = FakeGitHub()
        self._labeled_issue(fake, 1, "invalid", 4)
        self._labeled_issue(fake, 2, "needs-repro", 8)
        self._labeled_issue(fake, 3, "needs-info", 8)
        self._labeled_issue(fake, 4, "stale", 15)
        self._labeled_issue(fake, 5, "autoclose", 15)
        plan = plan_for(fake)
        self.assertEqual(kinds(plan), {n: CLOSE_EXPIRED for n in range(1, 6)})
        for action in plan.actions:
            self.assertEqual(action.close_reason, "not_planned")
            self.assertIn("Closing this issue because", action.comment)

    def test_not_yet_expired_gets_one_nudge(self) -> None:
        fake = FakeGitHub()
        self._labeled_issue(fake, 1, "invalid", 2)
        self._labeled_issue(fake, 2, "needs-info", 6)
        self._labeled_issue(fake, 3, "needs-info", 3)
        fake.comment(3, ME, "nudged\n" + marker("nudge", "needs-info"), 2)
        plan = plan_for(fake)
        self.assertEqual(kinds(plan), {1: NUDGE, 2: NUDGE})

    def test_label_event_on_a_late_page_is_found(self) -> None:
        fake = FakeGitHub()
        fake.issue(1, labels=["needs-info"], updated=8)
        for i in range(150):
            fake.events.setdefault(1, []).append(
                {"event": "subscribed", "created_at": ago(60 - i * 0.1), "actor": {"login": "x"}}
            )
        fake.labeled(1, "needs-info", 8)
        plan = plan_for(fake)
        self.assertEqual(kinds(plan), {1: CLOSE_EXPIRED})

    def test_relabel_resets_timer(self) -> None:
        fake = FakeGitHub()
        self._labeled_issue(fake, 1, "needs-info", 20)
        fake.labeled(1, "needs-info", 2)
        plan = plan_for(fake)
        self.assertEqual(kinds(plan), {1: NUDGE})

    def test_human_comment_after_label_vetoes_close(self) -> None:
        fake = FakeGitHub()
        self._labeled_issue(fake, 1, "needs-info", 10)
        fake.comment(1, "reporter", "here are my logs", 5)
        self._labeled_issue(fake, 2, "needs-info", 10)
        fake.comment(2, "ci", "bot ping", 5, bot=True)
        fake.comment(2, "dependabot[bot]", "bump", 4)
        self._labeled_issue(fake, 3, "needs-info", 10)
        fake.comment(3, "reporter", "comment before the label", 12)
        self._labeled_issue(fake, 4, "stale", 20)
        fake.comment(4, "user", "still happens", 3)
        plan = plan_for(fake)
        self.assertNotIn(1, kinds(plan))
        self.assertIn("human comment", skipped(plan)[1])
        self.assertEqual(kinds(plan)[2], CLOSE_EXPIRED)
        self.assertEqual(kinds(plan)[3], CLOSE_EXPIRED)
        self.assertEqual(kinds(plan)[4], UNSTALE)

    def test_upvoted_locked_assigned_never_closed(self) -> None:
        fake = FakeGitHub()
        self._labeled_issue(fake, 1, "stale", 20, upvotes=12)
        self._labeled_issue(fake, 2, "stale", 20, locked=True)
        self._labeled_issue(fake, 3, "stale", 20, assignees=["dev"])
        plan = plan_for(fake)
        self.assertEqual(kinds(plan), {})
        self.assertEqual(set(skipped(plan)), {1, 2, 3})

    def test_label_without_event_is_skipped(self) -> None:
        fake = FakeGitHub()
        fake.issue(1, labels=["needs-info"], updated=30)
        plan = plan_for(fake)
        self.assertEqual(kinds(plan), {})
        self.assertIn("no recorded labeled event", skipped(plan)[1])

    def test_safety_limits_defer(self) -> None:
        fake = FakeGitHub()
        for n in range(1, 6):
            self._labeled_issue(fake, n, "invalid", 5)
        plan = plan_for(fake, TriageRules(max_closes=2))
        self.assertEqual(len(plan.actions), 2)
        self.assertEqual(len(plan.deferred), 3)

    def test_rules_from_config_file_and_repo_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".navin").mkdir()
            (root / ".navin" / "triage.json").write_text(json.dumps({
                "tone": "neutral",
                "lifecycle": {"invalid": None, "needs-info": {"days": 2}, "waiting": {"days": 5}},
                "unattended_apply": "yes",
                "repos": {"O/R": {"stale_days": 30}},
            }))
            rules = triage.load_rules(root, "o/r")
        self.assertIsNone(rules.rule_for("invalid"))
        self.assertEqual(rules.rule_for("needs-info").days, 2)
        self.assertEqual(rules.rule_for("waiting").days, 5)
        self.assertEqual(rules.stale_days, 30)
        self.assertEqual(rules.prefix(), "")
        self.assertFalse(rules.unattended_apply)  # only literal true opts in


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


def _dup_pair(fake: FakeGitHub) -> None:
    fake.issue(10, title="Crash when exporting PDF report with images", created=40, updated=3,
               body="Exporting a PDF report that contains images crashes the renderer process.")
    fake.issue(11, title="PDF report export crashes with images", created=2, updated=1,
               body="The renderer process crashes exporting a PDF report containing images.")
    fake.issue(12, title="Dark mode toggle missing in settings", created=1, updated=1,
               body="Settings page has no dark theme switch.")


def _notice(fake: FakeGitHub, number: int, target: int, days: float, author: str = ME) -> dict[str, Any]:
    fake.issues[number]["labels"].append({"name": "possible-duplicate"})
    body = f"Navin here: possible duplicate of:\n- #{target}\n\n{marker('duplicate-of', target)}"
    return fake.comment(number, author, body, days)


class DuplicateTest(unittest.TestCase):
    def test_detection_flags_newer_issue_with_marker(self) -> None:
        fake = FakeGitHub()
        _dup_pair(fake)
        plan = plan_for(fake)
        self.assertEqual(kinds(plan), {11: FLAG_DUPLICATE})
        action = plan.actions[0]
        self.assertEqual(action.target, 10)
        self.assertEqual(duplicate_target(action.comment), 10)
        self.assertEqual(action.add_labels, ["possible-duplicate"])
        self.assertEqual(plan.similarity, "tokens")

    def test_detection_uses_embeddings_when_available(self) -> None:
        fake = FakeGitHub()
        _dup_pair(fake)
        vectors = {10: [1.0, 0.0], 11: [0.0, 1.0], 12: [0.0, 1.0]}

        def embed(texts: list[str]) -> list[list[float]]:
            order = sorted(fake.issues.values(), key=lambda r: (r["created_at"], r["number"]))
            return [vectors[r["number"]] for r in order]

        plan = plan_for(fake, embed=embed)
        self.assertEqual(plan.similarity, "embeddings")
        self.assertEqual(kinds(plan), {12: FLAG_DUPLICATE})
        self.assertEqual(plan.actions[0].target, 11)

    def test_previous_notice_is_not_repeated(self) -> None:
        fake = FakeGitHub()
        _dup_pair(fake)
        fake.comment(11, ME, "old\n" + marker("duplicate-of", 10), 1)
        plan = plan_for(fake)
        self.assertNotIn(11, kinds(plan))

    def test_target_comes_from_marker_not_first_hash(self) -> None:
        body = "See #3 and #4 in the logs.\n- #42\n" + marker("duplicate-of", 42)
        self.assertEqual(duplicate_target(body), 42)
        self.assertIsNone(duplicate_target("possible duplicate of #7"))

    def test_waits_three_days(self) -> None:
        fake = FakeGitHub()
        _dup_pair(fake)
        _notice(fake, 11, 10, 2)
        plan = plan_for(fake)
        self.assertNotIn(11, kinds(plan))
        self.assertIn("waiting", skipped(plan)[11])

    def test_closes_after_wait_keeping_labels(self) -> None:
        fake = FakeGitHub()
        _dup_pair(fake)
        fake.issues[11]["labels"].append({"name": "bug"})
        _notice(fake, 11, 10, 4)
        plan = plan_for(fake)
        self.assertEqual(kinds(plan)[11], CLOSE_DUPLICATE)
        with patch("navin.github.forge_issues.httpx.request", fake):
            result = apply_plan(client(fake), plan)
        self.assertEqual(result["errors"], 0)
        issue = fake.issues[11]
        self.assertEqual(issue["state"], "closed")
        self.assertEqual(issue["state_reason"], "duplicate")
        self.assertEqual({lab["name"] for lab in issue["labels"]}, {"bug", "duplicate"})

    def test_author_thumbs_down_vetoes(self) -> None:
        fake = FakeGitHub()
        _dup_pair(fake)
        notice = _notice(fake, 11, 10, 5)
        fake.reactions[notice["id"]] = [{"content": "-1", "user": {"login": "reporter"}}]
        plan = plan_for(fake)
        self.assertEqual(kinds(plan)[11], DISMISS_DUPLICATE)
        self.assertNotEqual(plan.actions[0].close_reason, "duplicate")

    def test_other_users_thumbs_down_does_not_veto(self) -> None:
        fake = FakeGitHub()
        _dup_pair(fake)
        notice = _notice(fake, 11, 10, 5)
        fake.reactions[notice["id"]] = [{"content": "-1", "user": {"login": "someone-else"}}]
        self.assertEqual(kinds(plan_for(fake))[11], CLOSE_DUPLICATE)

    def test_later_comment_vetoes(self) -> None:
        fake = FakeGitHub()
        _dup_pair(fake)
        _notice(fake, 11, 10, 5)
        fake.comment(11, "reporter", "not the same: mine is on Windows", 4)
        plan = plan_for(fake)
        self.assertEqual(kinds(plan)[11], DISMISS_DUPLICATE)

    def test_spoofed_notice_by_another_user_is_ignored(self) -> None:
        fake = FakeGitHub()
        _dup_pair(fake)
        _notice(fake, 11, 10, 5, author="troll")
        plan = plan_for(fake)
        self.assertNotIn(11, kinds(plan))
        self.assertIn("without a Navin duplicate notice", skipped(plan)[11])

    def test_missing_target_skips(self) -> None:
        fake = FakeGitHub()
        _dup_pair(fake)
        _notice(fake, 11, 999, 5)
        plan = plan_for(fake)
        self.assertNotIn(11, kinds(plan))


# ---------------------------------------------------------------------------
# Dry run, apply, approval
# ---------------------------------------------------------------------------


class _Gate:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.requests: list[Any] = []

    async def ask(self, request: Any) -> Any:
        from navin.agent.approval import ApprovalDecision

        self.requests.append(request)
        return ApprovalDecision(allowed=self.allowed, reason="" if self.allowed else "no")


class SafetyTest(unittest.TestCase):
    def _fake(self) -> FakeGitHub:
        fake = FakeGitHub()
        fake.issue(1, labels=["invalid"], updated=5)
        fake.labeled(1, "invalid", 5)
        fake.issue(2, updated=40)
        return fake

    def test_plan_makes_no_writes(self) -> None:
        fake = self._fake()
        _dup_pair(fake)
        plan = plan_for(fake)
        self.assertTrue(plan.actions)
        self.assertEqual(fake.writes, [])
        self.assertTrue(all(method == "GET" for method, _u, _p in fake.calls))
        rendered = triage.render_plan(plan)
        # The plan renders as a canvas (stats + table) marked as a dry run,
        # and rendering it still writes nothing.
        self.assertEqual(rendered["format"], "canvas")
        self.assertIn("navin-canvas", rendered["content"])
        self.assertIn("Dry run", rendered["content"])
        self.assertEqual(fake.writes, [])
        # Nothing to do: no empty canvas, a markdown note instead.
        empty = triage.TriagePlan(repo=plan.repo, forge=plan.forge, generated_at=plan.generated_at)
        self.assertEqual(triage.render_plan(empty)["format"], "markdown")

    def _run(self, coro_factory, gate: _Gate | None, fake: FakeGitHub, *, rules: TriageRules | None = None,
             metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        from navin.agent.approval import bind_approval_gate, reset_approval_gate
        from navin.agent.tools.context import (
            RequestContext,
            bind_request_context,
            reset_request_context,
        )

        async def go() -> dict[str, Any]:
            gate_token = bind_approval_gate(gate)
            ctx_token = bind_request_context(RequestContext(channel="websocket", chat_id="c", metadata=metadata or {}))
            try:
                return await coro_factory()
            finally:
                reset_request_context(ctx_token)
                reset_approval_gate(gate_token)

        with (
            patch("navin.github.forge_issues.httpx.request", fake),
            patch.object(triage_runner, "resolve_repo", lambda root, repo="", config=None: (remote(), "tok")),
            patch.object(triage_runner, "load_rules", lambda root, slug: rules or TriageRules()),
            patch.object(triage_runner, "_embedder", lambda: None),
            patch("navin.github.triage.datetime") as fake_dt,
        ):
            fake_dt.now.return_value = NOW
            fake_dt.fromisoformat = datetime.fromisoformat
            return asyncio.run(go())

    def test_apply_requires_approval_refused(self) -> None:
        fake = self._fake()
        gate = _Gate(False)
        out = self._run(lambda: triage_runner.triage_apply(Path("."), config=object()), gate, fake)
        self.assertEqual(out["applied"], 0)
        self.assertEqual(len(gate.requests), 1)
        self.assertIn("2 action(s), 1 close(s)", gate.requests[0].action)
        self.assertEqual(fake.writes, [])

    def test_apply_with_approval_writes(self) -> None:
        fake = self._fake()
        gate = _Gate(True)
        out = self._run(lambda: triage_runner.triage_apply(Path("."), config=object()), gate, fake)
        self.assertEqual(out["applied"], 2)
        self.assertEqual(fake.issues[1]["state"], "closed")
        self.assertIn({"name": "stale"}, fake.issues[2]["labels"])
        self.assertIn("applied", out["render"]["content"])

    def test_unattended_apply_refused_without_opt_in(self) -> None:
        fake = self._fake()
        gate = _Gate(True)
        out = self._run(
            lambda: triage_runner.triage_apply(Path("."), config=object()),
            gate,
            fake,
            metadata={"_cron_trigger": True},
        )
        self.assertIn("unattended apply is off", out["refused"])
        self.assertEqual(gate.requests, [])
        self.assertEqual(fake.writes, [])

    def test_no_gate_refused_without_opt_in(self) -> None:
        fake = self._fake()
        out = self._run(lambda: triage_runner.triage_apply(Path("."), config=object()), None, fake)
        self.assertIn("refused", out)
        self.assertEqual(fake.writes, [])

    def test_unattended_apply_with_opt_in(self) -> None:
        fake = self._fake()
        out = self._run(
            lambda: triage_runner.triage_apply(Path("."), config=object()),
            None,
            fake,
            rules=TriageRules(unattended_apply=True),
            metadata={"_cron_trigger": True},
        )
        self.assertEqual(out["applied"], 2)

    def test_plan_action_never_writes(self) -> None:
        fake = self._fake()
        out = self._run(lambda: triage_runner.triage_plan(Path("."), config=object()), None, fake)
        self.assertTrue(out["plan"]["dry_run"])
        self.assertEqual(fake.writes, [])


class CommandTest(unittest.TestCase):
    def test_parse_args(self) -> None:
        self.assertEqual(parse_triage_args(""), ("", False, ""))
        self.assertEqual(parse_triage_args("o/r --apply"), ("o/r", True, ""))
        self.assertEqual(parse_triage_args("--apply https://github.com/o/r"), ("https://github.com/o/r", True, ""))
        self.assertTrue(parse_triage_args("--force")[2])
        self.assertTrue(parse_triage_args("a/b c/d")[2])

    def test_brief(self) -> None:
        self.assertIn("issue_triage_plan repo=o/r", triage_brief("o/r", False))
        self.assertIn("issue_triage_apply", triage_brief("", True))
        self.assertIn("untrusted", triage_brief("", False))


if __name__ == "__main__":
    unittest.main()


class IntegrationTest(unittest.TestCase):
    def test_forge_tool_exposes_triage_actions(self) -> None:
        from navin.agent.tools import forge as forge_tool

        enum = forge_tool.ForgeTool.parameters.fget(None)["properties"]["action"]["enum"]  # type: ignore[arg-type]
        for act in ("issue_triage_plan", "issue_triage_apply", "issue_labels"):
            self.assertIn(act, enum)
        tool = forge_tool.ForgeTool.__new__(forge_tool.ForgeTool)
        self.assertTrue(tool.call_read_only({"action": "issue_triage_plan"}))
        self.assertFalse(tool.call_read_only({"action": "issue_triage_apply"}))

    def test_triage_command_registered_as_agent_turn(self) -> None:
        from navin.command.builtin import is_agent_turn_command, register_builtin_commands
        from navin.command.router import CommandRouter

        router = CommandRouter()
        register_builtin_commands(router)
        self.assertTrue(is_agent_turn_command("/triage o/r --apply"))

    def test_triage_command_rewrites_message(self) -> None:
        from navin.bus.events import InboundMessage
        from navin.command.router import CommandContext
        from navin.github.triage_command import cmd_triage

        msg = InboundMessage(channel="websocket", sender_id="u", chat_id="c", content="/triage o/r")
        ctx = CommandContext(msg=msg, session=None, key="k", raw="/triage o/r", args="o/r")
        self.assertIsNone(asyncio.run(cmd_triage(ctx)))
        self.assertIn("issue_triage_plan repo=o/r", msg.content)
        self.assertEqual(msg.metadata["preload_skills"], ["issue-triage"])
