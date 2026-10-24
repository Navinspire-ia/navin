# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Clone a remote Git repository into a local project folder.

Used by the Dev / composer project picker ("Import from Git", Cursor-style):
the user pastes a URL, we shallow-clone into the Projects root (or an
explicit parent), then the WebUI opens and remembers the resulting path.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from navin.config.paths import is_navin_internal_path
from navin.utils import wsl
from navin.utils.git_argv import git_argv
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
    """``code`` / ``host`` are set for failures the UI can act on
    (``auth_required`` opens Settings > Git & GitHub for ``host``)."""

    def __init__(
        self,
        message: str,
        status: int = 400,
        *,
        code: str = "",
        host: str = "",
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.host = host
        self.detail = detail

    def payload(self) -> dict[str, Any]:
        """JSON body for coded failures: ``{error, message, code, host, detail}``."""
        return {
            "error": self.message,
            "message": self.message,
            "code": self.code,
            "host": self.host,
            "detail": self.detail,
        }


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


def _host_git_env() -> dict[str, str]:
    """Parent env minus the PyInstaller library paths.

    The frozen desktop build prepends
    ``/usr/lib/Navin/navin-dist/_internal`` to ``LD_LIBRARY_PATH``; a child
    ``git-remote-https`` then loads the bundled ``libssl.so.3`` (OpenSSL
    3.0) while the host ``libcurl`` needs ``OPENSSL_3.2.0``/``3.5.0`` and
    aborts. Git is a host binary: it must use host libraries.
    """
    env = dict(os.environ)
    marker = "navin-dist"
    for key in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        value = env.get(key)
        if not value or marker not in value:
            continue
        kept = [
            part
            for part in value.split(os.pathsep)
            if part and marker not in part and "_internal" not in part
        ]
        if kept:
            env[key] = os.pathsep.join(kept)
        else:
            env.pop(key, None)
    return env


def _clone_failure(
    raw: str,
    *,
    returncode: int,
    host: str,
    credential: Any | None,
) -> GitCloneError:
    """Map git's stderr to an actionable, coded error (like explain_push_failure)."""
    from navin.utils.git_credentials import SETTINGS_POINTER, classify_git_failure
    from navin.webui.forge_api import redact_url_credentials

    raw = redact_url_credentials(raw or "")
    # Keep the head (the first error, e.g. the OPENSSL symbol dump) and the
    # tail (the final "fatal:" line): a plain [-400:] slice hid the beginning
    # of the failure behind truncated middle lines.
    if len(raw) <= 400:
        detail = raw
    else:
        detail = f"{raw[:200]}\n...\n{raw[-200:]}"
    where = host or "the server"
    kind = classify_git_failure(detail)
    if kind == "auth":
        if credential is not None:
            message = (
                f"{where} rejected the saved token. Update it in "
                f"{SETTINGS_POINTER}, then try again."
            )
        else:
            message = (
                f"{where} needs you to sign in to clone this repository. Connect "
                f"your account or add a token for {where} in {SETTINGS_POINTER}, "
                "then try again."
            )
        return GitCloneError(message, status=401, code="auth_required", host=host, detail=detail)
    if kind == "ssh_auth":
        return GitCloneError(
            f"The SSH key on this machine is not accepted by {where}. Add your "
            "public key to the account, or clone the HTTPS URL with a token "
            f"from {SETTINGS_POINTER}.",
            status=401,
            code="auth_required",
            host=host,
            detail=detail,
        )
    if kind == "forbidden":
        return GitCloneError(
            f"Your {where} account cannot read this repository. Check the token "
            f"scopes in {SETTINGS_POINTER}.",
            status=403,
            code="forbidden",
            host=host,
            detail=detail,
        )
    if kind == "not_found":
        message = f"Repository not found on {where}."
        if credential is None:
            message += (
                f" If it is private, add a token for {where} in {SETTINGS_POINTER}."
            )
        return GitCloneError(message, status=404, code="not_found", host=host, detail=detail)
    if kind == "network":
        return GitCloneError(
            f"Cannot reach {where}. Check the network connection or proxy.",
            status=502,
            code="network",
            host=host,
            detail=detail,
        )
    return GitCloneError(
        f"git clone failed: {detail or f'exit {returncode}'}",
        status=502,
        host=host,
        detail=detail,
    )


def _existing_remote_url(path: Path) -> str | None:
    try:
        proc = subprocess.run(  # noqa: S603
            [*(git_argv(path) or ["git", "-C", str(path)]), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            env=_host_git_env(),
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
    config: Any | None = None,
) -> dict[str, Any]:
    """Shallow-clone *url* under *parent* and return ``{path, name, url}``.

    If the target folder already exists and points at the same remote, the
    existing folder is reused (idempotent open). A non-empty conflicting
    folder raises :class:`GitCloneError`. A forge token for the URL's host
    (Settings > Git & GitHub, env, or a signed-in ``gh``) is handed to git
    through the environment, never on the command line.
    """
    if not shutil.which("git") and not (sys.platform == "win32" and wsl.wsl_executable()):
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

    clone_argv = ["git", "clone", "--depth", "1", normalized, str(target)]
    location = wsl.parse_unc(str(target)) if sys.platform == "win32" else None
    via_wsl = False
    if location is not None and wsl.wsl_executable():
        # Clone into the WSL folder with the distro's git: Windows git over
        # \\wsl.localhost is slow and leaves CRLF / permission noise.
        distro = wsl.resolve_distro(location.distro) or location.distro
        clone_argv = [*wsl.command_prefix(distro), "git", "clone", "--depth", "1", normalized, location.posix]
        via_wsl = True
    elif not shutil.which("git"):
        raise GitCloneError("git is not installed on this machine", status=500)

    from navin.utils import git_credentials

    host = git_credentials.host_of(normalized)
    try:
        credential = git_credentials.credential_for_url(normalized, config=config)
    except Exception:  # noqa: BLE001 - a token lookup must not block a public clone
        credential = None
    clone_env = git_credentials.git_env(
        base=_host_git_env(), wsl=via_wsl, credentials=[credential]
    )
    try:
        proc = subprocess.run(  # noqa: S603
            clone_argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_CLONE_TIMEOUT_S,
            check=False,
            env=clone_env,
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
        # Auth / network failures from git are client-facing, not opaque 500s.
        raise _clone_failure(
            (proc.stderr or proc.stdout or "").strip(),
            returncode=proc.returncode,
            host=host,
            credential=credential,
        )

    return {
        "path": str(target),
        "name": folder_name,
        "url": normalized,
        "reused": False,
    }
