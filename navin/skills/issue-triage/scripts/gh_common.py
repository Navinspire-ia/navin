#!/usr/bin/env python3
"""Shared GitHub REST helper for the issue-triage scripts.

Transport, in order of preference:
  1. a token in GITHUB_TOKEN or GH_TOKEN (direct HTTPS with urllib),
  2. the `gh` CLI (`gh api`), which reuses its own login.

Every script imports this module from its own folder. Run it directly to
check which transport and repository would be used:

    gh_common.py [--repo owner/name]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import Any

API = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
MARKER = "<!-- navin:duplicate-notice -->"

Transport = Callable[[str, str, Any], Any]


class GitHubError(RuntimeError):
    pass


def _token_transport(token: str) -> Transport:
    def send(method: str, path: str, body: Any = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{API}{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("User-Agent", "navin-issue-triage")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                if exc.code in (403, 429) and attempt < 3:
                    wait = int(exc.headers.get("Retry-After") or 0)
                    reset = exc.headers.get("X-RateLimit-Reset")
                    if not wait and reset:
                        wait = max(0, int(reset) - int(time.time()))
                    if 0 < wait <= 300:
                        time.sleep(wait)
                        continue
                if exc.code == 404:
                    return None
                raise GitHubError(f"{method} {path}: HTTP {exc.code} {exc.read()[:300]!r}") from exc
        raise GitHubError(f"{method} {path}: rate limited")

    return send


def _no_window() -> dict[str, Any]:
    try:
        from navin.utils.proc import no_window_kwargs

        return dict(no_window_kwargs())
    except ImportError:
        return {"creationflags": 0x08000000} if os.name == "nt" else {}


def _gh_transport(method: str, path: str, body: Any = None) -> Any:
    cmd = ["gh", "api", "-X", method, "-H", "Accept: application/vnd.github+json", path.lstrip("/")]
    if body is not None:
        cmd += ["--input", "-"]
    proc = subprocess.run(
        cmd,
        input=json.dumps(body) if body is not None else None,
        capture_output=True,
        text=True,
        check=False,
        **_no_window(),
    )
    if proc.returncode != 0:
        if "Not Found" in proc.stderr or "HTTP 404" in proc.stderr:
            return None
        raise GitHubError(f"gh api {method} {path}: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def default_transport() -> Transport:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return _token_transport(token)
    if shutil.which("gh"):
        return _gh_transport
    raise GitHubError("no GITHUB_TOKEN / GH_TOKEN and no `gh` CLI found")


class Client:
    """Thin REST wrapper. Writes are skipped and logged unless apply=True."""

    def __init__(self, repo: str, *, apply: bool = False, transport: Transport | None = None) -> None:
        if repo.count("/") != 1:
            raise GitHubError(f"repo must be owner/name, got {repo!r}")
        self.repo = repo
        self.apply = apply
        self._send = transport
        self.writes: list[tuple[str, str, Any]] = []

    def _transport(self) -> Transport:
        if self._send is None:
            self._send = default_transport()
        return self._send

    def get(self, path: str) -> Any:
        return self._transport()("GET", path, None)

    def paginate(self, path: str, *, max_pages: int = 10) -> Iterator[dict[str, Any]]:
        sep = "&" if "?" in path else "?"
        for page in range(1, max_pages + 1):
            items = self.get(f"{path}{sep}per_page=100&page={page}") or []
            if not items:
                return
            yield from items
            if len(items) < 100:
                return

    def write(self, method: str, path: str, body: Any = None, *, why: str = "") -> Any:
        self.writes.append((method, path, body))
        if not self.apply:
            print(f"[dry-run] {method} {path} {why}".rstrip())
            return None
        return self._transport()(method, path, body)

    # Repository-relative helpers.
    def issue_path(self, number: int, tail: str = "") -> str:
        return f"/repos/{self.repo}/issues/{number}{tail}"


def detect_repo(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("GITHUB_REPOSITORY")
    if env:
        return env
    if shutil.which("gh"):
        proc = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            capture_output=True, text=True, check=False, **_no_window(),
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    raise GitHubError("pass --repo owner/name (or set GITHUB_REPOSITORY)")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def days_since(value: str, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    return (now - parse_time(value)).total_seconds() / 86400


def is_bot(user: dict[str, Any] | None) -> bool:
    if not user:
        return True
    return user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]")


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", help="owner/name (default: GITHUB_REPOSITORY or the current gh repo)")
    parser.add_argument("--apply", action="store_true", help="really write to GitHub (default: dry run)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show which GitHub transport and repository the triage scripts use.")
    parser.add_argument("--repo", help="owner/name")
    args = parser.parse_args(argv)
    try:
        transport = "token" if (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")) else (
            "gh CLI" if shutil.which("gh") else "none")
        print(f"transport: {transport}")
        print(f"repo: {detect_repo(args.repo)}")
    except GitHubError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
