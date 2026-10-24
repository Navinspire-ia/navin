#!/usr/bin/env python3
"""Issue lifecycle: nudge on a lifecycle label, mark stale, close expired.

Subcommands:
  nudge ISSUE --label L   comment what is needed and when it closes
  sweep                   mark inactive issues stale, then close every issue
                          whose lifecycle label is older than its timeout and
                          that got no human comment since

Timeouts come from the built-in table below, overridden by --config
(JSON: {"lifecycle": [{"label", "days", "reason", "nudge"}], "upvote_threshold",
"exempt_labels"}) and by --stale-days / --days LABEL=N. Dry run unless --apply.

Examples:
    issue_lifecycle.py sweep --repo acme/app --stale-days 30
    issue_lifecycle.py sweep --days needs-info=10 --apply
    issue_lifecycle.py nudge 412 --label needs-repro --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gh_common as gh  # noqa: E402

NUDGE_MARKER = "<!-- navin:lifecycle-nudge -->"

DEFAULT_LIFECYCLE: list[dict[str, Any]] = [
    {"label": "invalid", "days": 3, "reason": "it does not look related to this project",
     "nudge": "This does not look related to this project. Point us to the link if we misread it."},
    {"label": "needs-repro", "days": 7, "reason": "reproduction steps are still missing",
     "nudge": "We could not reproduce this yet. Please share the exact steps, what happened and what you expected."},
    {"label": "needs-info", "days": 7, "reason": "the requested details are still missing",
     "nudge": "We need more details to continue: version, operating system and the full error or log output."},
    {"label": "stale", "days": 14, "reason": "there was no activity for a long time",
     "nudge": "This issue was marked stale after a period without activity."},
    {"label": "autoclose", "days": 14, "reason": "there was no activity for a long time",
     "nudge": "This issue is scheduled for automatic closure."},
]


def load_rules(args: argparse.Namespace) -> dict[str, Any]:
    rules: dict[str, Any] = {"lifecycle": [dict(e) for e in DEFAULT_LIFECYCLE], "upvote_threshold": 10,
                             "exempt_labels": ["pinned", "security", "keep-open"]}
    if args.config:
        data = json.loads(Path(args.config).read_text(encoding="utf-8"))
        rules.update({k: v for k, v in data.items() if k in rules})
    by_label = {e["label"]: e for e in rules["lifecycle"]}
    if getattr(args, "stale_days", None) is not None and "stale" in by_label:
        by_label["stale"]["days"] = args.stale_days
    for item in getattr(args, "days", None) or []:
        label, _, value = item.partition("=")
        if label not in by_label or not value.isdigit():
            raise SystemExit(f"--days expects LABEL=N with a known label, got {item!r}")
        by_label[label]["days"] = int(value)
    return rules


def nudge_text(entry: dict[str, Any]) -> str:
    return (f"{entry['nudge']} It will be closed automatically if there is no activity within "
            f"{entry['days']} days.\n\n{NUDGE_MARKER}")


def cmd_nudge(client: gh.Client, args: argparse.Namespace, rules: dict[str, Any]) -> int:
    entry = next((e for e in rules["lifecycle"] if e["label"] == args.label), None)
    if not entry:
        print(f"no lifecycle entry for label {args.label!r}, nothing to do")
        return 0
    body = nudge_text(entry)
    if not client.apply:
        print(body)
    client.write("POST", client.issue_path(args.issue, "/comments"), {"body": body}, why=f"nudge {args.label}")
    return 0


def _labels(issue: dict[str, Any]) -> set[str]:
    return {lbl["name"] for lbl in issue.get("labels", [])}


def mark_stale(client: gh.Client, rules: dict[str, Any], now: datetime, budget: list[int]) -> int:
    stale = next((e for e in rules["lifecycle"] if e["label"] == "stale"), None)
    if not stale:
        return 0
    cutoff = now - timedelta(days=stale["days"])
    lifecycle_labels = {e["label"] for e in rules["lifecycle"]}
    count = 0
    for issue in client.paginate(f"/repos/{client.repo}/issues?state=open&sort=updated&direction=asc"):
        if gh.parse_time(issue["updated_at"]) > cutoff:
            break  # sorted oldest update first: everything after is fresh
        labels = _labels(issue)
        if issue.get("pull_request") or issue.get("locked") or issue.get("assignees"):
            continue
        if labels & lifecycle_labels or labels & set(rules["exempt_labels"]):
            continue
        if (issue.get("reactions") or {}).get("+1", 0) >= rules["upvote_threshold"]:
            continue
        if budget[0] <= 0:
            print(f"#{issue['number']}: deferred (action cap)")
            continue
        age = gh.days_since(issue["updated_at"], now)
        path = client.issue_path(issue["number"])
        client.write("POST", path + "/labels", {"labels": ["stale"]}, why=f"stale, {age:.0f}d inactive")
        client.write("POST", path + "/comments", {"body": nudge_text(stale)}, why="stale nudge")
        budget[0] -= 1
        count += 1
    return count


def close_expired(client: gh.Client, rules: dict[str, Any], now: datetime, budget: list[int]) -> int:
    count = 0
    for entry in rules["lifecycle"]:
        label = entry["label"]
        for issue in client.paginate(f"/repos/{client.repo}/issues?state=open&labels={label}"):
            if issue.get("pull_request") or issue.get("locked") or issue.get("assignees"):
                continue
            if _labels(issue) & set(rules["exempt_labels"]):
                continue
            if (issue.get("reactions") or {}).get("+1", 0) >= rules["upvote_threshold"]:
                continue
            path = client.issue_path(issue["number"])
            events = list(client.paginate(path + "/events"))
            stamps = [e["created_at"] for e in events
                      if e.get("event") == "labeled" and (e.get("label") or {}).get("name") == label]
            if not stamps:
                continue
            labeled_at = max(gh.parse_time(s) for s in stamps)
            if now - labeled_at < timedelta(days=entry["days"]):
                continue
            comments = list(client.paginate(path + "/comments"))
            human = [c for c in comments if gh.parse_time(c["created_at"]) > labeled_at
                     and not gh.is_bot(c.get("user")) and NUDGE_MARKER not in (c.get("body") or "")]
            if human:
                if label in ("stale", "autoclose"):
                    client.write("DELETE", f"{path}/labels/{label}", why="unstale: human activity")
                continue
            if budget[0] <= 0:
                print(f"#{issue['number']}: deferred (action cap)")
                continue
            client.write("POST", path + "/comments", {"body": (
                f"Closing for now because {entry['reason']}. Open a new issue if this is still relevant.")},
                why=f"{label} expired")
            client.write("PATCH", path, {"state": "closed", "state_reason": "not_planned"}, why="close")
            budget[0] -= 1
            count += 1
    return count


def main(argv: list[str] | None = None, client: gh.Client | None = None,
         now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_nudge = sub.add_parser("nudge", help="comment after a lifecycle label was applied")
    p_nudge.add_argument("issue", type=int)
    p_nudge.add_argument("--label", required=True)
    p_sweep = sub.add_parser("sweep", help="mark stale and close expired issues")
    p_sweep.add_argument("--stale-days", type=int, help="inactivity before `stale` (default 14)")
    p_sweep.add_argument("--days", action="append", metavar="LABEL=N", help="override one label timeout")
    p_sweep.add_argument("--max-actions", type=int, default=50, help="cap of issues touched per run")
    p_sweep.add_argument("--skip-stale", action="store_true", help="only close expired issues")
    for p in (p_nudge, p_sweep):
        p.add_argument("--config", help="JSON rules file")
        gh.add_common_args(p)
    args = parser.parse_args(argv)
    rules = load_rules(args)
    now = now or datetime.now(timezone.utc)
    try:
        client = client or gh.Client(gh.detect_repo(args.repo), apply=args.apply)
        if args.cmd == "nudge":
            return cmd_nudge(client, args, rules)
        budget = [args.max_actions]
        staled = 0 if args.skip_stale else mark_stale(client, rules, now, budget)
        closed = close_expired(client, rules, now, budget)
    except gh.GitHubError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    verb = "" if client.apply else "would be "
    print(f"stale: {staled} {verb}marked, expired: {closed} {verb}closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
