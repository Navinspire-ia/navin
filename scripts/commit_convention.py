#!/usr/bin/env python3
"""Conventional Commits checker and SemVer bump helper.

Humans follow CONTRIBUTING.md and .github/COMMIT_CONVENTION.md.
Agents MUST run this before opening or editing a pull request.
CI runs `check-title` on every pull_request event.

Subcommands:
  check-title     validate a PR title (and body/labels when breaking)
  next-version    compute the next SemVer from conventional commits
  --self-test     run the embedded contract checks and exit
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, NoReturn

ROOT = Path(__file__).resolve().parents[1]
MAX_SUBJECT_LEN = 72
TYPES = (
    "feat",
    "fix",
    "perf",
    "refactor",
    "docs",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
)
PATCH_TYPES = frozenset({"fix", "perf"})
MINOR_TYPES = frozenset({"feat"})
MAJOR_LABEL = "semver:major"
TITLE_RE = re.compile(
    r"^(?P<type>"
    + "|".join(TYPES)
    + r")"
    + r"(?:\((?P<scope>[a-z][a-z0-9-]*)\))?"
    + r"(?P<breaking>!)?"
    + r": (?P<subject>[a-z0-9].*?)"
    + r"(?: \(#\d+\))?$"
)
BREAKING_FOOTER_RE = re.compile(
    r"^(?:BREAKING CHANGE|BREAKING-CHANGE): \S",
    re.MULTILINE,
)
PYPROJECT_VERSION_RE = re.compile(
    r'^version\s*=\s*"(\d+\.\d+\.\d+)"',
    re.MULTILINE,
)


class ConventionError(ValueError):
    """Raised when a title, body, or bump input is not releasable."""


@dataclass(frozen=True)
class ParsedTitle:
    type: str
    scope: str | None
    breaking: bool
    subject: str


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def bump(self, kind: str) -> Version:
        if kind == "major":
            return Version(self.major + 1, 0, 0)
        if kind == "minor":
            return Version(self.major, self.minor + 1, 0)
        if kind == "patch":
            return Version(self.major, self.minor, self.patch + 1)
        return self


def parse_version(raw: str) -> Version:
    text = raw.strip()
    if text.startswith("v"):
        text = text[1:]
    parts = text.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ConventionError(f"not a SemVer X.Y.Z: {raw!r}")
    return Version(int(parts[0]), int(parts[1]), int(parts[2]))


def parse_title(title: str) -> ParsedTitle:
    stripped = title.strip()
    if not stripped:
        raise ConventionError("title is empty")
    if "\n" in stripped:
        raise ConventionError("title must be a single line")
    if len(stripped) > MAX_SUBJECT_LEN:
        raise ConventionError(
            f"title is {len(stripped)} chars; maximum is {MAX_SUBJECT_LEN}"
        )
    if stripped.endswith("."):
        raise ConventionError("title must not end with a period")
    match = TITLE_RE.fullmatch(stripped)
    if not match:
        raise ConventionError(
            "title must match `<type>(<optional-scope>)!: <subject>` with a known type, "
            + "optional kebab-case scope, lowercase subject, and no trailing period. "
            + f"Known types: {', '.join(TYPES)}."
        )
    return ParsedTitle(
        type=match.group("type"),
        scope=match.group("scope"),
        breaking=bool(match.group("breaking")),
        subject=match.group("subject"),
    )


def has_breaking_footer(body: str) -> bool:
    return bool(BREAKING_FOOTER_RE.search(body or ""))


def parse_labels(raw: str | Iterable[str] | None) -> set[str]:
    if raw is None:
        return set()
    if isinstance(raw, str):
        parts = re.split(r"[\n,]", raw)
    else:
        parts = list(raw)
    return {item.strip() for item in parts if item.strip()}


def check_title(
    title: str,
    *,
    body: str = "",
    labels: Iterable[str] | str | None = None,
) -> ParsedTitle:
    parsed = parse_title(title)
    if parsed.breaking:
        if not has_breaking_footer(body):
            raise ConventionError(
                "breaking title (`type!:`) requires a footer "
                + "`BREAKING CHANGE: <what the user must do>` in the PR body"
            )
        label_set = parse_labels(labels)
        if MAJOR_LABEL not in label_set:
            raise ConventionError(
                f"breaking title requires the `{MAJOR_LABEL}` label "
                + "so a mistyped bang cannot ship a major version"
            )
    return parsed


def bump_rank_for_commit(message: str) -> int:
    """0 none, 1 patch, 2 minor, 3 major. Non-conventional messages are 0."""
    if not message.strip():
        return 0
    subject, _, body = message.strip().partition("\n")
    try:
        parsed = parse_title(subject.strip())
    except ConventionError:
        return 0
    if parsed.breaking or has_breaking_footer(body):
        return 3
    if parsed.type in MINOR_TYPES:
        return 2
    if parsed.type in PATCH_TYPES:
        return 1
    return 0


def next_version(current: Version, messages: Iterable[str]) -> tuple[Version, str]:
    rank = 0
    for message in messages:
        rank = max(rank, bump_rank_for_commit(message))
    kind = {0: "none", 1: "patch", 2: "minor", 3: "major"}[rank]
    return current.bump(kind), kind


def read_pyproject_version(root: Path = ROOT) -> Version:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = PYPROJECT_VERSION_RE.search(text)
    if not match:
        raise ConventionError("pyproject.toml has no version = \"X.Y.Z\"")
    return parse_version(match.group(1))


def git_output(args: list[str], cwd: Path) -> str:
    try:
        completed = subprocess.run(
            ["/usr/bin/git", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise ConventionError(f"git failed: {exc}") from exc
    if completed.returncode != 0:
        raise ConventionError(completed.stderr.strip() or "git failed")
    return completed.stdout


def last_version_tag(cwd: Path) -> str | None:
    completed = subprocess.run(
        ["/usr/bin/git", "describe", "--tags", "--match", "v[0-9]*", "--abbrev=0"],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    tag = completed.stdout.strip()
    return tag or None


def commits_since(cwd: Path, since: str | None) -> list[str]:
    args = ["log", "--format=%B%x1e"]
    if since:
        args.append(f"{since}..HEAD")
    else:
        args.append("HEAD")
    raw = git_output(args, cwd)
    return [chunk.strip() for chunk in raw.split("\x1e") if chunk.strip()]


def die(message: str, code: int = 1) -> NoReturn:
    print(f"commit-convention: {message}", file=sys.stderr)
    raise SystemExit(code)


def cmd_check_title(args: argparse.Namespace) -> int:
    title = args.title
    if args.title_file:
        title = Path(args.title_file).read_text(encoding="utf-8")
    if title is None:
        title = os.environ.get("PR_TITLE", "")
    body = args.body or ""
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    elif not body:
        body = os.environ.get("PR_BODY", "")
    labels = args.labels
    if not labels:
        labels = os.environ.get("PR_LABELS", "")
    try:
        parsed = check_title(title, body=body, labels=labels)
    except ConventionError as exc:
        die(str(exc))
    print(
        json.dumps(
            {
                "type": parsed.type,
                "scope": parsed.scope,
                "breaking": parsed.breaking,
                "subject": parsed.subject,
            }
        )
    )
    return 0


def cmd_next_version(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else ROOT
    try:
        if args.current:
            current = parse_version(args.current)
        else:
            current = read_pyproject_version(root)
        if args.commits_file:
            text = Path(args.commits_file).read_text(encoding="utf-8")
            messages = [chunk.strip() for chunk in text.split("\n\n") if chunk.strip()]
            since = None
        else:
            since = args.since
            if since is None:
                since = last_version_tag(root)
            messages = commits_since(root, since)
        nxt, kind = next_version(current, messages)
    except ConventionError as exc:
        die(str(exc))
    print(
        json.dumps(
            {
                "current": str(current),
                "next": str(nxt),
                "bump": kind,
                "since": since,
                "considered": len(messages),
            }
        )
    )
    return 0


def _expect_ok(title: str, body: str = "", labels: str = "") -> None:
    check_title(title, body=body, labels=labels)


def _expect_fail(title: str, snippet: str, body: str = "", labels: str = "") -> None:
    try:
        check_title(title, body=body, labels=labels)
    except ConventionError as exc:
        if snippet not in str(exc):
            raise AssertionError(
                f"{title!r} failed with {exc!r}, expected {snippet!r}"
            ) from exc
        return
    raise AssertionError(f"{title!r} should have been rejected")


def self_test() -> int:
    _expect_ok("fix(git-import): run git clone without the bundled libssl")
    _expect_ok("feat(session-import): show hidden directories in the folder picker")
    _expect_ok("chore(release): v2.1.0")
    _expect_ok("docs: document the commit convention")
    _expect_ok("fix: handle empty clone urls (#2)")
    _expect_ok(
        "feat(session-import)!: default the importer source to auto",
        body="BREAKING CHANGE: callers must pass the source explicitly.\n",
        labels="semver:major",
    )

    _expect_fail("Fix: uppercase type", "must match")
    _expect_fail("feat: Add an uppercase subject", "must match")
    _expect_fail("feat: trailing period.", "period")
    _expect_fail("unknown: not a type", "must match")
    _expect_fail("feat(WebUI): bad scope", "must match")
    _expect_fail(
        "feat(session-import): this subject is deliberately far too long for the 72 character limit",
        "maximum is 72",
    )
    _expect_fail(
        "feat(session-import)!: default the importer source to auto",
        "BREAKING CHANGE",
        body="no footer here",
        labels="semver:major",
    )
    _expect_fail(
        "feat(session-import)!: default the importer source to auto",
        MAJOR_LABEL,
        body="BREAKING CHANGE: callers must pass the source explicitly.\n",
        labels="bug",
    )

    current = Version(2, 5, 1)
    only_docs, kind = next_version(current, ["docs: mention the convention"])
    assert kind == "none" and str(only_docs) == "2.5.1", (only_docs, kind)

    patch, kind = next_version(
        current,
        [
            "docs: ignore me",
            "fix(git-import): run git clone without the bundled libssl",
            "perf: skip a redundant fs stat",
        ],
    )
    assert kind == "patch" and str(patch) == "2.5.2", (patch, kind)

    minor, kind = next_version(
        current,
        [
            "fix: a bug",
            "feat(webui): add a show-hidden toggle",
        ],
    )
    assert kind == "minor" and str(minor) == "2.6.0", (minor, kind)

    major, kind = next_version(
        current,
        [
            "feat(webui): add a show-hidden toggle",
            "feat(session-import)!: default the importer source to auto\n\n"
            + "BREAKING CHANGE: callers must pass the source explicitly.\n",
        ],
    )
    assert kind == "major" and str(major) == "3.0.0", (major, kind)

    body_only_major, kind = next_version(
        current,
        [
            "refactor: split the importer defaults\n\n"
            + "BREAKING CHANGE: the dropdown no longer defaults to cursor.\n",
        ],
    )
    assert kind == "major" and str(body_only_major) == "3.0.0", (body_only_major, kind)

    skipped, kind = next_version(current, ["Sync public CLI tree from main abcdef"])
    assert kind == "none" and str(skipped) == "2.5.1", (skipped, kind)

    print("commit-convention: self-test ok")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check Conventional Commit PR titles and compute the next SemVer."
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run embedded contract checks and exit.",
    )
    sub = parser.add_subparsers(dest="cmd")

    check = sub.add_parser("check-title", help="Validate a PR title.")
    check.add_argument("--title", help="PR title. Defaults to $PR_TITLE.")
    check.add_argument("--title-file", type=Path, help="Read the title from a file.")
    check.add_argument("--body", default="", help="PR body. Defaults to $PR_BODY.")
    check.add_argument("--body-file", type=Path, help="Read the PR body from a file.")
    check.add_argument(
        "--labels",
        default="",
        help="Comma or newline separated labels. Defaults to $PR_LABELS.",
    )
    check.set_defaults(func=cmd_check_title)

    nxt = sub.add_parser("next-version", help="Compute the next SemVer from git history.")
    nxt.add_argument("--current", help="Current X.Y.Z. Defaults to pyproject.toml.")
    nxt.add_argument(
        "--since",
        help="Git revision to start after. Defaults to the latest vX.Y.Z tag.",
    )
    nxt.add_argument(
        "--commits-file",
        type=Path,
        help="Blank-line-separated commit messages instead of git log.",
    )
    nxt.add_argument("--root", help="Repository root. Defaults to the repo containing this script.")
    nxt.set_defaults(func=cmd_next_version)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())
