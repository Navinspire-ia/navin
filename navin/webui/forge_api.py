# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Pull / merge requests on any forge: GitHub, GitLab, Forgejo (Gitea).

The Code panel used to shell out to ``gh``, so a machine without the GitHub
CLI answered "gh CLI not installed" and a GitLab or Forgejo remote was never
supported at all. Everything here is plain REST over ``httpx`` with a token:
no external binary to install, identical behaviour on macOS, Windows, Linux
and WSL. ``gh`` stays a *token source* when it happens to be installed and
signed in, so GitHub users who already rely on it keep working untouched.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from navin.utils.proc import no_window_kwargs
from navin.webui.project_search import ProjectSearchError

GITHUB = "github"
GITLAB = "gitlab"
FORGEJO = "forgejo"
UNKNOWN = "unknown"

FORGE_KINDS: tuple[str, ...] = (GITHUB, GITLAB, FORGEJO)

FORGE_LABELS = {
    GITHUB: "GitHub",
    GITLAB: "GitLab",
    FORGEJO: "Forgejo",
    UNKNOWN: "unknown",
}

# GitLab calls it a merge request everywhere, including in its own API errors.
REQUEST_LABELS = {
    GITHUB: "pull request",
    GITLAB: "merge request",
    FORGEJO: "pull request",
    UNKNOWN: "pull request",
}

_HTTP_TIMEOUT_S = 20.0
_PROBE_TIMEOUT_S = 6.0
_GH_TOKEN_TIMEOUT_S = 10
_PROBE_TTL_S = 900.0
_MAX_TITLE = 200
_MAX_BODY = 4000


