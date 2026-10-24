#!/usr/bin/env python3
"""Post one "possible duplicates" notice on an issue (1 to 3 targets).

The notice carries a hidden marker so auto_close_duplicates.py can find it
later and read the targets from it. Every issue number is checked to exist
first. Dry run unless --apply.

Example:
    comment_duplicates.py 412 --duplicates 120 87 --repo acme/app --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gh_common as gh  # noqa: E402


def build_body(repo: str, targets: list[int], grace_days: int) -> str:
    noun = "issue" if len(targets) == 1 else "issues"
    lines = [f"Found {len(targets)} possible duplicate {noun}:", ""]
    lines += [f"{i}. https://github.com/{repo}/issues/{n}" for i, n in enumerate(targets, 1)]
    lines += [
        "",
        f"This issue will be closed as a duplicate in {grace_days} days unless someone objects.",
        "",
        "- Same problem? Close this one and upvote the original instead.",
        "- Not a duplicate? Leave a comment or react with a thumbs down to this notice.",
        "",
        gh.MARKER + " targets=" + ",".join(str(n) for n in targets),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None, client: gh.Client | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("issue", type=int, help="issue that receives the notice")
    parser.add_argument("--duplicates", type=int, nargs="+", required=True, help="1 to 3 older issue numbers")
    parser.add_argument("--grace-days", type=int, default=3, help="days announced before auto-close (default 3)")
    gh.add_common_args(parser)
    args = parser.parse_args(argv)
    targets = list(dict.fromkeys(args.duplicates))
    if not 1 <= len(targets) <= 3:
        parser.error("--duplicates takes 1 to 3 issue numbers")
    if args.issue in targets:
        parser.error("an issue cannot duplicate itself")
    try:
        client = client or gh.Client(gh.detect_repo(args.repo), apply=args.apply)
        for number in (args.issue, *targets):
            if not client.get(client.issue_path(number)):
                print(f"error: issue #{number} not found in {client.repo}", file=sys.stderr)
                return 1
        for comment in client.paginate(client.issue_path(args.issue, "/comments")):
            if gh.MARKER in (comment.get("body") or ""):
                print(f"#{args.issue} already has a duplicate notice, nothing to do")
                return 0
        body = build_body(client.repo, targets, args.grace_days)
        if not client.apply:
            print(body)
        client.write("POST", client.issue_path(args.issue, "/comments"), {"body": body}, why="duplicate notice")
    except gh.GitHubError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{'posted' if client.apply else 'would post'} duplicate notice on #{args.issue}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
