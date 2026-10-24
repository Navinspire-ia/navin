# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""One credential bridge for every git subprocess Navin starts.

Import from Git, the agent's ``git`` tool, the board's auto-PR push and the
Code panel's Commit & Push each shelled out to ``git`` on their own. None of
them handed git the forge token the user saved in Settings > Git & GitHub,
and only one of them stopped git from waiting on a credential prompt nobody
could answer. A private repository therefore cloned in a terminal and failed
in the app, or hung until the timeout.

This module is the single place that knows how to:

* keep git non-interactive (``GIT_TERMINAL_PROMPT=0``, Git Credential
  Manager told never to open a window, SSH in ``BatchMode``), and
* supply the token for HTTPS remotes through environment-scoped git config
  (``GIT_CONFIG_COUNT`` / ``http.<url>.extraheader``). The token never
  appears on a command line (``ps`` shows argv, not the environment), in a
  remote URL, or in a file on disk.

The header is scoped to ``<scheme>://<host>/``, so a token for one forge is
never sent to another remote of the same repository.
"""

from __future__ import annotations

import base64
import os
import re
import shlex
import subprocess
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from navin.utils.proc import no_window_kwargs

SETTINGS_POINTER = "Settings > Git & GitHub"

GITHUB = "github"
GITLAB = "gitlab"
FORGEJO = "forgejo"
BITBUCKET = "bitbucket"
UNKNOWN = "unknown"

# The username each forge expects next to a token in HTTP Basic auth.
# GitHub app/OAuth tokens need ``x-access-token`` (PATs accept it too),
# GitLab OAuth tokens need ``oauth2`` (PATs accept any name), Bitbucket
# access tokens need ``x-token-auth``. Forgejo/Gitea read the user from the
# token itself, so the owner's login is used when known and ``git`` otherwise.
FORGE_USERNAMES: dict[str, str] = {
    GITHUB: "x-access-token",
    GITLAB: "oauth2",
    FORGEJO: "git",
    BITBUCKET: "x-token-auth",
}
_DEFAULT_USERNAME = "git"

_BITBUCKET_HOSTS = frozenset({"bitbucket.org", "www.bitbucket.org"})
_BITBUCKET_ENV = ("BITBUCKET_TOKEN", "BITBUCKET_ACCESS_TOKEN")

# Subcommands that talk to a remote and may need credentials.
NETWORK_SUBCOMMANDS = frozenset({"clone", "fetch", "pull", "push", "ls-remote", "submodule"})

_SSH_DEFAULT = "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
_REMOTE_LIST_TIMEOUT_S = 10

# Logins learned from the forge "current user" call (Settings > Git & GitHub
# accounts list). Forgejo prefers the token owner as the Basic username.
_known_logins: dict[str, str] = {}
_known_lock = threading.Lock()


def remember_login(host: str, login: str | None) -> None:
    name = (host or "").strip().lower()
    if not name or not login:
        return
    with _known_lock:
        _known_logins[name] = str(login)


def known_login(host: str) -> str:
    with _known_lock:
        return _known_logins.get((host or "").strip().lower(), "")


# ---------------------------------------------------------------------------
# Non-interactive environment
# ---------------------------------------------------------------------------


def ssh_command(existing: str | None) -> str:
    """``GIT_SSH_COMMAND`` that can never stop at a prompt.

    An existing ``ssh`` command keeps its own flags (an ``-i`` key, a port)
    and only gains the options it does not already set. A wrapper that is not
    ``ssh`` (plink, a custom script) is left exactly as it is: appending
    OpenSSH options to it would break it.
    """
    value = (existing or "").strip()
    if not value:
        return _SSH_DEFAULT
    try:
        parts = shlex.split(value, posix=os.name != "nt")
    except ValueError:
        return value
    if not parts:
        return _SSH_DEFAULT
    program = re.split(r"[\\/]", parts[0].strip("\"'"))[-1].lower()
    if program not in ("ssh", "ssh.exe"):
        return value
    lowered = value.lower()
    extra: list[str] = []
    if "batchmode" not in lowered:
        extra.append("-o BatchMode=yes")
    if "stricthostkeychecking" not in lowered:
        extra.append("-o StrictHostKeyChecking=accept-new")
    return " ".join([value, *extra]) if extra else value


def apply_noninteractive(env: dict[str, str]) -> dict[str, str]:
    """Make ``env`` safe for a git process with no terminal attached."""
    env["GIT_TERMINAL_PROMPT"] = "0"
    # Git Credential Manager (Windows, macOS) would otherwise open a sign-in
    # window from a background process and wait for it.
    env["GCM_INTERACTIVE"] = "never"
    if env.get("GIT_SSH") and not env.get("GIT_SSH_COMMAND"):
        # GIT_SSH_COMMAND would take precedence over the user's GIT_SSH.
        return env
    env["GIT_SSH_COMMAND"] = ssh_command(env.get("GIT_SSH_COMMAND"))
    return env


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GitCredential:
    """A token for one ``<scheme>://<host>[:port]/`` prefix."""

    scheme: str
    netloc: str
    host: str
    kind: str
    username: str
    source: str
    token: str = field(repr=False)

    @property
    def url_prefix(self) -> str:
        return f"{self.scheme}://{self.netloc}/"

    def header(self) -> str:
        raw = f"{self.username}:{self.token}".encode()
        return f"Authorization: Basic {base64.b64encode(raw).decode('ascii')}"