class ForgeError(ProjectSearchError):
    """A forge operation failed in a way the user can act on.

    ``code`` is stable and machine-readable so the WebUI can show a
    translated, actionable message instead of forwarding raw API prose.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int = 409,
        code: str = "forge_error",
        host: str = "",
        forge: str = UNKNOWN,
    ) -> None:
        super().__init__(message, status=status)
        self.code = code
        self.host = host
        self.forge = forge


# ---------------------------------------------------------------------------
# Remote parsing
# ---------------------------------------------------------------------------


# git@host:owner/repo.git - the scp-like syntax urlsplit cannot parse.
# A leading backslash in the path is refused so "C:\repos\x" stays a path.
_SCP_LIKE = re.compile(r"^(?:[^@/\s]+@)?(?P<host>[A-Za-z0-9._-]+):(?P<path>[^\\/].*)$")

_CREDENTIALS_IN_URL = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]+@")


def redact_url_credentials(text: str) -> str:
    """Strip ``user:token@`` out of any URL so logs never carry a secret."""
    return _CREDENTIALS_IN_URL.sub(lambda m: f"{m.group('scheme')}", text or "")


@dataclass(frozen=True)
class ForgeRemote:
    """Where ``origin`` points, normalized for API use."""

    kind: str
    host: str
    owner: str
    repo: str
    scheme: str = "https"
    port: int | None = None

    @property
    def netloc(self) -> str:
        if self.port is None:
            return self.host
        return f"{self.host}:{self.port}"

    @property
    def base(self) -> str:
        return f"{self.scheme}://{self.netloc}"

    @property
    def slug(self) -> str:
        """``owner/repo``, keeping GitLab subgroups (``group/sub/repo``)."""
        return f"{self.owner}/{self.repo}"

    @property
    def url(self) -> str:
        return f"{self.base}/{self.slug}"

    @property
    def api_base(self) -> str:
        if self.kind == GITHUB:
            if self.host in ("github.com", "www.github.com"):
                return "https://api.github.com"
            # GitHub Enterprise Server.
            return f"{self.base}/api/v3"
        if self.kind == GITLAB:
            return f"{self.base}/api/v4"
        return f"{self.base}/api/v1"

    def with_kind(self, kind: str) -> "ForgeRemote":
        if kind == self.kind:
            return self
        return ForgeRemote(
            kind=kind,
            host=self.host,
            owner=self.owner,
            repo=self.repo,
            scheme=self.scheme,
            port=self.port,
        )


def parse_remote_url(url: str) -> ForgeRemote | None:
    """Turn a git remote into ``ForgeRemote`` (kind left ``unknown``).

    Understands ``https://host/owner/repo.git``, ``http://`` for self-hosted
    plain HTTP, ``ssh://git@host:2222/owner/repo.git`` and the scp-like
    ``git@host:owner/repo.git``. Embedded credentials are dropped, the
    trailing ``.git`` is removed, and GitLab subgroups stay in ``owner``.
    Local paths and unsupported transports return ``None``.
    """
    raw = (url or "").strip()
    if not raw:
        return None

    scheme = "https"
    port: int | None = None
    if "://" in raw:
        parts = urlsplit(raw)
        if parts.scheme in ("http", "https"):
            scheme = parts.scheme
            try:
                port = parts.port
            except ValueError:
                port = None
        elif parts.scheme in ("ssh", "git", "git+ssh"):
            # The SSH port says nothing about where the web API listens.
            scheme = "https"
        else:
            return None
        host = (parts.hostname or "").lower()
        path = parts.path or ""
    else:
        match = _SCP_LIKE.match(raw)
        if match is None:
            return None
        host = match.group("host").lower()
        path = match.group("path")

    if port in (80, 443):
        port = None

    cleaned = path.strip("/")
    if cleaned.lower().endswith(".git"):
        cleaned = cleaned[: -len(".git")]
    cleaned = cleaned.strip("/")
    if not host:
        return None
    if "." not in host and host != "localhost":
        return None
    if "/" not in cleaned:
        return None
    owner, _, repo = cleaned.rpartition("/")
    if not owner or not repo:
        return None
    return ForgeRemote(kind=UNKNOWN, host=host, owner=owner, repo=repo, scheme=scheme, port=port)


# ---------------------------------------------------------------------------
# References: issue / request URLs, and "which repository" strings
# ---------------------------------------------------------------------------


ISSUE = "issue"
REQUEST = "request"

# The path segment each forge puts before the number. GitHub uses /pull/,
# Forgejo /pulls/, GitLab /-/merge_requests/.
_REF_MARKERS = {
    "issues": ISSUE,
    "issue": ISSUE,
    "pull": REQUEST,
    "pulls": REQUEST,
    "merge_requests": REQUEST,
}


@dataclass(frozen=True)
class ForgeRef:
    """One issue or pull/merge request, as stored on a board task."""

    remote: ForgeRemote
    number: int
    target: str = ISSUE

    @property
    def is_request(self) -> bool:
        return self.target == REQUEST


def parse_ref_url(url: str) -> ForgeRef | None:
    """Turn an issue / PR / MR web URL into ``ForgeRef`` (kind ``unknown``).

    Handles ``/owner/repo/issues/7`` (GitHub, Forgejo),
    ``/owner/repo/pull/7`` (GitHub), ``/owner/repo/pulls/7`` (Forgejo) and
    ``/group/sub/repo/-/merge_requests/7`` (GitLab, subgroups included).
    """
    raw = (url or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"https://{raw}"
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if not host or ("." not in host and host != "localhost"):
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port in (80, 443):
        port = None

    segments = [segment for segment in (parts.path or "").split("/") if segment]
    for index in range(len(segments) - 2, -1, -1):
        target = _REF_MARKERS.get(segments[index].lower())
        if target is None:
            continue
        number_raw = segments[index + 1]
        if not number_raw.isdigit():
            continue
        slug_segments = segments[:index]
        # GitLab inserts a literal "-" between the project and the resource.
        if slug_segments and slug_segments[-1] == "-":
            slug_segments = slug_segments[:-1]
        if len(slug_segments) < 2:
            return None
        remote = ForgeRemote(
            kind=UNKNOWN,
            host=host,
            owner="/".join(slug_segments[:-1]),
            repo=slug_segments[-1],
            scheme=parts.scheme,
            port=port,
        )
        return ForgeRef(remote=remote, number=int(number_raw), target=target)
    return None


# ---------------------------------------------------------------------------
# Forge kind detection
# ---------------------------------------------------------------------------


_KNOWN_HOSTS = {
    "github.com": GITHUB,
    "www.github.com": GITHUB,
    "ssh.github.com": GITHUB,
    "gitlab.com": GITLAB,
    "www.gitlab.com": GITLAB,
    "salsa.debian.org": GITLAB,
    "invent.kde.org": GITLAB,
    "codeberg.org": FORGEJO,
    "gitea.com": FORGEJO,
    "next.forgejo.org": FORGEJO,
    "disroot.org": FORGEJO,
}

_HOST_LABELS = {
    "github": GITHUB,
    "gitlab": GITLAB,
    "forgejo": FORGEJO,
    "gitea": FORGEJO,
    "codeberg": FORGEJO,
}

# host -> (kind, expiry). A probe crosses the network; a self-hosted host does
# not change flavour between two clicks on Create PR.
_probe_cache: dict[str, tuple[str, float]] = {}


def _configured_host_kinds(config: Any | None) -> dict[str, str]:
    forge = getattr(getattr(config, "tools", None), "forge", None)
    hosts = getattr(forge, "hosts", None)
    if not isinstance(hosts, dict):
        return {}
    out: dict[str, str] = {}
    for host, kind in hosts.items():
        name = str(host or "").strip().lower()
        flavour = str(kind or "").strip().lower()
        if name and flavour in FORGE_KINDS:
            out[name] = flavour
    return out


def kind_from_host(host: str, *, config: Any | None = None) -> str:
    """Forge flavour from the hostname alone, config override first.

    Never guesses from a substring: ``forgejo.navinspire.ai`` matches on the
    ``forgejo`` label, ``my-github-mirror.example.com`` does not.
    """
    name = (host or "").strip().lower()
    if not name:
        return UNKNOWN
    override = _configured_host_kinds(config).get(name)
    if override:
        return override
    if name in _KNOWN_HOSTS:
        return _KNOWN_HOSTS[name]
    for label in name.split("."):
        flavour = _HOST_LABELS.get(label)
        if flavour:
            return flavour
    return UNKNOWN


def _probe_endpoint(base: str, path: str) -> bool:
    """True when the endpoint exists, even behind auth.

    A 401/403 still proves the API is there: GitLab's ``/api/v4/version``
    refuses anonymous callers but only GitLab answers on that path.
    """
    try:
        response = httpx.get(
            f"{base}{path}",
            timeout=_PROBE_TIMEOUT_S,
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )
    except (httpx.HTTPError, OSError, ValueError):
        return False
    return response.status_code in (200, 401, 403)


def probe_kind(remote: ForgeRemote) -> str:
    """Ask the host what it runs when the name gives nothing away."""
    cached = _probe_cache.get(remote.host)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0]
    found = UNKNOWN
    # Forgejo/Gitea first: its version endpoint is public, so it answers
    # without a token and is the common self-hosted case.
    for kind, path in (
        (FORGEJO, "/api/v1/version"),
        (GITLAB, "/api/v4/version"),
        (GITHUB, "/api/v3/meta"),
    ):
        if _probe_endpoint(remote.base, path):
            found = kind
            break
    _probe_cache[remote.host] = (found, now + _PROBE_TTL_S)
    return found


def clear_probe_cache() -> None:
    _probe_cache.clear()


def _origin_url(root: Path) -> str:
    from navin.webui.project_search import _run_git

    result = _run_git(root, "remote", "get-url", "origin")
    if result is None or result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def detect_forge(
    root: Path,
    *,
    config: Any | None = None,
    allow_probe: bool = True,
) -> ForgeRemote | None:
    """Which forge hosts ``origin`` for the repository at ``root``."""
    remote = parse_remote_url(_origin_url(root))
    if remote is None:
        return None
    return resolve_kind(remote, config=config, allow_probe=allow_probe)


def resolve_kind(
    remote: ForgeRemote,
    *,
    config: Any | None = None,
    allow_probe: bool = True,
) -> ForgeRemote:
    """Fill in the forge flavour of a remote parsed from a URL."""
    if remote.kind != UNKNOWN:
        return remote
    kind = kind_from_host(remote.host, config=config)
    if kind == UNKNOWN and allow_probe:
        kind = probe_kind(remote)
    return remote.with_kind(kind)


def resolve_ref(
    url: str,
    *,
    config: Any | None = None,
    allow_probe: bool = True,
) -> ForgeRef | None:
    """``ForgeRef`` with its forge flavour resolved, from a stored URL."""
    ref = parse_ref_url(url)
    if ref is None:
        return None
    remote = resolve_kind(ref.remote, config=config, allow_probe=allow_probe)
    return ForgeRef(remote=remote, number=ref.number, target=ref.target)


def parse_repo_ref(ref: str, *, origin: ForgeRemote | None = None) -> ForgeRemote | None:
    """Which repository a "issues repo" setting points at.

    Accepts what the panel stores: a bare ``owner/name`` (read on the same
    host as ``origin``, so a Forgejo project keeps talking to its own
    server), a ``host/owner/name`` reference, or a full URL / SSH remote.
    """
    text = str(ref or "").strip().strip("/")
    if not text:
        return origin
    if "://" in text or text.startswith("git@") or "@" in text.split("/")[0]:
        parsed = parse_remote_url(text)
        if parsed is None:
            return None
        return _same_host_kind(parsed, origin)
    if text.lower().endswith(".git"):
        text = text[: -len(".git")]
    segments = [segment for segment in text.split("/") if segment]
    if len(segments) < 2:
        return None
    head = segments[0].lower()
    host, _, port_raw = head.partition(":")
    if "." in host or host == "localhost":
        if len(segments) < 3:
            return None
        port: int | None = None
        if port_raw:
            if not port_raw.isdigit():
                return None
            port = int(port_raw)
            if port in (80, 443):
                port = None
        remote = ForgeRemote(
            kind=UNKNOWN,
            host=host,
            owner="/".join(segments[1:-1]),
            repo=segments[-1],
            # A port always means a self-hosted server reached over plain
            # HTTP in practice only when the origin says so; default to https.
            scheme="https",
            port=port,
        )
        return _same_host_kind(remote, origin)
    if origin is not None:
        return ForgeRemote(
            kind=origin.kind,
            host=origin.host,
            owner="/".join(segments[:-1]),
            repo=segments[-1],
            scheme=origin.scheme,
            port=origin.port,
        )
    # No remote to borrow from: a bare slug historically meant github.com.
    return ForgeRemote(
        kind=GITHUB,
        host="github.com",
        owner="/".join(segments[:-1]),
        repo=segments[-1],
    )


def _same_host_kind(remote: ForgeRemote, origin: ForgeRemote | None) -> ForgeRemote:
    """Borrow scheme/port/kind from ``origin`` when it is the same server."""
    if origin is None or origin.host != remote.host:
        return remote
    return ForgeRemote(
        kind=remote.kind if remote.kind != UNKNOWN else origin.kind,
        host=remote.host,
        owner=remote.owner,
        repo=remote.repo,
        scheme=origin.scheme,
        port=origin.port,
    )


def resolve_repo_remote(
    root: Path,
    repo_ref: str | None = None,
    *,
    config: Any | None = None,
    allow_probe: bool = True,
) -> ForgeRemote | None:
    """The remote issues should be read from: ``repo_ref`` or ``origin``."""
    origin = detect_forge(root, config=config, allow_probe=allow_probe)
    if not (repo_ref or "").strip():
        return origin
    remote = parse_repo_ref(str(repo_ref), origin=origin)
    if remote is None:
        return None
    return resolve_kind(remote, config=config, allow_probe=allow_probe)


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


_ENV_BY_KIND: dict[str, tuple[str, ...]] = {
    GITHUB: ("GITHUB_TOKEN", "GH_TOKEN"),
    GITLAB: ("GITLAB_TOKEN", "GL_TOKEN", "GLAB_TOKEN"),
    FORGEJO: (
        "FORGEJO_TOKEN",
        "FORGEJO_ACCESS_TOKEN",
        "GITEA_TOKEN",
        "GITEA_SERVER_TOKEN",
        "GITEA_ACCESS_TOKEN",
        "GITEA_HTTP_TOKEN",
        "TEA_TOKEN",
    ),
}

_NON_ENV_CHAR = re.compile(r"[^A-Z0-9]+")


def host_env_var(host: str) -> str:
    """Per-host env var, so two Forgejo servers can hold different tokens."""
    slug = _NON_ENV_CHAR.sub("_", (host or "").upper()).strip("_")
    return f"NAVIN_FORGE_TOKEN_{slug}" if slug else ""


def env_var_names(kind: str, host: str) -> list[str]:
    """Every env var consulted for this host, most specific first."""
    names: list[str] = []
    per_host = host_env_var(host)
    if per_host:
        names.append(per_host)
    names.extend(_ENV_BY_KIND.get(kind, ()))
    return names


def _configured_tokens(config: Any | None) -> dict[str, str]:
    forge = getattr(getattr(config, "tools", None), "forge", None)
    tokens = getattr(forge, "tokens", None)
    if not isinstance(tokens, dict):
        return {}
    out: dict[str, str] = {}
    for host, token in tokens.items():
        name = str(host or "").strip().lower()
        value = str(token or "").strip()
        if name and value:
            out[name] = value
    return out


def gh_cli_token(host: str) -> str:
    """Token from a signed-in ``gh``, so existing GitHub setups just work."""
    if shutil.which("gh") is None:
        return ""
    try:
        completed = subprocess.run(  # noqa: S603
            ["gh", "auth", "token", "--hostname", host or "github.com"],  # noqa: S607
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GH_TOKEN_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return (completed.stdout or "").strip()


def resolve_token(remote: ForgeRemote, *, config: Any | None = None) -> tuple[str, str]:
    """``(token, source)`` for this remote, most explicit source first.

    ``source`` is ``"settings"``, ``"env:NAME"``, ``"gh"`` or ``""``. It is
    safe to show in the UI: it names where the token came from, never the
    token itself.
    """
    from_config = _configured_tokens(config).get(remote.host)
    if from_config:
        return from_config, "settings"
    for name in env_var_names(remote.kind, remote.host):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value, f"env:{name}"
    if remote.kind == GITHUB:
        token = gh_cli_token(remote.host)
        if token:
            return token, "gh"
    return "", ""


def token_missing_error(remote: ForgeRemote) -> ForgeError:
    """The one error users actually hit, phrased so it can be fixed."""
    envs = " / ".join(env_var_names(remote.kind, remote.host)) or "a forge token"
    return ForgeError(
        f"No API token for {remote.host}. Add one in Settings > Git "
        f"(forge tokens) or set {envs}.",
        status=401,
        code="forge_token_missing",
        host=remote.host,
        forge=remote.kind,
    )


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _headers(kind: str, token: str) -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "navin-code"}
    if not token:
        return headers
    if kind == GITHUB:
        headers["Authorization"] = f"Bearer {token}"
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    elif kind == GITLAB:
        headers["PRIVATE-TOKEN"] = token
    else:
        headers["Authorization"] = f"token {token}"
    return headers


def _api_message(payload: Any, text: str) -> str:
    """The forge's own explanation, without echoing a whole HTML error page."""
    if isinstance(payload, dict):
        for key in ("message", "error", "error_description", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                for key in ("message", "code"):
                    value = first.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
            elif isinstance(first, str):
                return first.strip()
    if isinstance(payload, list) and payload and isinstance(payload[0], str):
        return payload[0].strip()
    stripped = (text or "").strip()
    if stripped.startswith("<"):
        return ""
    return stripped


def _request(
    method: str,
    url: str,
    *,
    kind: str,
    token: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, Any, str]:
    """One REST call. Redirects are never followed: a 3xx off-host would
    hand the token to whatever the redirect points at."""
    try:
        response = httpx.request(
            method,
            url,
            headers=_headers(kind, token),
            params=params,
            json=json_body,
            timeout=_HTTP_TIMEOUT_S,
            follow_redirects=False,
        )
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise ForgeError(
            f"cannot reach {urlsplit(url).hostname or 'the forge'}: "
            f"{redact_url_credentials(str(exc))[:200]}",
            status=502,
            code="forge_unreachable",
            host=urlsplit(url).hostname or "",
            forge=kind,
        ) from exc
    text = response.text or ""
    try:
        payload = response.json() if text.strip() else None
    except ValueError:
        payload = None
    return response.status_code, payload, text


def _raise_for_status(
    remote: ForgeRemote,
    status: int,
    payload: Any,
    text: str,
    *,
    action: str,
) -> None:
    if 200 <= status < 300:
        return
    detail = redact_url_credentials(_api_message(payload, text))[:300]
    suffix = f": {detail}" if detail else ""
    label = FORGE_LABELS.get(remote.kind, remote.kind)
    if status == 401:
        raise ForgeError(
            f"{remote.host} rejected the token (401). Update it in "
            f"Settings > Git{suffix}",
            status=401,
            code="forge_token_invalid",
            host=remote.host,
            forge=remote.kind,
        )
    if status == 403:
        raise ForgeError(
            f"The token for {remote.host} is missing the scope needed to "
            f"{action} (403). On {label} it needs write access to "
            f"{remote.slug}{suffix}",
            status=403,
            code="forge_token_forbidden",
            host=remote.host,
            forge=remote.kind,
        )
    if status == 404:
        raise ForgeError(
            f"{remote.slug} not found on {remote.host} (404). Either the "
            f"repository path is wrong or the token cannot see it{suffix}",
            status=404,
            code="forge_repo_not_found",
            host=remote.host,
            forge=remote.kind,
        )
    if status in (400, 409, 422):
        raise ForgeError(
            f"{label} refused to {action}{suffix or ': validation failed'}",
            status=409,
            code="forge_rejected",
            host=remote.host,
            forge=remote.kind,
        )
    raise ForgeError(
        f"{label} returned HTTP {status} while trying to {action}{suffix}",
        status=502,
        code="forge_http_error",
        host=remote.host,
        forge=remote.kind,
    )


def _project_id(remote: ForgeRemote) -> str:
    """GitLab addresses a project by URL-encoded ``group/sub/repo``."""
    return quote(remote.slug, safe="")


# ---------------------------------------------------------------------------
# Checks / CI
# ---------------------------------------------------------------------------


_PASSING = {"success", "successful", "skipped", "neutral", "manual", "passed"}
_FAILING = {
    "failure",
    "failed",
    "error",
    "cancelled",
    "canceled",
    "timed_out",
    "action_required",
}


def summarize_checks(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold normalized ``{name, state, url}`` rows into the panel summary."""
    rows: list[dict[str, Any]] = []
    passing = failing = pending = 0
    for item in items[:40]:
        if not isinstance(item, dict):
            continue
        state = str(item.get("state") or "unknown").strip().lower()
        rows.append(
            {
                "name": str(item.get("name") or "check"),
                "state": state,
                "url": item.get("url"),
            }
        )
        if state in _PASSING:
            passing += 1
        elif state in _FAILING:
            failing += 1
        else:
            pending += 1
    if failing:
        overall = "failure"
    elif pending:
        overall = "pending"
    elif passing:
        overall = "success"
    else:
        overall = "unknown"
    return {
        "state": overall,
        "total": len(rows),
        "passing": passing,
        "failing": failing,
        "pending": pending,
        "items": rows,
    }


def empty_checks() -> dict[str, Any]:
    return summarize_checks([])


# ---------------------------------------------------------------------------
# Normalization of one pull / merge request
# ---------------------------------------------------------------------------


def _normalize_github_pr(data: dict[str, Any]) -> dict[str, Any]:
    head = data.get("head") if isinstance(data.get("head"), dict) else {}
    base = data.get("base") if isinstance(data.get("base"), dict) else {}
    return {
        "url": data.get("html_url") or data.get("url"),
        "number": data.get("number"),
        "title": data.get("title") or "",
        "state": str(data.get("state") or ""),
        "is_draft": bool(data.get("draft")),
        "head": (head or {}).get("ref") or "",
        "base": (base or {}).get("ref") or "",
    }


def _normalize_gitlab_mr(data: dict[str, Any]) -> dict[str, Any]:
    title = str(data.get("title") or "")
    return {
        "url": data.get("web_url"),
        "number": data.get("iid") or data.get("id"),
        "title": title,
        "state": str(data.get("state") or ""),
        "is_draft": bool(
            data.get("draft")
            or data.get("work_in_progress")
            or title.lower().startswith("draft:")
        ),
        "head": data.get("source_branch") or "",
        "base": data.get("target_branch") or "",
    }


def _normalize_forgejo_pr(data: dict[str, Any]) -> dict[str, Any]:
    head = data.get("head") if isinstance(data.get("head"), dict) else {}
    base = data.get("base") if isinstance(data.get("base"), dict) else {}
    title = str(data.get("title") or "")
    return {
        "url": data.get("html_url") or data.get("url"),
        "number": data.get("number") or data.get("id"),
        "title": title,
        "state": str(data.get("state") or ""),
        # Forgejo has no draft flag on the REST payload; it marks a draft the
        # way Gitea's UI does, with a WIP title prefix.
        "is_draft": bool(data.get("draft")) or title.upper().startswith("WIP:"),
        "head": (head or {}).get("ref") or "",
        "base": (base or {}).get("ref") or "",
    }


def normalize_pr(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    if kind == GITLAB:
        return _normalize_gitlab_mr(data)
    if kind == GITHUB:
        return _normalize_github_pr(data)
    return _normalize_forgejo_pr(data)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def default_branch(remote: ForgeRemote, token: str) -> str:
    """The branch a new request targets. Falls back to ``main``."""
    if remote.kind == GITLAB:
        url = f"{remote.api_base}/projects/{_project_id(remote)}"
    else:
        url = f"{remote.api_base}/repos/{remote.slug}"
    status, payload, text = _request("GET", url, kind=remote.kind, token=token)
    _raise_for_status(remote, status, payload, text, action="read the repository")
    if isinstance(payload, dict):
        branch = str(payload.get("default_branch") or "").strip()
        if branch:
            return branch
    return "main"


def find_open_request(remote: ForgeRemote, token: str, branch: str) -> dict[str, Any] | None:
    """The open PR/MR whose source branch is ``branch``, if there is one."""
    if not branch:
        return None
    if remote.kind == GITLAB:
        status, payload, text = _request(
            "GET",
            f"{remote.api_base}/projects/{_project_id(remote)}/merge_requests",
            kind=remote.kind,
            token=token,
            params={"source_branch": branch, "state": "opened", "per_page": 20},
        )
    elif remote.kind == GITHUB:
        status, payload, text = _request(
            "GET",
            f"{remote.api_base}/repos/{remote.slug}/pulls",
            kind=remote.kind,
            token=token,
            # ``head`` on GitHub is qualified by the owner of the fork.
            params={"head": f"{remote.owner}:{branch}", "state": "open", "per_page": 20},
        )
    else:
        status, payload, text = _request(
            "GET",
            f"{remote.api_base}/repos/{remote.slug}/pulls",
            kind=remote.kind,
            token=token,
            params={"state": "open", "limit": 50},
        )
    _raise_for_status(remote, status, payload, text, action="list pull requests")
    if not isinstance(payload, list):
        return None
    for row in payload:
        if not isinstance(row, dict):
            continue
        normalized = normalize_pr(remote.kind, row)
        if remote.kind == FORGEJO and normalized.get("head") != branch:
            continue
        if normalized.get("url"):
            return normalized
    return None


def create_request(
    remote: ForgeRemote,
    token: str,
    *,
    head: str,
    base: str,
    title: str,
    body: str,
    draft: bool,
) -> dict[str, Any]:
    """Open a pull request (GitHub, Forgejo) or merge request (GitLab)."""
    clean_title = (title or head).strip()[:_MAX_TITLE] or head
    clean_body = (body or "").strip()[:_MAX_BODY]
    action = f"open a {REQUEST_LABELS.get(remote.kind, 'pull request')}"

    if remote.kind == GITLAB:
        # GitLab has no draft flag on create: the "Draft:" title prefix is the
        # documented way, and its UI reads it back as a draft.
        if draft and not clean_title.lower().startswith("draft:"):
            clean_title = f"Draft: {clean_title}"[:_MAX_TITLE]
        status, payload, text = _request(
            "POST",
            f"{remote.api_base}/projects/{_project_id(remote)}/merge_requests",
            kind=remote.kind,
            token=token,
            json_body={
                "source_branch": head,
                "target_branch": base,
                "title": clean_title,
                "description": clean_body,
            },
        )
    elif remote.kind == GITHUB:
        status, payload, text = _request(
            "POST",
            f"{remote.api_base}/repos/{remote.slug}/pulls",
            kind=remote.kind,
            token=token,
            json_body={
                "title": clean_title,
                "body": clean_body,
                "head": head,
                "base": base,
                "draft": bool(draft),
            },
        )
        if status == 422 and draft:
            # Draft PRs are a paid feature on private repos in some plans.
            retry_status, retry_payload, retry_text = _request(
                "POST",
                f"{remote.api_base}/repos/{remote.slug}/pulls",
                kind=remote.kind,
                token=token,
                json_body={
                    "title": clean_title,
                    "body": clean_body,
                    "head": head,
                    "base": base,
                },
            )
            if 200 <= retry_status < 300:
                status, payload, text = retry_status, retry_payload, retry_text
    else:
        if draft and not clean_title.upper().startswith("WIP:"):
            clean_title = f"WIP: {clean_title}"[:_MAX_TITLE]
        status, payload, text = _request(
            "POST",
            f"{remote.api_base}/repos/{remote.slug}/pulls",
            kind=remote.kind,
            token=token,
            json_body={
                "title": clean_title,
                "body": clean_body,
                "head": head,
                "base": base,
            },
        )

    _raise_for_status(remote, status, payload, text, action=action)
    if not isinstance(payload, dict):
        raise ForgeError(
            f"{FORGE_LABELS.get(remote.kind, remote.kind)} returned no "
            f"{REQUEST_LABELS.get(remote.kind, 'pull request')} payload",
            status=502,
            code="forge_empty_response",
            host=remote.host,
            forge=remote.kind,
        )
    normalized = normalize_pr(remote.kind, payload)
    if not normalized.get("url"):
        raise ForgeError(
            f"{FORGE_LABELS.get(remote.kind, remote.kind)} returned no URL",
            status=502,
            code="forge_empty_response",
            host=remote.host,
            forge=remote.kind,
        )
    return normalized


def _github_checks(remote: ForgeRemote, token: str, ref: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    status, payload, text = _request(
        "GET",
        f"{remote.api_base}/repos/{remote.slug}/commits/{quote(ref, safe='')}/check-runs",
        kind=remote.kind,
        token=token,
        params={"per_page": 40},
    )
    _raise_for_status(remote, status, payload, text, action="read CI checks")
    runs = (payload or {}).get("check_runs") if isinstance(payload, dict) else None
    for row in runs or []:
        if not isinstance(row, dict):
            continue
        state = row.get("conclusion") or row.get("status") or "unknown"
        items.append(
            {
                "name": row.get("name") or "check",
                "state": str(state).lower(),
                "url": row.get("html_url") or row.get("details_url"),
            }
        )
    # Legacy commit statuses (external CI posting to the Status API).
    status, payload, text = _request(
        "GET",
        f"{remote.api_base}/repos/{remote.slug}/commits/{quote(ref, safe='')}/status",
        kind=remote.kind,
        token=token,
    )
    if 200 <= status < 300 and isinstance(payload, dict):
        for row in payload.get("statuses") or []:
            if not isinstance(row, dict):
                continue
            items.append(
                {
                    "name": row.get("context") or "status",
                    "state": str(row.get("state") or "unknown").lower(),
                    "url": row.get("target_url"),
                }
            )
    return items


def _gitlab_checks(remote: ForgeRemote, token: str, branch: str) -> list[dict[str, Any]]:
    status, payload, text = _request(
        "GET",
        f"{remote.api_base}/projects/{_project_id(remote)}/pipelines",
        kind=remote.kind,
        token=token,
        params={"ref": branch, "per_page": 1, "order_by": "id", "sort": "desc"},
    )
    _raise_for_status(remote, status, payload, text, action="read pipelines")
    if not isinstance(payload, list) or not payload:
        return []
    latest = payload[0]
    if not isinstance(latest, dict) or latest.get("id") is None:
        return []
    status, jobs, text = _request(
        "GET",
        f"{remote.api_base}/projects/{_project_id(remote)}/pipelines/{latest['id']}/jobs",
        kind=remote.kind,
        token=token,
        params={"per_page": 40},
    )
    if not (200 <= status < 300) or not isinstance(jobs, list):
        # The pipeline state alone still tells the panel whether CI is red.
        return [
            {
                "name": f"pipeline #{latest.get('id')}",
                "state": str(latest.get("status") or "unknown").lower(),
                "url": latest.get("web_url"),
            }
        ]
    items: list[dict[str, Any]] = []
    for row in jobs:
        if not isinstance(row, dict):
            continue
        items.append(
            {
                "name": row.get("name") or "job",
                "state": str(row.get("status") or "unknown").lower(),
                "url": row.get("web_url"),
            }
        )
    return items


def _forgejo_checks(remote: ForgeRemote, token: str, ref: str) -> list[dict[str, Any]]:
    status, payload, text = _request(
        "GET",
        f"{remote.api_base}/repos/{remote.slug}/commits/{quote(ref, safe='')}/statuses",
        kind=remote.kind,
        token=token,
        params={"limit": 40},
    )
    if status == 404:
        # A Forgejo without Actions and without any external CI has nothing
        # to report; that is not an error worth painting red in the panel.
        return []
    _raise_for_status(remote, status, payload, text, action="read commit statuses")
    items: list[dict[str, Any]] = []
    rows = payload if isinstance(payload, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        items.append(
            {
                "name": row.get("context") or row.get("description") or "status",
                "state": str(row.get("status") or row.get("state") or "unknown").lower(),
                "url": row.get("target_url"),
            }
        )
    return items


def check_summary(remote: ForgeRemote, token: str, *, branch: str) -> dict[str, Any]:
    """CI rollup for ``branch``, in the shape the Code panel already renders."""
    if remote.kind == GITLAB:
        items = _gitlab_checks(remote, token, branch)
    elif remote.kind == GITHUB:
        items = _github_checks(remote, token, branch)
    else:
        items = _forgejo_checks(remote, token, branch)
    return summarize_checks(items)


# ---------------------------------------------------------------------------
# One pull / merge request by number (board PR sync)
# ---------------------------------------------------------------------------


def _merged_state(kind: str, data: dict[str, Any]) -> tuple[bool, Any]:
    """``(merged, merged_at)``: each forge says it its own way."""
    merged_at = data.get("merged_at") or data.get("mergedAt")
    if kind == GITLAB:
        # GitLab has no "merged" boolean: "merged" is one of the MR states.
        merged = str(data.get("state") or "").lower() == "merged" or bool(merged_at)
    else:
        merged = bool(data.get("merged")) or bool(merged_at)
    return merged, merged_at


def get_request(remote: ForgeRemote, token: str, number: int) -> dict[str, Any]:
    """One pull request (GitHub, Forgejo) or merge request (GitLab) by number.

    GitLab numbers merge requests per project with ``iid``, which is exactly
    what its web URL carries, so the number read off a stored URL is usable
    as-is on all three forges.
    """
    if remote.kind == GITLAB:
        url = f"{remote.api_base}/projects/{_project_id(remote)}/merge_requests/{int(number)}"
    else:
        url = f"{remote.api_base}/repos/{remote.slug}/pulls/{int(number)}"
    action = f"read the {REQUEST_LABELS.get(remote.kind, 'pull request')}"
    status, payload, text = _request("GET", url, kind=remote.kind, token=token)
    _raise_for_status(remote, status, payload, text, action=action)
    if not isinstance(payload, dict):
        raise ForgeError(
            f"{FORGE_LABELS.get(remote.kind, remote.kind)} returned no payload for "
            f"{REQUEST_LABELS.get(remote.kind, 'pull request')} #{number}",
            status=502,
            code="forge_empty_response",
            host=remote.host,
            forge=remote.kind,
        )
    normalized = normalize_pr(remote.kind, payload)
    merged, merged_at = _merged_state(remote.kind, payload)
    normalized["merged"] = merged
    normalized["merged_at"] = merged_at
    if merged and str(normalized.get("state") or "").lower() in ("closed", ""):
        # GitHub reports a merged PR as state=closed; the board cares about
        # the difference between "merged" and "closed without merging".
        normalized["state"] = "merged"
    return normalized


def close_request(remote: ForgeRemote, token: str, number: int) -> dict[str, Any]:
    """Close a pull / merge request without merging it."""
    action = f"close the {REQUEST_LABELS.get(remote.kind, 'pull request')}"
    if remote.kind == GITLAB:
        status, payload, text = _request(
            "PUT",
            f"{remote.api_base}/projects/{_project_id(remote)}/merge_requests/{int(number)}",
            kind=remote.kind,
            token=token,
            json_body={"state_event": "close"},
        )
    elif remote.kind == GITHUB:
        status, payload, text = _request(
            "PATCH",
            f"{remote.api_base}/repos/{remote.slug}/pulls/{int(number)}",
            kind=remote.kind,
            token=token,
            json_body={"state": "closed"},
        )
    else:
        # Forgejo/Gitea models a pull request as an issue: the state lives on
        # the issue, and PATCHing /pulls/ is not how its own UI closes one.
        status, payload, text = _request(
            "PATCH",
            f"{remote.api_base}/repos/{remote.slug}/issues/{int(number)}",
            kind=remote.kind,
            token=token,
            json_body={"state": "closed"},
        )
    _raise_for_status(remote, status, payload, text, action=action)
    return {"ok": True, "number": int(number), "state": "closed"}


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------


_ISSUE_STATES = ("open", "closed", "all")
_MAX_ISSUES = 200
_MAX_ISSUE_BODY = 2000

# GitLab says "opened" where GitHub and Forgejo say "open".
_GITLAB_STATES = {"open": "opened", "closed": "closed", "all": "all"}


def _clean_state(state: str) -> str:
    value = str(state or "open").strip().lower()
    return value if value in _ISSUE_STATES else "open"


def _normalize_state(value: Any) -> str:
    state = str(value or "").strip().lower()
    return "open" if state == "opened" else state


def _label_names(raw: Any) -> list[str]:
    """Labels come back as ``["bug"]`` on GitLab and ``[{name}]`` elsewhere."""
    names: list[str] = []
    for item in raw or []:
        if isinstance(item, dict):
            name = item.get("name") or item.get("title")
            if name:
                names.append(str(name))
        elif isinstance(item, str) and item.strip():
            names.append(item.strip())
    return names


def _people(raw: Any) -> list[str]:
    names: list[str] = []
    for item in raw or []:
        if isinstance(item, dict):
            name = item.get("login") or item.get("username")
            if name:
                names.append(str(name))
    return names


def _normalize_github_issue(data: dict[str, Any]) -> dict[str, Any]:
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    return {
        "number": data.get("number"),
        "title": str(data.get("title") or ""),
        "body": str(data.get("body") or "")[:_MAX_ISSUE_BODY],
        "url": str(data.get("html_url") or data.get("url") or ""),
        "state": _normalize_state(data.get("state")),
        "labels": _label_names(data.get("labels")),
        "author": (user or {}).get("login"),
        "assignees": _people(data.get("assignees")),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "comments": int(data.get("comments") or 0)
        if isinstance(data.get("comments"), (int, float))
        else 0,
    }


def _normalize_gitlab_issue(data: dict[str, Any]) -> dict[str, Any]:
    author = data.get("author") if isinstance(data.get("author"), dict) else {}
    return {
        # ``iid`` is the number shown in the UI and used by every /issues/<n>
        # endpoint; ``id`` is global to the instance and useless here.
        "number": data.get("iid") or data.get("id"),
        "title": str(data.get("title") or ""),
        "body": str(data.get("description") or "")[:_MAX_ISSUE_BODY],
        "url": str(data.get("web_url") or ""),
        "state": _normalize_state(data.get("state")),
        "labels": _label_names(data.get("labels")),
        "author": (author or {}).get("username"),
        "assignees": _people(data.get("assignees")),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "comments": int(data.get("user_notes_count") or 0)
        if isinstance(data.get("user_notes_count"), (int, float))
        else 0,
    }


def _normalize_forgejo_issue(data: dict[str, Any]) -> dict[str, Any]:
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    return {
        "number": data.get("number") or data.get("index"),
        "title": str(data.get("title") or ""),
        "body": str(data.get("body") or "")[:_MAX_ISSUE_BODY],
        "url": str(data.get("html_url") or data.get("url") or ""),
        "state": _normalize_state(data.get("state")),
        "labels": _label_names(data.get("labels")),
        "author": (user or {}).get("login"),
        "assignees": _people(data.get("assignees")),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "comments": int(data.get("comments") or 0)
        if isinstance(data.get("comments"), (int, float))
        else 0,
    }


def normalize_issue(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    if kind == GITLAB:
        return _normalize_gitlab_issue(data)
    if kind == GITHUB:
        return _normalize_github_issue(data)
    return _normalize_forgejo_issue(data)


def _is_pull_request_row(kind: str, row: dict[str, Any]) -> bool:
    """GitHub returns pull requests from /issues; the board wants issues."""
    if kind == GITHUB:
        return isinstance(row.get("pull_request"), dict)
    if kind == FORGEJO:
        return row.get("pull_request") is not None
    return False


def list_issues(
    remote: ForgeRemote,
    token: str,
    *,
    state: str = "open",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Issues of ``remote``, newest first, pull requests excluded."""
    wanted = _clean_state(state)
    count = max(1, min(int(limit), _MAX_ISSUES))
    if remote.kind == GITLAB:
        url = f"{remote.api_base}/projects/{_project_id(remote)}/issues"
        params: dict[str, Any] = {
            "state": _GITLAB_STATES[wanted],
            "per_page": count,
            "order_by": "updated_at",
            "sort": "desc",
        }
    elif remote.kind == GITHUB:
        url = f"{remote.api_base}/repos/{remote.slug}/issues"
        params = {"state": wanted, "per_page": count, "sort": "updated", "direction": "desc"}
    else:
        url = f"{remote.api_base}/repos/{remote.slug}/issues"
        # Gitea/Forgejo lists pull requests with issues unless asked otherwise.
        params = {"state": wanted, "limit": count, "type": "issues"}
    status, payload, text = _request(
        "GET", url, kind=remote.kind, token=token, params=params
    )
    _raise_for_status(remote, status, payload, text, action="list issues")
    rows = payload if isinstance(payload, list) else []
    issues: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or _is_pull_request_row(remote.kind, row):
            continue
        issues.append(normalize_issue(remote.kind, row))
    return issues


def _forgejo_label_ids(remote: ForgeRemote, token: str, labels: list[str]) -> list[int]:
    """Forgejo's create-issue payload takes label ids, not names.

    Names that do not exist on the repository are dropped rather than
    failing the whole issue: the board mirror matters more than its tags.
    """
    if not labels:
        return []
    status, payload, _text = _request(
        "GET",
        f"{remote.api_base}/repos/{remote.slug}/labels",
        kind=remote.kind,
        token=token,
        params={"limit": 100},
    )
    if not (200 <= status < 300) or not isinstance(payload, list):
        return []
    by_name = {
        str(row.get("name") or "").strip().lower(): row.get("id")
        for row in payload
        if isinstance(row, dict) and row.get("id") is not None
    }
    ids: list[int] = []
    for label in labels:
        found = by_name.get(str(label).strip().lower())
        if isinstance(found, int):
            ids.append(found)
    return ids


def create_issue(
    remote: ForgeRemote,
    token: str,
    *,
    title: str,
    body: str = "",
    labels: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """File an issue on any forge, dropping labels rather than failing."""
    clean_title = (title or "issue").strip()[:_MAX_TITLE] or "issue"
    clean_body = (body or "").strip()[:_MAX_BODY]
    names = [str(label).strip() for label in (labels or []) if str(label).strip()][:10]

    if remote.kind == GITLAB:
        url = f"{remote.api_base}/projects/{_project_id(remote)}/issues"
        base_body: dict[str, Any] = {"title": clean_title, "description": clean_body}
        # GitLab takes a comma-separated string and creates missing labels.
        with_labels = {**base_body, "labels": ",".join(names)} if names else base_body
    elif remote.kind == GITHUB:
        url = f"{remote.api_base}/repos/{remote.slug}/issues"
        base_body = {"title": clean_title, "body": clean_body}
        with_labels = {**base_body, "labels": names} if names else base_body
    else:
        url = f"{remote.api_base}/repos/{remote.slug}/issues"
        base_body = {"title": clean_title, "body": clean_body}
        ids = _forgejo_label_ids(remote, token, names) if names else []
        with_labels = {**base_body, "labels": ids} if ids else base_body

    status, payload, text = _request(
        "POST", url, kind=remote.kind, token=token, json_body=with_labels
    )
    if status in (400, 422) and with_labels is not base_body:
        # A label that does not exist on the repository must not cost the issue.
        status, payload, text = _request(
            "POST", url, kind=remote.kind, token=token, json_body=base_body
        )
    _raise_for_status(remote, status, payload, text, action="create an issue")
    if not isinstance(payload, dict):
        raise ForgeError(
            f"{FORGE_LABELS.get(remote.kind, remote.kind)} returned no issue payload",
            status=502,
            code="forge_empty_response",
            host=remote.host,
            forge=remote.kind,
        )
    issue = normalize_issue(remote.kind, payload)
    if not issue.get("url"):
        raise ForgeError(
            f"{FORGE_LABELS.get(remote.kind, remote.kind)} returned no issue URL",
            status=502,
            code="forge_empty_response",
            host=remote.host,
            forge=remote.kind,
        )
    return issue


def comment_issue(
    remote: ForgeRemote,
    token: str,
    number: int,
    body: str,
) -> dict[str, Any]:
    """Add a comment to an issue (a note, in GitLab's vocabulary)."""
    text_body = (body or "").strip()[:_MAX_BODY]
    if not text_body:
        raise ForgeError(
            "refusing to post an empty comment",
            status=400,
            code="forge_empty_comment",
            host=remote.host,
            forge=remote.kind,
        )
    if remote.kind == GITLAB:
        url = f"{remote.api_base}/projects/{_project_id(remote)}/issues/{int(number)}/notes"
    else:
        url = f"{remote.api_base}/repos/{remote.slug}/issues/{int(number)}/comments"
    status, payload, text = _request(
        "POST", url, kind=remote.kind, token=token, json_body={"body": text_body}
    )
    _raise_for_status(remote, status, payload, text, action="comment on the issue")
    data = payload if isinstance(payload, dict) else {}
    return {
        "ok": True,
        "number": int(number),
        "url": data.get("html_url") or data.get("web_url") or "",
    }


def close_issue(
    remote: ForgeRemote,
    token: str,
    number: int,
    *,
    comment: str = "",
) -> dict[str, Any]:
    """Close an issue, optionally saying why first (like ``gh issue close``)."""
    commented = False
    if (comment or "").strip():
        comment_issue(remote, token, number, comment)
        commented = True
    if remote.kind == GITLAB:
        status, payload, text = _request(
            "PUT",
            f"{remote.api_base}/projects/{_project_id(remote)}/issues/{int(number)}",
            kind=remote.kind,
            token=token,
            json_body={"state_event": "close"},
        )
    else:
        status, payload, text = _request(
            "PATCH",
            f"{remote.api_base}/repos/{remote.slug}/issues/{int(number)}",
            kind=remote.kind,
            token=token,
            json_body={"state": "closed"},
        )
    _raise_for_status(remote, status, payload, text, action="close the issue")
    return {"ok": True, "number": int(number), "state": "closed", "commented": commented}


# ---------------------------------------------------------------------------
# Reviews: inline comments on a pull / merge request
# ---------------------------------------------------------------------------


_REVIEW_EVENTS = ("COMMENT", "REQUEST_CHANGES", "APPROVE")


def request_files(remote: ForgeRemote, token: str, number: int) -> list[str]:
    """Paths touched by the request, used to anchor inline comments."""
    if remote.kind == GITLAB:
        base = f"{remote.api_base}/projects/{_project_id(remote)}/merge_requests/{int(number)}"
        status, payload, text = _request(
            "GET", f"{base}/diffs", kind=remote.kind, token=token, params={"per_page": 100}
        )
        rows: list[Any] = payload if isinstance(payload, list) else []
        if status == 404:
            # /diffs landed in GitLab 15.7; older servers only have /changes.
            status, payload, text = _request(
                "GET", f"{base}/changes", kind=remote.kind, token=token
            )
            if isinstance(payload, dict):
                rows = payload.get("changes") or []
        _raise_for_status(remote, status, payload, text, action="list changed files")
        paths: list[str] = []
        for row in rows:
            if isinstance(row, dict):
                path = row.get("new_path") or row.get("old_path")
                if path:
                    paths.append(str(path))
        return paths

    status, payload, text = _request(
        "GET",
        f"{remote.api_base}/repos/{remote.slug}/pulls/{int(number)}/files",
        kind=remote.kind,
        token=token,
        params={"per_page": 100} if remote.kind == GITHUB else {"limit": 100},
    )
    _raise_for_status(remote, status, payload, text, action="list changed files")
    rows = payload if isinstance(payload, list) else []
    return [
        str(row.get("filename"))
        for row in rows
        if isinstance(row, dict) and row.get("filename")
    ]


def _gitlab_diff_refs(remote: ForgeRemote, token: str, number: int) -> dict[str, Any]:
    """The three shas GitLab needs to pin a comment to a diff line."""
    status, payload, text = _request(
        "GET",
        f"{remote.api_base}/projects/{_project_id(remote)}/merge_requests/{int(number)}",
        kind=remote.kind,
        token=token,
    )
    _raise_for_status(remote, status, payload, text, action="read the merge request")
    refs = (payload or {}).get("diff_refs") if isinstance(payload, dict) else None
    return refs if isinstance(refs, dict) else {}


def create_review(
    remote: ForgeRemote,
    token: str,
    number: int,
    *,
    body: str,
    comments: list[dict[str, Any]],
    event: str = "COMMENT",
) -> dict[str, Any]:
    """Post a review with inline comments, in each forge's own shape.

    GitHub and Forgejo have a review object holding every comment at once.
    GitLab has no review: an inline comment is a discussion pinned to the
    diff, so the summary becomes a note and each finding a discussion.
    """
    wanted = event if event in _REVIEW_EVENTS else "COMMENT"
    rows = [
        row
        for row in comments
        if isinstance(row, dict) and row.get("path") and int(row.get("line") or 0) > 0
    ]

    if remote.kind == GITLAB:
        return _gitlab_review(remote, token, number, body=body, comments=rows, event=wanted)
    if remote.kind == GITHUB:
        payload_body = {
            "event": wanted,
            "body": body[:65000],
            "comments": [
                {
                    "path": str(row["path"]),
                    "line": int(row["line"]),
                    "side": str(row.get("side") or "RIGHT"),
                    "body": str(row.get("body") or ""),
                }
                for row in rows
            ],
        }
    else:
        payload_body = {
            # Gitea/Forgejo says APPROVED where GitHub says APPROVE.
            "event": "APPROVED" if wanted == "APPROVE" else wanted,
            "body": body[:65000],
            "comments": [
                {
                    "path": str(row["path"]),
                    "body": str(row.get("body") or ""),
                    # Forgejo anchors on the position in the new file.
                    "new_position": int(row["line"]),
                }
                for row in rows
            ],
        }
    status, payload, text = _request(
        "POST",
        f"{remote.api_base}/repos/{remote.slug}/pulls/{int(number)}/reviews",
        kind=remote.kind,
        token=token,
        json_body=payload_body,
    )
    _raise_for_status(remote, status, payload, text, action="post the review")
    data = payload if isinstance(payload, dict) else {}
    return {
        "ok": True,
        "posted": len(rows),
        "failed": [],
        "review_id": data.get("id"),
        "html_url": data.get("html_url"),
    }


def _gitlab_review(
    remote: ForgeRemote,
    token: str,
    number: int,
    *,
    body: str,
    comments: list[dict[str, Any]],
    event: str,
) -> dict[str, Any]:
    base = f"{remote.api_base}/projects/{_project_id(remote)}/merge_requests/{int(number)}"
    refs = _gitlab_diff_refs(remote, token, number) if comments else {}
    posted = 0
    failed: list[dict[str, Any]] = []
    for row in comments:
        position = {
            "position_type": "text",
            "base_sha": refs.get("base_sha"),
            "start_sha": refs.get("start_sha"),
            "head_sha": refs.get("head_sha"),
            "new_path": str(row["path"]),
            "old_path": str(row["path"]),
            "new_line": int(row["line"]),
        }
        status, payload, text = _request(
            "POST",
            f"{base}/discussions",
            kind=remote.kind,
            token=token,
            params=None,
            json_body={"body": str(row.get("body") or ""), "position": position},
        )
        if 200 <= status < 300:
            posted += 1
            continue
        # A line that moved out of the diff is refused per comment; the rest
        # of the review must still land.
        failed.append(
            {
                "path": str(row["path"]),
                "line": int(row["line"]),
                "detail": redact_url_credentials(_api_message(payload, text))[:200],
            }
        )
    status, payload, text = _request(
        "POST",
        f"{base}/notes",
        kind=remote.kind,
        token=token,
        json_body={"body": (body or "Navin review")[:65000]},
    )
    _raise_for_status(remote, status, payload, text, action="post the review summary")
    if event == "APPROVE":
        _request("POST", f"{base}/approve", kind=remote.kind, token=token)
    data = payload if isinstance(payload, dict) else {}
    return {
        "ok": True,
        "posted": posted,
        "failed": failed,
        "review_id": data.get("id"),
        "html_url": (data.get("web_url") or f"{remote.url}/-/merge_requests/{int(number)}"),
    }


# ---------------------------------------------------------------------------
# Failed CI log (Fix CI / Explain CI)
# ---------------------------------------------------------------------------


_MAX_LOG_CHARS = 6000


def failed_log_excerpt(remote: ForgeRemote, token: str, *, branch: str) -> str:
    """Tail of the failing CI job for ``branch``, empty when unavailable.

    Only GitLab exposes a job log as plain text over REST. GitHub redirects
    to a signed blob (``gh run view --log-failed`` is used instead when the
    CLI is there) and Forgejo Actions has no stable log endpoint yet, so
    both return an empty excerpt rather than a wrong one.
    """
    if remote.kind != GITLAB or not token or not branch:
        return ""
    try:
        status, payload, _text = _request(
            "GET",
            f"{remote.api_base}/projects/{_project_id(remote)}/pipelines",
            kind=remote.kind,
            token=token,
            params={"ref": branch, "status": "failed", "per_page": 1, "sort": "desc"},
        )
        if not (200 <= status < 300) or not isinstance(payload, list) or not payload:
            return ""
        pipeline = payload[0]
        if not isinstance(pipeline, dict) or pipeline.get("id") is None:
            return ""
        status, jobs, _text = _request(
            "GET",
            f"{remote.api_base}/projects/{_project_id(remote)}"
            f"/pipelines/{pipeline['id']}/jobs",
            kind=remote.kind,
            token=token,
            params={"scope[]": "failed", "per_page": 5},
        )
        if not (200 <= status < 300) or not isinstance(jobs, list):
            return ""
        job_id = next(
            (
                row.get("id")
                for row in jobs
                if isinstance(row, dict)
                and row.get("id") is not None
                and str(row.get("status") or "").lower() in _FAILING
            ),
            None,
        )
        if job_id is None:
            return ""
        status, _payload, trace = _request(
            "GET",
            f"{remote.api_base}/projects/{_project_id(remote)}/jobs/{job_id}/trace",
            kind=remote.kind,
            token=token,
        )
        if not (200 <= status < 300):
            return ""
    except ForgeError:
        # A log excerpt is a bonus on top of the prompt, never a blocker.
        return ""
    return redact_url_credentials(trace or "")[-_MAX_LOG_CHARS:]
