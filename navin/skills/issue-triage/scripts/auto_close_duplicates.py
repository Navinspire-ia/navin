#!/usr/bin/env python3
"""Close issues flagged as duplicates once the grace period has passed.

An open issue is closed (state_reason=duplicate, label `duplicate`) when:
  - it carries a duplicate notice written by comment_duplicates.py (marker),
    posted by the trusted account (--bot-login, default: any bot or the
    account of the current token),
  - the notice is older than --grace-days (default 3),
  - no human commented after the notice,
  - the issue author did not react with a thumbs down to the notice.
The target issue is read from the marker, never guessed from the text.
Dry run unless --apply.

Example:
    auto_close_duplicates.py --repo acme/app --grace-days 3 --max-closes 20 --apply
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gh_common as gh  # noqa: E402

TARGETS = re.compile(re.escape(gh.MARKER) + r"\s*targets=([\d,]+)")


def find_notice(comments: list[dict[str, Any]], trusted: set[str]) -> dict[str, Any] | None:
    notices = [
        c for c in comments
        if gh.MARKER in (c.get("body") or "")
        and (gh.is_bot(c.get("user")) or (c.get("user") or {}).get("login") in trusted)
    ]
    return notices[-1] if notices else None


def decide(client: gh.Client, issue: dict[str, Any], trusted: set[str], grace_days: float,
           now: datetime) -> tuple[int | None, str]:
    comments = list(client.paginate(client.issue_path(issue["number"], "/comments")))
    notice = find_notice(comments, trusted)
    if not notice:
        return None, "no duplicate notice"
    age = gh.days_since(notice["created_at"], now)
    if age < grace_days:
        return None, f"notice is {age:.1f}d old (grace {grace_days}d)"
    noticed = gh.parse_time(notice["created_at"])
    for c in comments:
        if gh.parse_time(c["created_at"]) > noticed and not gh.is_bot(c.get("user")) \
                and gh.MARKER not in (c.get("body") or ""):
            return None, f"objection: @{c['user']['login']} commented after the notice"
    author = (issue.get("user") or {}).get("login")
    reactions = client.get(f"/repos/{client.repo}/issues/comments/{notice['id']}/reactions") or []
    if any(r.get("content") == "-1" and (r.get("user") or {}).get("login") == author for r in reactions):
        return None, "objection: author reacted thumbs down"
    match = TARGETS.search(notice.get("body") or "")
    if not match:
        return None, "notice has no target list"
    target = int(match.group(1).split(",")[0])
    return target, f"duplicate of #{target}, notice {age:.1f}d old, no objection"


def main(argv: list[str] | None = None, client: gh.Client | None = None,
         now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grace-days", type=float, default=3)
    parser.add_argument("--lookback-days", type=int, default=60, help="only issues created in this window")
    parser.add_argument("--max-closes", type=int, default=20, help="cap per run (rest is deferred)")
    parser.add_argument("--bot-login", action="append", default=[], help="extra trusted notice author")
    gh.add_common_args(parser)
    args = parser.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    try:
        client = client or gh.Client(gh.detect_repo(args.repo), apply=args.apply)
        trusted = set(args.bot_login)
        me = client.get("/user") if not args.bot_login else None
        if me and me.get("login"):
            trusted.add(me["login"])
        since = (now - timedelta(days=args.lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        closed = 0
        for issue in client.paginate(f"/repos/{client.repo}/issues?state=open&since={since}"):
            if issue.get("pull_request") or issue.get("locked"):
                continue
            if gh.days_since(issue["created_at"], now) < args.grace_days:
                continue
            target, why = decide(client, issue, trusted, args.grace_days, now)
            if target is None:
                if why != "no duplicate notice":
                    print(f"#{issue['number']}: keep ({why})")
                continue
            if closed >= args.max_closes:
                print(f"#{issue['number']}: deferred (max {args.max_closes} closes per run)")
                continue
            path = client.issue_path(issue["number"])
            client.write("POST", path + "/comments", {"body": (
                f"Closing as a duplicate of #{target}. Follow that issue for updates, "
                "and reopen this one if it turns out to be different.")}, why=f"#{issue['number']} close note")
            client.write("POST", path + "/labels", {"labels": ["duplicate"]}, why="label duplicate")
            client.write("PATCH", path, {"state": "closed", "state_reason": "duplicate"}, why=why)
            closed += 1
    except gh.GitHubError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{'closed' if client.apply else 'would close'} {closed} issue(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
