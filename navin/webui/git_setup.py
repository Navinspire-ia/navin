# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Is git on this machine ready for the account the user just connected?

A token saved in Settings > Git & GitHub is half of what a clone, a commit and
a push need. The machine also needs:

* a git that reads the token the way Navin hands it over (environment-scoped
  config, which ``GIT_CONFIG_COUNT`` only gained in git 2.31),
* a commit identity (``user.name`` / ``user.email``), without which the first
  commit from the Code panel fails with "Please tell me who you are",
* for the user's own terminal, a credential helper, which each OS provides
  differently: Git for Windows ships Git Credential Manager, macOS has the
  keychain helper, Linux distributions usually configure nothing at all,
* remotes that the stored token can actually authenticate (HTTPS), or an SSH
  key for the SSH ones.

This module detects all of it *where the project's git runs*: on the host, or
inside the WSL distribution of a ``\\\\wsl.localhost`` project opened from
Windows. Once an account is connected it fixes what is safe to fix on its own
(a missing commit identity, taken from the forge account; never overwriting a
value the user set), and reports the rest with the exact command for the
current OS. Writing the token to the system credential store or switching a
remote to HTTPS only happens on an explicit request. No answer ever carries a
token.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from navin.quality import wsl_route
from navin.utils import git_credentials, wsl
from navin.utils.git_argv import git_route
from navin.utils.proc import no_window_kwargs

_GIT_TIMEOUT_S = 10
_MAX_SSH_KEYS = 12
_MAX_REMOTES = 12

# GIT_CONFIG_COUNT / GIT_CONFIG_KEY_n arrived in git 2.31: an older git ignores
# the scoped auth header, so every private HTTPS remote fails in the app.
MIN_BRIDGE_VERSION = (2, 31)

WINDOWS = "windows"
MACOS = "macos"
LINUX = "linux"

# Helpers that keep the secret in an OS store or in memory only.
SECURE_HELPERS = frozenset(
    {"manager", "osxkeychain", "libsecret", "wincred", "gnome-keyring", "cache", "gh"}
)
# ``store`` writes ~/.git-credentials in clear text.
PLAINTEXT_HELPERS = frozenset({"store"})

_WSL_GCM = "/mnt/c/Program\\ Files/Git/mingw64/bin/git-credential-manager.exe"
_REMOTE_NAME = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


class GitSetupError(Exception):
    def __init__(self, message: str, *, status: int = 400, code: str = "git_setup_error") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------


def os_id(system: str | None = None) -> str:
    value = (system if system is not None else sys.platform).lower()
    if value.startswith("win"):
        return WINDOWS
    if value == "darwin":
        return MACOS
    return LINUX


def _linux_pretty_name(path: Path = Path("/etc/os-release")) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "PRETTY_NAME":
            return value.strip().strip('"').strip("'")
    return ""


def platform_info(system: str | None = None) -> dict[str, Any]:
    """``{os, label, arch, wsl}`` of the machine the engine runs on."""
    name = os_id(system)
    release = platform.release() or ""
    if name == WINDOWS:
        label = f"Windows {release}".strip()
    elif name == MACOS:
        version = platform.mac_ver()[0]
        label = f"macOS {version}" if version else "macOS"
    else:
        label = _linux_pretty_name() or f"Linux {release}".strip()
    inside_wsl = name == LINUX and (
        bool(os.environ.get("WSL_DISTRO_NAME")) or "microsoft" in release.lower()
    )
    return {"os": name, "label": label, "arch": platform.machine() or "", "wsl": inside_wsl}


# ---------------------------------------------------------------------------
# Where git runs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GitTarget:
    """How to reach the git that serves ``root`` (or the host git without one).

    ``argv`` ends with ``git`` (plus ``-C root`` on the host); ``shell`` is the
    prefix that runs any other program in the same place, set only inside a
    WSL distribution. ``argv`` is ``None`` when no git is reachable.
    """

    argv: tuple[str, ...] | None
    distro: str | None = None
    shell: tuple[str, ...] | None = None
    root: str | None = None


