#!/usr/bin/env python3
"""Add or remove labels on one issue, one by one (existing labels are kept).

Labels that do not exist in the repository are skipped with a notice
instead of being created by accident. Dry run unless --apply.

Example:
    edit_labels.py 412 --add bug needs-repro --remove stale --repo acme/app --apply
"""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gh_common as gh  # noqa: E402


def main(argv: list[str] | None = None, client: gh.Client | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("issue", type=int)
    parser.add_argument("--add", nargs="*", default=[], help="labels to add")
    parser.add_argument("--remove", nargs="*", default=[], help="labels to remove")
    parser.add_argument("--allow-unknown", action="store_true", help="do not check that labels exist")
    gh.add_common_args(parser)
    args = parser.parse_args(argv)
    if not args.add and not args.remove:
        parser.error("give --add and/or --remove")
    try:
        client = client or gh.Client(gh.detect_repo(args.repo), apply=args.apply)
        issue = client.get(client.issue_path(args.issue))
        if not issue:
            print(f"error: issue #{args.issue} not found", file=sys.stderr)
            return 1
        current = {lbl["name"] for lbl in issue.get("labels", [])}
        known = None if args.allow_unknown else {
            lbl["name"] for lbl in client.paginate(f"/repos/{client.repo}/labels")}
        to_add = [lbl for lbl in args.add if lbl not in current and (known is None or lbl in known)]
        for lbl in args.add:
            if known is not None and lbl not in known:
                print(f"skip unknown label {lbl!r}")
        to_remove = [lbl for lbl in args.remove if lbl in current]
        if to_add:
            client.write("POST", client.issue_path(args.issue, "/labels"), {"labels": to_add}, why=f"add {to_add}")
        for lbl in to_remove:
            client.write("DELETE", client.issue_path(args.issue, "/labels/" + urllib.parse.quote(lbl, safe="")),
                         why=f"remove {lbl!r}")
    except gh.GitHubError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"#{args.issue}: add={to_add} remove={to_remove}{'' if client.apply else ' (dry run)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