def http_target(url: str) -> tuple[str, str, int | None] | None:
    """``(scheme, host, port)`` of an HTTP(S) remote, else ``None``.

    SSH and scp-like remotes authenticate with keys, local paths need nothing,
    and a URL that already carries ``user[:pass]@`` is left to its own
    credentials rather than getting a second, conflicting header.
    """
    raw = (url or "").strip()
    if "://" not in raw:
        return None
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    if parts.username or parts.password:
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if (parts.scheme, port) in (("https", 443), ("http", 80)):
        port = None
    return parts.scheme, host, port


def host_of(url: str) -> str:
    """Hostname of any git remote (HTTPS, SSH, scp-like), or ``""``."""
    target = http_target(url)
    if target is not None:
        return target[1]
    from navin.webui.forge_api import parse_remote_url

    remote = parse_remote_url(url)
    if remote is not None:
        return remote.host
    match = re.match(r"^(?:[^@/\s]+@)?([A-Za-z0-9._-]+):", (url or "").strip())
    if match and "://" not in url:
        return match.group(1).lower()
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def forge_section(config: Any | None) -> Any | None:
    """``ForgeConfig`` out of a full Config, a ToolsConfig or itself."""
    if config is None:
        return None
    if hasattr(config, "tokens") and not hasattr(config, "forge"):
        return config
    forge = getattr(config, "forge", None)
    if forge is not None:
        return forge
    return getattr(getattr(config, "tools", None), "forge", None)


def as_full_config(config: Any | None, tokens: Mapping[str, str] | None = None) -> Any | None:
    """A ``config.tools.forge`` shaped object, which is what forge_api reads.

    ``tokens`` replaces the stored tokens (the agent tool snapshots them at
    creation); hosts pins are kept from ``config`` when there is one.
    """
    forge = forge_section(config)
    if tokens is None:
        if forge is None:
            return None
        if getattr(getattr(config, "tools", None), "forge", None) is forge:
            return config
        return SimpleNamespace(tools=SimpleNamespace(forge=forge))
    hosts = getattr(forge, "hosts", None) if forge is not None else None
    section = SimpleNamespace(
        tokens={str(k).strip().lower(): str(v) for k, v in dict(tokens).items()},
        hosts=dict(hosts) if isinstance(hosts, dict) else {},
    )
    return SimpleNamespace(tools=SimpleNamespace(forge=section))


def _load_config() -> Any | None:
    try:
        from navin.config.loader import load_config

        return load_config()
    except Exception:  # noqa: BLE001 - a broken config must not block git
        return None


def kind_for_host(host: str, *, config: Any | None = None) -> str:
    name = (host or "").strip().lower()
    if name in _BITBUCKET_HOSTS:
        return BITBUCKET
    from navin.webui.forge_api import kind_from_host

    return kind_from_host(name, config=config)


def username_for(kind: str, host: str) -> str:
    if kind == FORGEJO:
        return known_login(host) or FORGE_USERNAMES[FORGEJO]
    return FORGE_USERNAMES.get(kind, _DEFAULT_USERNAME)