def resolve_target(root: Path | str | None = None) -> GitTarget:
    text = str(root or "").strip()
    if text:
        route = git_route(text)
        if route is not None and route.distro:
            return GitTarget(
                argv=tuple(route.argv),
                distro=route.distro,
                shell=tuple(wsl.command_prefix(route.distro)),
                root=text,
            )
        if route is not None and Path(text).is_dir():
            return GitTarget(argv=tuple(route.argv), root=text)
    git = shutil.which("git")
    return GitTarget(argv=(git,) if git else None)


def _run(
    argv: tuple[str, ...] | list[str],
    *args: str,
    input_text: str | None = None,
    timeout: float = _GIT_TIMEOUT_S,
) -> subprocess.CompletedProcess[str] | None:
    argv_full = [*argv, *args]
    try:
        completed = subprocess.run(  # argv list, never a shell string
            argv_full,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=git_credentials.apply_noninteractive(dict(os.environ)),
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        wsl_route.note_launch(argv_full, timed_out=True)
        return None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    wsl_route.note_launch(argv_full, timed_out=False, completed=completed)
    return completed


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION.search(text or "")
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def _git_os(target: GitTarget, host_os: str) -> str:
    return LINUX if target.distro else host_os


def _config_get(target: GitTarget, key: str) -> tuple[str | None, str | None]:
    """``(value, scope)`` of ``key`` as git sees it; scope may be ``None``."""
    if target.argv is None:
        return None, None
    out = _run(target.argv, "config", "--show-scope", "--get", key)
    if out is not None and out.returncode == 0:
        lines = [line for line in (out.stdout or "").splitlines() if line.strip()]
        line = lines[-1] if lines else ""
        scope, sep, value = line.partition("\t")
        if sep:
            return value.strip() or None, scope.strip() or None
        return line.strip() or None, None
    if out is not None and out.returncode == 1:
        return None, None
    # git older than 2.26 has no --show-scope.
    out = _run(target.argv, "config", "--get", key)
    if out is not None and out.returncode == 0:
        return (out.stdout or "").strip() or None, None
    return None, None


def helper_name(value: str) -> str:
    """Family of a ``credential.helper`` value: manager, osxkeychain, store, ..."""
    lowered = (value or "").strip().lower()
    if not lowered:
        return ""
    if lowered.startswith("!"):
        # ``!gh auth git-credential`` is what ``gh auth setup-git`` writes.
        program = re.split(r"[\\/]", lowered[1:].split()[0] if lowered[1:].split() else "")[-1]
        if program.removesuffix(".exe") == "gh" and "git-credential" in lowered:
            return "gh"
        return "custom"
    for name in ("manager", "osxkeychain", "libsecret", "wincred", "gnome-keyring"):
        if name in lowered:
            return name
    first = lowered.split()[0]
    base = re.split(r"[\\/]", first)[-1]
    base = base.removeprefix("git-credential-").removesuffix(".exe")
    if base in ("cache", "store", "oauth"):
        return base
    return "custom"


def _helpers(target: GitTarget) -> list[str]:
    """The effective ``credential.helper`` chain (an empty value resets it)."""
    if target.argv is None:
        return []
    out = _run(target.argv, "config", "--get-all", "credential.helper")
    if out is None or out.returncode != 0:
        return []
    chain: list[str] = []
    for line in (out.stdout or "").splitlines():
        if not line.strip():
            chain = []
        else:
            chain.append(line.strip())
    return chain


def _libsecret_available(target: GitTarget) -> bool:
    if target.argv is None or target.distro:
        return False
    out = _run(target.argv, "--exec-path")
    if out is None or out.returncode != 0:
        return False
    exec_path = Path((out.stdout or "").strip())
    return (exec_path / "git-credential-libsecret").is_file()


def recommended_helper(git_os: str, *, in_wsl: bool, libsecret: bool) -> dict[str, str]:
    """``{helper, command, note}``: the secure helper this OS should use."""
    if git_os == WINDOWS:
        return {
            "helper": "manager",
            "command": "git config --global credential.helper manager",
            "note": "Git Credential Manager ships with Git for Windows.",
        }
    if git_os == MACOS:
        return {
            "helper": "osxkeychain",
            "command": "git config --global credential.helper osxkeychain",
            "note": "Stores the token in the macOS Keychain.",
        }
    if in_wsl:
        return {
            "helper": "manager",
            "command": f'git config --global credential.helper "{_WSL_GCM}"',
            "note": "Reuses Git Credential Manager from Git for Windows.",
        }
    if libsecret:
        return {
            "helper": "libsecret",
            "command": "git config --global credential.helper libsecret",
            "note": "Stores the token in the desktop keyring.",
        }
    return {
        "helper": "cache",
        "command": "git config --global credential.helper 'cache --timeout=3600'",
        "note": "Keeps the token in memory for an hour.",
    }


def _package_manager() -> str:
    for name in ("apt-get", "dnf", "yum", "pacman", "zypper", "apk"):
        if shutil.which(name):
            return name
    return ""


def install_hint(git_os: str, *, upgrade: bool = False, in_wsl_target: bool = False) -> dict[str, str]:
    """``{command, url}`` that installs (or upgrades) git on this OS."""
    if git_os == WINDOWS:
        # The same unattended install Navin runs on its own at startup.
        verb = "upgrade" if upgrade else "install"
        return {
            "command": (
                f"winget {verb} --id Git.Git -e --silent "
                "--accept-package-agreements --accept-source-agreements"
            ),
            "url": "https://git-scm.com/download/win",
        }
    if git_os == MACOS:
        brew = bool(shutil.which("brew"))
        command = ("brew upgrade git" if upgrade else "brew install git") if brew else "xcode-select --install"
        return {"command": command, "url": "https://git-scm.com/download/mac"}
    manager = "apt-get" if in_wsl_target else _package_manager()
    commands = {
        "apt-get": "sudo apt-get update && sudo apt-get install -y git",
        "dnf": "sudo dnf install -y git",
        "yum": "sudo yum install -y git",
        "pacman": "sudo pacman -S --needed git",
        "zypper": "sudo zypper install -y git",
        "apk": "sudo apk add git",
    }
    return {
        "command": commands.get(manager, commands["apt-get"]),
        "url": "https://git-scm.com/download/linux",
    }


def _ssh_keys(target: GitTarget) -> list[str]:
    """File names of the public keys in ~/.ssh (never their content)."""
    if target.shell:
        out = _run(target.shell, "sh", "-c", 'ls -1 "$HOME"/.ssh/*.pub 2>/dev/null')
        if out is None:
            return []
        names = [line.strip().rsplit("/", 1)[-1] for line in (out.stdout or "").splitlines()]
        return sorted({name for name in names if name.endswith(".pub")})[:_MAX_SSH_KEYS]
    try:
        folder = Path.home() / ".ssh"
        return sorted(path.name for path in folder.glob("*.pub") if path.is_file())[:_MAX_SSH_KEYS]
    except (OSError, RuntimeError):
        return []


def https_equivalent(url: str) -> str | None:
    """``https://host/owner/repo.git`` for an SSH remote, else ``None``."""
    from navin.webui.forge_api import parse_remote_url

    raw = (url or "").strip()
    if not raw or remote_protocol(raw) != "ssh":
        return None
    remote = parse_remote_url(raw)
    if remote is None:
        return None
    return f"https://{remote.host}/{remote.owner}/{remote.repo}.git"


def remote_protocol(url: str) -> str:
    raw = (url or "").strip()
    if "://" in raw:
        scheme = raw.split("://", 1)[0].lower()
        if scheme in ("http", "https"):
            return "https"
        if scheme in ("ssh", "git+ssh", "ssh+git"):
            return "ssh"
        if scheme == "file":
            return "local"
        return scheme
    if re.match(r"^(?:[^@/\s]+@)?[A-Za-z0-9._-]+:[^\\/]", raw) and not re.match(r"^[A-Za-z]:", raw):
        return "ssh"
    return "local"


def _project_sign_in(url: str, root: Path | str | None, config: Any | None) -> str:
    """``git`` or ``gh`` when the project's own git is already signed in for ``url``.

    A WSL project pushed for months with ``store`` or ``gh`` showed "no token
    yet" and a Connect button: only Navin's own tokens were looked at. The
    lookup is the one the forge API uses (cached per project), so the panel
    and the agent agree on what this project can reach.
    """
    from navin.webui import forge_api

    if root is None:
        return ""
    remote = forge_api.parse_remote_url(url)
    if remote is None:
        return ""
    remote = forge_api.resolve_kind(remote, config=config, allow_probe=False)
    token, source = forge_api.repo_sign_in_token(remote, root)
    return source if token else ""


def _remotes(
    target: GitTarget,
    *,
    config: Any | None,
    ssh_keys: list[str],
    root: Path | str | None = None,
) -> list[dict[str, Any]]:
    from navin.webui.forge_api import redact_url_credentials

    if target.argv is None or target.root is None:
        return []
    out = _run(target.argv, "config", "--get-regexp", r"^remote\..*\.url$")
    if out is None or out.returncode != 0:
        return []
    remotes: list[dict[str, Any]] = []
    for line in (out.stdout or "").splitlines():
        key, _, url = line.strip().partition(" ")
        url = url.strip()
        if not key.startswith("remote.") or not key.endswith(".url") or not url:
            continue
        name = key[len("remote.") : -len(".url")]
        protocol = remote_protocol(url)
        host = git_credentials.host_of(url) if protocol in ("https", "ssh") else ""
        if protocol == "https":
            if git_credentials.http_target(url) is None:
                auth = "embedded"
            else:
                credential = git_credentials.credential_for_url(url, config=config, tokens=None if config else {})
                if credential is not None:
                    auth = "token"
                else:
                    auth = _project_sign_in(url, root, config) or "none"
        elif protocol == "ssh":
            auth = "ssh_key" if ssh_keys else "none"
        else:
            auth = "local"
        remotes.append(
            {
                "name": name,
                "url": redact_url_credentials(url),
                "protocol": protocol,
                "host": host,
                "auth": auth,
                "https_url": https_equivalent(url),
            }
        )
        if len(remotes) >= _MAX_REMOTES:
            break
    return remotes


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _load_config(config: Any | None) -> Any | None:
    if config is not None:
        return config
    try:
        from navin.config.loader import load_config

        return load_config()
    except (OSError, ValueError):
        # A broken config still gets a report, only without the stored tokens.
        return None


def _check(check_id: str, level: str, params: dict[str, Any] | None = None, fix: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": check_id, "level": level, "params": params or {}, "fix": fix}


def machine_report(root: Path | str | None = None, *, config: Any | None = None) -> dict[str, Any]:
    """Everything the Git settings and the Dev Git panel show about this machine.

    ``checks`` is the ordered list the UI renders: ``{id, level, params, fix}``
    with ``level`` in ok / info / warn / error and ``fix`` either a command to
    copy for this OS or an action the engine can run.
    """
    host = platform_info()
    target = resolve_target(root)
    git_os = _git_os(target, host["os"])
    in_wsl = bool(target.distro) or bool(host["wsl"])
    checks: list[dict[str, Any]] = []

    version_text: str | None = None
    version: tuple[int, int, int] | None = None
    if target.argv is not None:
        out = _run(target.argv, "--version")
        if out is not None and out.returncode == 0:
            version_text = (out.stdout or "").strip().removeprefix("git version ").strip() or None
            version = parse_version(version_text or "")
    available = version_text is not None
    bridge_ok = bool(version and version[:2] >= MIN_BRIDGE_VERSION)
    git_info = {
        "available": available,
        "version": version_text,
        "path": (target.argv[0] if target.argv and not target.distro else None),
        "distro": target.distro,
        "bridge_ok": bridge_ok if available else False,
        "min_version": ".".join(str(part) for part in MIN_BRIDGE_VERSION),
    }

    if not available:
        hint = install_hint(git_os, in_wsl_target=bool(target.distro))
        checks.append(
            _check(
                "git_missing",
                "error",
                {"os": git_os, "distro": target.distro},
                {"command": hint["command"], "url": hint["url"]},
            )
        )
        return {
            "platform": host,
            "git": git_info,
            "identity": {"name": None, "email": None, "name_scope": None, "email_scope": None},
            "credential_helper": {"helpers": [], "active": "", "secure": False,
                                  "recommended": recommended_helper(git_os, in_wsl=in_wsl, libsecret=False)},
            "ssh": {"keys": []},
            "gh": {"available": bool(shutil.which("gh"))},
            "remotes": [],
            "checks": checks,
            "root": target.root,
        }

    if bridge_ok:
        checks.append(_check("git_ok", "ok", {"version": version_text, "distro": target.distro}))
    else:
        hint = install_hint(git_os, upgrade=True, in_wsl_target=bool(target.distro))
        checks.append(
            _check(
                "git_old",
                "warn",
                {"version": version_text, "min": git_info["min_version"]},
                {"command": hint["command"], "url": hint["url"]},
            )
        )

    name, name_scope = _config_get(target, "user.name")
    email, email_scope = _config_get(target, "user.email")
    missing = [label for label, value in (("name", name), ("email", email)) if not value]
    if missing:
        checks.append(_check("identity_missing", "warn", {"missing": missing}, {"action": "identity"}))
    else:
        checks.append(_check("identity_ok", "ok", {"name": name, "email": email}))

    helpers = _helpers(target)
    active = helper_name(helpers[-1]) if helpers else ""
    families = [helper_name(value) for value in helpers]
    secure = any(family in SECURE_HELPERS for family in families)
    recommended = recommended_helper(
        git_os, in_wsl=in_wsl, libsecret=git_os == LINUX and _libsecret_available(target)
    )
    if any(family in PLAINTEXT_HELPERS for family in families):
        checks.append(
            _check("helper_plaintext", "warn", {"helper": "store"}, {"command": recommended["command"]})
        )
    elif not helpers:
        checks.append(
            _check(
                "helper_missing",
                "info",
                {"recommended": recommended["helper"]},
                {"command": recommended["command"]},
            )
        )
    else:
        checks.append(_check("helper_ok", "ok", {"helper": active or families[-1]}))

    cfg = _load_config(config)
    keys = _ssh_keys(target)
    remotes = _remotes(target, config=cfg, ssh_keys=keys, root=root)
    for remote in remotes:
        params = {"remote": remote["name"], "host": remote["host"], "auth": remote["auth"]}
        if remote["protocol"] == "https" and remote["auth"] == "none":
            checks.append(_check("remote_no_token", "warn", params, {"action": "connect", "host": remote["host"]}))
        elif remote["protocol"] == "ssh" and remote["auth"] == "none":
            fix = (
                {"action": "use_https", "remote": remote["name"], "url": remote["https_url"]}
                if remote["https_url"]
                else {"command": 'ssh-keygen -t ed25519 -C "you@example.com"'}
            )
            checks.append(_check("remote_ssh_no_key", "warn", params, fix))
        elif remote["protocol"] in ("https", "ssh"):
            checks.append(_check("remote_ok", "ok", params))

    return {
        "platform": host,
        "git": git_info,
        "identity": {"name": name, "email": email, "name_scope": name_scope, "email_scope": email_scope},
        "credential_helper": {
            "helpers": [helper_name(value) or value for value in helpers],
            "active": active,
            "secure": secure,
            "recommended": recommended,
        },
        "ssh": {"keys": keys},
        "gh": {"available": bool(shutil.which("gh"))},
        "remotes": remotes,
        "checks": checks,
        "root": target.root,
    }


# ---------------------------------------------------------------------------
# Fixes
# ---------------------------------------------------------------------------


def _clean_identity(name: str | None, email: str | None) -> tuple[str | None, str | None]:
    clean_name = (name or "").strip() or None
    clean_email = (email or "").strip() or None
    if clean_name is not None:
        if len(clean_name) > 200 or any(ch in clean_name for ch in "\r\n\0<>") or clean_name.startswith("-"):
            raise GitSetupError("This name cannot be used as a git author name.", code="bad_name")
    if clean_email is not None:
        if (
            len(clean_email) > 254
            or "@" not in clean_email
            or any(ch.isspace() for ch in clean_email)
            or any(ch in clean_email for ch in "\0<>")
            or clean_email.startswith("-")
        ):
            raise GitSetupError("This is not a valid email address.", code="bad_email")
    return clean_name, clean_email


def set_identity(
    name: str | None,
    email: str | None,
    *,
    root: Path | str | None = None,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Write ``user.name`` / ``user.email`` to the global git config of the target.

    ``overwrite=False`` only fills what is missing. Returns ``{ok, name, email,
    changed, distro, error}``.
    """
    clean_name, clean_email = _clean_identity(name, email)
    target = resolve_target(root)
    if target.argv is None:
        raise GitSetupError("git is not installed on this machine.", status=503, code="git_missing")
    current_name, _ = _config_get(target, "user.name")
    current_email, _ = _config_get(target, "user.email")
    changed: list[str] = []
    for key, value, current in (
        ("user.name", clean_name, current_name),
        ("user.email", clean_email, current_email),
    ):
        if value is None or value == current or (current and not overwrite):
            continue
        out = _run(target.argv, "config", "--global", key, value)
        if out is None or out.returncode != 0:
            detail = ((out.stderr or out.stdout) if out is not None else "git did not answer").strip()
            return {
                "ok": False,
                "name": current_name,
                "email": current_email,
                "changed": changed,
                "distro": target.distro,
                "error": f"git config {key} failed: {detail[:300]}",
            }
        changed.append(key)
    final_name, _ = _config_get(target, "user.name")
    final_email, _ = _config_get(target, "user.email")
    return {
        "ok": True,
        "name": final_name,
        "email": final_email,
        "changed": changed,
        "distro": target.distro,
        "error": None,
    }


def identity_for_account(kind: str, host: str, login: str | None, profile: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Commit name and email the forge account stands for.

    The email is the one the forge itself would put on a web commit: the
    public / commit address when there is one, else the forge's no-reply
    address, so pushes are not refused for exposing a private email.
    """
    from navin.webui import forge_api

    data = profile or {}
    login = (login or "").strip() or None
    name = (str(data.get("name") or "").strip() or login) or None
    email = str(data.get("email") or "").strip() or None
    account_id = str(data.get("id") or "").strip()
    host = (host or "").strip().lower()
    if email is None and login and host:
        if kind == forge_api.GITHUB and account_id:
            email = f"{account_id}+{login}@users.noreply.{host}"
        elif kind == forge_api.GITLAB and account_id:
            email = f"{account_id}-{login}@users.noreply.{host}"
        elif kind == forge_api.FORGEJO:
            email = f"{login}@noreply.{host}"
    return name, email


_GITHUB_WANTED = ("repo", "workflow", "read:org")
_GITHUB_IMPLIES = {"read:org": ("read:org", "write:org", "admin:org")}


def missing_scopes(kind: str, scopes: list[str] | None) -> list[str] | None:
    """Scopes a token lacks for clone + push + pull requests; ``None`` = unknown."""
    from navin.webui import forge_api

    if scopes is None:
        return None
    have = {scope.strip().lower() for scope in scopes}
    if kind == forge_api.GITHUB:
        return [
            scope for scope in _GITHUB_WANTED
            if not any(alias in have for alias in _GITHUB_IMPLIES.get(scope, (scope,)))
        ]
    if kind == forge_api.GITLAB:
        return [] if "api" in have else ["api"]
    return None


def setup_account(host: str, *, root: Path | str | None = None, config: Any | None = None) -> dict[str, Any]:
    """Test the account for ``host`` and get git ready to use it.

    ``{ok, host, kind, login, scopes, error, actions, machine}``. ``actions``
    lists what was checked or done: the commit identity (filled from the
    account only where git has none) and the token scopes.
    """
    from navin.webui import git_accounts

    cfg = _load_config(config)
    info = git_accounts.account_info(host, cfg, fresh=True)
    actions: list[dict[str, Any]] = []
    target = resolve_target(root)
    if info["ok"]:
        actions.append(_identity_action(target, info, root=root))
        missing = missing_scopes(info["kind"], info["scopes"])
        if missing:
            actions.append({"id": "scopes", "status": "missing", "missing": missing})
        elif missing is not None:
            actions.append({"id": "scopes", "status": "ok", "scopes": info["scopes"]})
    return {
        "ok": info["ok"],
        "host": info["host"],
        "kind": info["kind"],
        "login": info["login"],
        "scopes": info["scopes"],
        "error": info["error"],
        "actions": actions,
        "machine": machine_report(root, config=cfg),
    }


def _identity_action(target: GitTarget, info: dict[str, Any], *, root: Path | str | None) -> dict[str, Any]:
    if target.argv is None:
        return {"id": "identity", "status": "unavailable"}
    current_name, _ = _config_get(target, "user.name")
    current_email, _ = _config_get(target, "user.email")
    if current_name and current_email:
        return {"id": "identity", "status": "already", "name": current_name, "email": current_email}
    want_name, want_email = identity_for_account(
        info["kind"], info["host"], info["login"], info.get("profile")
    )
    fill_name = None if current_name else want_name
    fill_email = None if current_email else want_email
    if not fill_name and not fill_email:
        return {"id": "identity", "status": "needs_input", "name": current_name, "email": current_email}
    try:
        result = set_identity(fill_name, fill_email, root=root, overwrite=False)
    except GitSetupError as exc:
        return {"id": "identity", "status": "failed", "error": exc.message}
    if not result["ok"]:
        return {"id": "identity", "status": "failed", "error": result["error"]}
    status = "configured" if result["name"] and result["email"] else "needs_input"
    return {
        "id": "identity",
        "status": status,
        "name": result["name"],
        "email": result["email"],
        "changed": result["changed"],
        "distro": result["distro"],
    }


def store_in_helper(host: str, *, root: Path | str | None = None, config: Any | None = None) -> dict[str, Any]:
    """Hand the stored token to the system credential helper (terminal git).

    Only into a helper that keeps it in an OS store or memory: never into the
    clear-text ``store`` helper. The token travels on stdin, never argv.
    """
    from navin.webui import git_accounts

    name = git_accounts.clean_host(host)
    target = resolve_target(root)
    if target.argv is None:
        raise GitSetupError("git is not installed on this machine.", status=503, code="git_missing")
    helpers = _helpers(target)
    families = [helper_name(value) for value in helpers]
    if not helpers:
        raise GitSetupError(
            "No credential helper is configured for git on this machine.",
            status=409,
            code="no_helper",
        )
    if any(family in PLAINTEXT_HELPERS for family in families):
        raise GitSetupError(
            "The configured credential helper stores secrets in clear text; "
            "switch to the recommended helper first.",
            status=409,
            code="plaintext_helper",
        )
    cfg = _load_config(config)
    credential = git_credentials.credential_for_url(
        f"https://{name}/", config=cfg, tokens=None if cfg is not None else {}
    )
    if credential is None:
        raise GitSetupError(
            f"No token for {name}. Add one in {git_credentials.SETTINGS_POINTER}.",
            status=404,
            code="no_token",
        )
    payload = (
        f"protocol={credential.scheme}\nhost={credential.netloc}\n"
        f"username={credential.username}\npassword={credential.token}\n\n"
    )
    out = _run(target.argv, "credential", "approve", input_text=payload)
    if out is None or out.returncode != 0:
        detail = ((out.stderr or out.stdout) if out is not None else "git did not answer").strip()
        raise GitSetupError(
            f"git credential approve failed: {detail[:300]}", status=502, code="helper_failed"
        )
    return {"ok": True, "host": name, "helper": families[-1] if families else "", "distro": target.distro}


def use_https_remote(remote: str, *, root: Path | str) -> dict[str, Any]:
    """Point an SSH remote at its HTTPS URL so the stored token authenticates it."""
    name = (remote or "").strip()
    if not _REMOTE_NAME.match(name) or name.startswith("-"):
        raise GitSetupError("invalid remote name", code="bad_remote")
    target = resolve_target(root)
    if target.argv is None or target.root is None:
        raise GitSetupError("git is not available for this project.", status=503, code="git_missing")
    changed: dict[str, str] = {}
    for key, push in ((f"remote.{name}.url", False), (f"remote.{name}.pushurl", True)):
        value, _scope = _config_get(target, key)
        if not value:
            if not push:
                raise GitSetupError(f"remote {name} not found", status=404, code="no_remote")
            continue
        https = https_equivalent(value)
        if https is None:
            if not push:
                raise GitSetupError(
                    f"remote {name} is not an SSH URL Navin can convert", status=409, code="not_ssh"
                )
            continue
        args = ["remote", "set-url", *(["--push"] if push else []), name, https]
        out = _run(target.argv, *args)
        if out is None or out.returncode != 0:
            detail = ((out.stderr or out.stdout) if out is not None else "git did not answer").strip()
            raise GitSetupError(f"git remote set-url failed: {detail[:300]}", status=409, code="set_url_failed")
        changed["push_url" if push else "url"] = https
    return {"ok": True, "remote": name, **changed}
