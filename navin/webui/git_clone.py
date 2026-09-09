# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Clone a remote Git repository into a local project folder.

Used by the Dev / composer project picker ("Import from Git", Cursor-style):
the user pastes a URL, we shallow-clone into the Projects root (or an
explicit parent), then the WebUI opens and remembers the resulting path.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from navin.config.paths import is_navin_internal_path
from navin.utils.proc import no_window_kwargs

_GIT_CLONE_TIMEOUT_S = 300
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
# https://..., git@host:path, ssh://..., or a bare github.com/owner/repo /
# owner/repo shorthand (Cursor accepts the short forms).
_URL_RE = re.compile(
    r"^(?:https?://|git@|ssh://)"
    r"|^(?:github\.com|gitlab\.com|bitbucket\.org)/"
    r"|^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$",
    re.IGNORECASE,
)


class GitCloneError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def normalize_git_url(raw: str) -> str:
    """Turn a pasted link / shorthand into a cloneable URL."""
    value = (raw or "").strip()
    if not value:
        raise GitCloneError("missing git url")
    # Strip a trailing slash and optional query/fragment from browser copies.
    value = value.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    if value.startswith("git@"):
        return value
    if re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$", value):
        # owner/repo → assume GitHub, matching Cursor's paste shortcut.
        return f"https://github.com/{value}"
    if re.match(r"^(?:github|gitlab|bitbucket)\.com/", value, re.IGNORECASE):
        return f"https://{value}"
    if value.startswith(("http://", "https://", "ssh://")):
        return value
    raise GitCloneError(
        "git url must be https://..., git@..., ssh://..., or owner/repo"
    )


def repo_name_from_url(url: str) -> str:
    """Folder name derived from the last path segment of the URL."""
    if url.startswith("git@"):
        # git@host:owner/repo.git
        path = url.split(":", 1)[-1]
    else:
        parsed = urlparse(url)
        path = unquote(parsed.path or "")
    name = path.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[: -len(".git")]
    name = name.strip()
    if not name or not _REPO_NAME_RE.fullmatch(name):
        raise GitCloneError("could not derive a safe folder name from the url")
    return name


def _existing_remote_url(path: Path) -> str | None:
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "-C", str(path), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def _urls_equivalent(a: str, b: str) -> bool:
    def _canon(url: str) -> str:
        value = url.strip().rstrip("/")
        if value.endswith(".git"):
            value = value[: -len(".git")]
        if value.startswith("git@"):
            host, _, path = value.partition(":")
            host = host.removeprefix("git@")
            return f"{host.lower()}/{path.lower()}"
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        path = (parsed.path or "").strip("/").lower()
        return f"{host}/{path}"

    return _canon(a) == _canon(b)


def clone_git_project(
    url: str,
    *,
    parent: str | None = None,
    name: str | None = None,
    default_parent: str | None = None,
) -> dict[str, Any]:
    """Shallow-clone *url* under *parent* and return ``{path, name, url}``.

    If the target folder already exists and points at the same remote, the
    existing folder is reused (idempotent open). A non-empty conflicting
    folder raises :class:`GitCloneError`.
    """
    if not shutil.which("git"):
        raise GitCloneError("git is not installed on this machine", status=500)

    normalized = normalize_git_url(url)
    if not _URL_RE.search(normalized):
        raise GitCloneError("unsupported git url")

    folder_name = (name or "").strip() or repo_name_from_url(normalized)
    if not _REPO_NAME_RE.fullmatch(folder_name):
        raise GitCloneError("invalid folder name")

    parent_raw = (parent or "").strip() or (default_parent or "").strip()
    if not parent_raw:
        raise GitCloneError("missing destination folder")
    parent_path = Path(parent_raw).expanduser()
    try:
        parent_path = parent_path.resolve()
    except OSError as exc:
        raise GitCloneError(f"cannot resolve destination: {exc}") from exc
    if not parent_path.is_absolute():
        raise GitCloneError("destination must be an absolute path")
    if is_navin_internal_path(str(parent_path)):
        raise GitCloneError("cannot clone into Navin's internal storage")
    try:
        parent_path.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise GitCloneError(
            f"permission denied writing to {parent_path}", status=403
        ) from exc
    except OSError as exc:
        raise GitCloneError(f"cannot create destination: {exc}") from exc
    if not parent_path.is_dir():
        raise GitCloneError("destination is not a directory")

    target = parent_path / folder_name
    if target.exists():
        if not target.is_dir():
            raise GitCloneError(f"path already exists and is not a folder: {target}")
        existing = _existing_remote_url(target)
        if existing and _urls_equivalent(existing, normalized):
            return {
                "path": str(target),
                "name": folder_name,
                "url": normalized,
                "reused": True,
            }
        # Empty dir is fine to overwrite by cloning into it; anything else
        # would clobber user data.
        try:
            has_entries = any(target.iterdir())
        except OSError as exc:
            raise GitCloneError(f"cannot inspect existing folder: {exc}") from exc
        if has_entries:
            raise GitCloneError(
                f"folder already exists: {target}. "
                "Open it from Recent projects, or pick another name."
            )
        try:
            target.rmdir()
        except OSError as exc:
            raise GitCloneError(f"cannot prepare destination: {exc}") from exc

    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "clone", "--depth", "1", normalized, str(target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_CLONE_TIMEOUT_S,
            check=False,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        raise GitCloneError("git clone timed out", status=504) from exc
    except PermissionError as exc:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        raise GitCloneError(
            f"permission denied writing to {parent_path}", status=403
        ) from exc
    except OSError as exc:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        raise GitCloneError(f"git clone failed: {exc}", status=500) from exc

    if proc.returncode != 0:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        detail = (proc.stderr or proc.stdout or "").strip()[-400:]
        # Auth / network failures from git are client-facing, not opaque 500s.
        status = 502
        lower = detail.lower()
        if "permission denied" in lower or "could not read from remote" in lower:
            status = 403
        elif "not found" in lower or "repository not found" in lower:
            status = 404
        raise GitCloneError(
            f"git clone failed: {detail or f'exit {proc.returncode}'}",
            status=status,
        )

    return {
        "path": str(target),
        "name": folder_name,
        "url": normalized,
        "reused": False,
    }