def resolve_host_token(
    host: str,
    *,
    kind: str,
    config: Any | None = None,
    scheme: str = "https",
    port: int | None = None,
) -> tuple[str, str]:
    """``(token, source)`` for ``host``; source never contains the token."""
    from navin.webui import forge_api

    api_kind = kind if kind in forge_api.FORGE_KINDS else forge_api.UNKNOWN
    remote = forge_api.ForgeRemote(
        kind=api_kind, host=host, owner="_", repo="_", scheme=scheme, port=port
    )
    token, source = forge_api.resolve_token(remote, config=config)
    if not token and kind == BITBUCKET:
        for name in _BITBUCKET_ENV:
            value = (os.environ.get(name) or "").strip()
            if value:
                return value, f"env:{name}"
    return token, source


def credential_for_url(
    url: str,
    *,
    config: Any | None = None,
    tokens: Mapping[str, str] | None = None,
) -> GitCredential | None:
    """The token to send to ``url``, or ``None`` (SSH, local, no token).

    With neither ``config`` nor ``tokens`` given, the saved configuration is
    read, so a token added a second ago in Settings is used right away.
    """
    target = http_target(url)
    if target is None:
        return None
    scheme, host, port = target
    if config is None and tokens is None:
        config = _load_config()
    effective = as_full_config(config, tokens)
    kind = kind_for_host(host, config=effective)
    token, source = resolve_host_token(
        host, kind=kind, config=effective, scheme=scheme, port=port
    )
    if not token:
        return None
    return GitCredential(
        scheme=scheme,
        netloc=host if port is None else f"{host}:{port}",
        host=host,
        kind=kind,
        username=username_for(kind, host),
        source=source,
        token=token,
    )


def auth_config_env(
    credentials: Iterable[GitCredential],
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """``GIT_CONFIG_*`` entries carrying one scoped auth header per host.

    Appends after any ``GIT_CONFIG_COUNT`` the caller already exported, so an
    operator's own env-scoped config survives.
    """
    source = base or {}
    try:
        start = max(0, int(source.get("GIT_CONFIG_COUNT") or 0))
    except (TypeError, ValueError):
        start = 0
    out: dict[str, str] = {}
    index = start
    for credential in credentials:
        out[f"GIT_CONFIG_KEY_{index}"] = f"http.{credential.url_prefix}.extraheader"
        out[f"GIT_CONFIG_VALUE_{index}"] = credential.header()
        index += 1
    if index != start:
        out["GIT_CONFIG_COUNT"] = str(index)
    return out


def wsl_forward_names(env: Mapping[str, str], *, include_ssh: bool = True) -> list[str]:
    """Variables ``wsl.exe`` must import for the bridge to work in a distro."""
    names = ["GIT_TERMINAL_PROMPT", "GCM_INTERACTIVE"]
    if include_ssh and env.get("GIT_SSH_COMMAND"):
        names.append("GIT_SSH_COMMAND")
    if env.get("GIT_CONFIG_COUNT"):
        names.append("GIT_CONFIG_COUNT")
        try:
            count = int(env["GIT_CONFIG_COUNT"])
        except ValueError:
            count = 0
        for index in range(count):
            for prefix in ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"):
                if f"{prefix}{index}" in env:
                    names.append(f"{prefix}{index}")
    return names


def merge_wslenv(existing: str | None, names: Iterable[str]) -> str:
    current = [part for part in (existing or "").split(":") if part]
    bare = {part.split("/", 1)[0] for part in current}
    for name in names:
        if name not in bare:
            current.append(name)
            bare.add(name)
    return ":".join(current)


def git_env(
    urls: Iterable[str] = (),
    *,
    config: Any | None = None,
    tokens: Mapping[str, str] | None = None,
    base: Mapping[str, str] | None = None,
    wsl: bool = False,
    credentials: Iterable[GitCredential | None] | None = None,
) -> dict[str, str]:
    """Full environment for a git process that may reach ``urls``.

    Non-interactive in every case; carries a scoped auth header for each
    HTTP(S) host a token is known for. ``wsl=True`` adds the ``WSLENV``
    entries so the values cross into a WSL distribution. Pass already
    resolved ``credentials`` to skip the lookup.
    """
    env = dict(os.environ if base is None else base)
    user_ssh = bool(env.get("GIT_SSH_COMMAND"))
    apply_noninteractive(env)
    wanted = [url for url in urls if http_target(url) is not None]
    if credentials is not None:
        credentials = [item for item in credentials if item is not None]
        wanted = []
    else:
        credentials = []
    if wanted:
        if config is None and tokens is None:
            config = _load_config()
            if config is None:
                tokens = {}
        seen: set[str] = set()
        for url in wanted:
            credential = credential_for_url(url, config=config, tokens=tokens)
            if credential is None or credential.url_prefix in seen:
                continue
            seen.add(credential.url_prefix)
            credentials.append(credential)
    env.update(auth_config_env(credentials, env))
    if wsl:
        # A Windows-side GIT_SSH_COMMAND names a Windows binary; only ours is
        # meaningful inside the distribution.
        env["WSLENV"] = merge_wslenv(
            env.get("WSLENV"), wsl_forward_names(env, include_ssh=not user_ssh)
        )
    return env


# ---------------------------------------------------------------------------
# Repository helpers
# ---------------------------------------------------------------------------


def is_network_command(args: Iterable[str]) -> bool:
    for arg in args:
        text = str(arg)
        if text.startswith("-"):
            continue
        return text in NETWORK_SUBCOMMANDS
    return False


def repo_remote_urls(root: Path | str) -> list[str]:
    """Fetch and push URLs of every remote of the repository at ``root``."""
    from navin.utils.git_argv import git_argv

    argv = git_argv(root)
    if argv is None:
        return []
    try:
        completed = subprocess.run(  # noqa: S603
            [*argv, "config", "--get-regexp", r"^remote\..*\.(push)?url$"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_REMOTE_LIST_TIMEOUT_S,
            check=False,
            env=apply_noninteractive(dict(os.environ)),
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    urls: list[str] = []
    for line in (completed.stdout or "").splitlines():
        _key, _, value = line.strip().partition(" ")
        value = value.strip()
        if value and value not in urls:
            urls.append(value)
    return urls


def git_env_for_repo(
    root: Path | str,
    *,
    config: Any | None = None,
    tokens: Mapping[str, str] | None = None,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """``git_env`` for a network command run inside an existing repository."""
    from navin.utils.git_argv import git_route

    route = git_route(root)
    urls = repo_remote_urls(root) if route is not None else []
    return git_env(
        urls,
        config=config,
        tokens=tokens,
        base=base,
        wsl=bool(route is not None and route.distro),
    )


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


_SSH_AUTH_MARKERS = (
    "permission denied (publickey",
    "host key verification failed",
    "no supported authentication methods",
)
_AUTH_MARKERS = (
    "authentication failed",
    "could not read username",
    "could not read password",
    "terminal prompts disabled",
    "invalid username or password",
    "invalid credentials",
    "http basic: access denied",
    "bad credentials",
    "requested url returned error: 401",
    "error: 401",
)
_FORBIDDEN_MARKERS = (
    "requested url returned error: 403",
    "error: 403",
    "permission to ",
    "you are not allowed",
    "access denied",
    "permission denied",
)
_NOT_FOUND_MARKERS = (
    "repository not found",
    "project not found",
    "does not appear to be a git repository",
    "requested url returned error: 404",
    "error: 404",
)
_NETWORK_MARKERS = (
    "could not resolve host",
    "failed to connect",
    "connection timed out",
    "connection refused",
    "network is unreachable",
    "operation timed out",
    "couldn't connect to server",
)


def classify_git_failure(text: str) -> str | None:
    """``"ssh_auth" | "auth" | "forbidden" | "not_found" | "network"`` or None."""
    lowered = (text or "").lower()
    if not lowered:
        return None
    if any(marker in lowered for marker in _SSH_AUTH_MARKERS):
        return "ssh_auth"
    if any(marker in lowered for marker in _AUTH_MARKERS):
        return "auth"
    if any(marker in lowered for marker in _NOT_FOUND_MARKERS):
        return "not_found"
    if any(marker in lowered for marker in _FORBIDDEN_MARKERS):
        return "forbidden"
    if any(marker in lowered for marker in _NETWORK_MARKERS):
        return "network"
    return None


def auth_hint(text: str, *, host: str = "") -> str:
    """One sentence telling the user how to fix a git auth failure, or ``""``."""
    kind = classify_git_failure(text)
    where = host or "the remote"
    if kind == "auth":
        return (
            f"{where} needs credentials. Connect the account or add a token for "
            f"{where} in {SETTINGS_POINTER}."
        )
    if kind == "ssh_auth":
        return (
            f"The SSH key on this machine is not accepted by {where}. Add the "
            "public key to the account, or use the HTTPS URL with a token from "
            f"{SETTINGS_POINTER}."
        )
    if kind == "forbidden":
        return (
            f"The account used for {where} cannot write or read this repository. "
            f"Check the token scopes in {SETTINGS_POINTER}."
        )
    return ""
