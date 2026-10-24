# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings > Git & GitHub: which forge accounts this machine can act as.

Backs ``/api/webui/git/accounts``, ``/api/webui/git/test`` and the GitHub
OAuth device sign-in (``/api/webui/git/device/start`` + ``/poll``). Every
answer names a host, a login, a token *source* and scopes; never a token.
The device code stays in this process, keyed by an opaque ``flow_id``, and
the token GitHub hands back is written through the same settings path as a
pasted one, so it is encrypted at rest like every other secret.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
from typing import Any

import httpx

from navin.utils import git_credentials
from navin.webui import forge_api

_ACCOUNT_TTL_S = 60.0
_ACCOUNT_ERROR_TTL_S = 15.0
_HTTP_TIMEOUT_S = 15.0

GITHUB_DEVICE_HOST = "github.com"
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_DEVICE_SCOPES = "repo read:org workflow"
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_CLIENT_ID_ENV = "NAVIN_GITHUB_OAUTH_CLIENT_ID"
_MAX_FLOWS = 16

# Generic env names that say which public host a token is for.
_PUBLIC_ENV_HOSTS = {
    "GITHUB_TOKEN": "github.com",
    "GH_TOKEN": "github.com",
    "GITLAB_TOKEN": "gitlab.com",
    "GL_TOKEN": "gitlab.com",
    "GLAB_TOKEN": "gitlab.com",
    "BITBUCKET_TOKEN": "bitbucket.org",
    "BITBUCKET_ACCESS_TOKEN": "bitbucket.org",
}


class GitAccountError(Exception):
    def __init__(self, message: str, *, status: int = 400, code: str = "git_account_error") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


# (host, token fingerprint) -> (expiry, {login, scopes, error})
_account_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def clear_account_cache() -> None:
    with _cache_lock:
        _account_cache.clear()


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _config(config: Any | None) -> Any | None:
    if config is not None:
        return config
    try:
        from navin.config.loader import load_config

        return load_config()
    except Exception:  # noqa: BLE001 - a broken config still lists env / gh
        return None


def _public_source(source: str) -> str:
    if source.startswith("env"):
        return "env"
    return source if source in ("settings", "gh") else source or ""


def _remote_for(host: str, kind: str) -> forge_api.ForgeRemote:
    api_kind = kind if kind in forge_api.FORGE_KINDS else forge_api.UNKNOWN
    return forge_api.ForgeRemote(kind=api_kind, host=host, owner="_", repo="_")


def _probe_user(host: str, kind: str, token: str, *, fresh: bool = False) -> dict[str, Any]:
    """``{login, scopes, error}`` for ``token``, cached briefly per token."""
    key = (host, _fingerprint(token))
    now = time.monotonic()
    if not fresh:
        with _cache_lock:
            hit = _account_cache.get(key)
        if hit is not None and hit[0] > now:
            return dict(hit[1])
    if kind not in forge_api.FORGE_KINDS:
        result: dict[str, Any] = {"login": None, "scopes": None, "error": None}
        ttl = _ACCOUNT_TTL_S
    else:
        try:
            info = forge_api.current_user(_remote_for(host, kind), token)
            result = {"login": info.get("login"), "scopes": info.get("scopes"), "error": None}
            if isinstance(info.get("profile"), dict):
                result["profile"] = dict(info["profile"])
            ttl = _ACCOUNT_TTL_S
        except forge_api.ForgeError as exc:
            result = {"login": None, "scopes": None, "error": exc.message}
            ttl = _ACCOUNT_ERROR_TTL_S
    if result.get("login"):
        git_credentials.remember_login(host, result["login"])
    with _cache_lock:
        _account_cache[key] = (now + ttl, dict(result))
    return result


def _candidate_hosts(config: Any | None) -> list[str]:
    forge = git_credentials.forge_section(config)
    tokens = getattr(forge, "tokens", None) or {}
    pinned = getattr(forge, "hosts", None) or {}
    hosts: list[str] = []

    def add(host: str) -> None:
        name = (host or "").strip().lower()
        if name and name not in hosts:
            hosts.append(name)

    for host in sorted(tokens) if isinstance(tokens, dict) else ():
        add(host)
    for host in sorted(pinned) if isinstance(pinned, dict) else ():
        add(host)
    for name, host in _PUBLIC_ENV_HOSTS.items():
        if (os.environ.get(name) or "").strip():
            add(host)
    for name, value in os.environ.items():
        if name.startswith("NAVIN_FORGE_TOKEN_") and (value or "").strip():
            # The per-host name folds dots to underscores; a host with dashes
            # would not round-trip, which only costs that host its listing.
            add(name[len("NAVIN_FORGE_TOKEN_"):].lower().replace("_", "."))
    # A signed-in gh covers github.com even with nothing configured.
    add("github.com")
    return hosts


def device_flow_client_id(config: Any | None = None) -> str:
    forge = git_credentials.forge_section(config)
    configured = str(getattr(forge, "github_oauth_client_id", "") or "").strip()
    return configured or (os.environ.get(_CLIENT_ID_ENV) or "").strip()


def list_accounts(config: Any | None = None) -> dict[str, Any]:
    """``{accounts: [{host, kind, login, source, scopes}], device_flow: {github}}``."""
    cfg = _config(config)
    effective = git_credentials.as_full_config(cfg)
    accounts: list[dict[str, Any]] = []
    for host in _candidate_hosts(cfg):
        kind = git_credentials.kind_for_host(host, config=effective)
        token, source = git_credentials.resolve_host_token(host, kind=kind, config=effective)
        if not token:
            continue
        info = _probe_user(host, kind, token)
        accounts.append(
            {
                "host": host,
                "kind": kind,
                "login": info.get("login"),
                "source": _public_source(source),
                "scopes": info.get("scopes"),
            }
        )
    return {
        "accounts": accounts,
        "device_flow": {"github": bool(device_flow_client_id(cfg))},
    }


def _clean_host(raw: str) -> str:
    value = (raw or "").strip().lower()
    if "://" in value:
        from urllib.parse import urlsplit

        value = (urlsplit(value).hostname or "").lower()
    value = value.strip("/").split("/")[0]
    if not value or any(ch.isspace() for ch in value):
        raise GitAccountError("host is required", status=400, code="bad_host")
    return value


def clean_host(raw: str) -> str:
    """``github.com`` out of a host, ``host:port`` or URL; raises on an empty one."""
    return _clean_host(raw)


def account_info(host: str, config: Any | None = None, *, fresh: bool = False) -> dict[str, Any]:
    """Everything known about the account behind ``host``'s token, never the token.

    ``{host, kind, source, has_token, ok, login, scopes, error, profile}``;
    ``profile`` carries the public ``id`` / ``name`` / ``email`` the forge
    returned, used to give git a commit identity.
    """
    name = _clean_host(host)
    cfg = _config(config)
    effective = git_credentials.as_full_config(cfg)
    kind = git_credentials.kind_for_host(name, config=effective)
    token, source = git_credentials.resolve_host_token(name, kind=kind, config=effective)
    base: dict[str, Any] = {
        "host": name,
        "kind": kind,
        "source": _public_source(source) if token else "",
        "has_token": bool(token),
        "ok": False,
        "login": None,
        "scopes": None,
        "error": None,
        "profile": {},
    }
    if not token:
        base["error"] = f"No token for {name}. Add one in {git_credentials.SETTINGS_POINTER}."
        return base
    if kind not in forge_api.FORGE_KINDS:
        base["error"] = (
            f"Cannot check {name}: its forge type is unknown. Set it in "
            f"{git_credentials.SETTINGS_POINTER}."
        )
        return base
    info = _probe_user(name, kind, token, fresh=fresh)
    base.update(
        ok=info.get("error") is None and bool(info.get("login")),
        login=info.get("login"),
        scopes=info.get("scopes"),
        error=info.get("error"),
        profile=dict(info.get("profile") or {}),
    )
    return base


def test_account(host: str, config: Any | None = None) -> dict[str, Any]:
    """``{ok, login, scopes, error}``: a fresh "who am I" with the current token."""
    info = account_info(host, config, fresh=True)
    return {
        "ok": info["ok"],
        "login": info["login"],
        "scopes": info["scopes"],
        "error": info["error"],
    }


# ---------------------------------------------------------------------------
# GitHub OAuth device flow
# ---------------------------------------------------------------------------


# flow_id -> {device_code, client_id, host, interval, expires_at, next_poll}
_flows: dict[str, dict[str, Any]] = {}
_flows_lock = threading.Lock()


def _prune_flows(now: float) -> None:
    for flow_id in [key for key, flow in _flows.items() if flow["expires_at"] <= now]:
        _flows.pop(flow_id, None)
    while len(_flows) >= _MAX_FLOWS:
        _flows.pop(next(iter(_flows)), None)


def _github_post(url: str, data: dict[str, str]) -> dict[str, Any]:
    try:
        response = httpx.post(
            url,
            data=data,
            headers={"Accept": "application/json", "User-Agent": "navin-code"},
            timeout=_HTTP_TIMEOUT_S,
            follow_redirects=False,
        )
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise GitAccountError(
            f"cannot reach github.com: {exc.__class__.__name__}",
            status=502,
            code="github_unreachable",
        ) from exc
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        raise GitAccountError(
            f"github.com returned HTTP {response.status_code} without a JSON answer",
            status=502,
            code="github_bad_response",
        )
    return payload


def start_device_flow(host: str = GITHUB_DEVICE_HOST, config: Any | None = None) -> dict[str, Any]:
    """Ask GitHub for a user code; the device code never leaves the server."""
    name = _clean_host(host or GITHUB_DEVICE_HOST)
    if name != GITHUB_DEVICE_HOST:
        raise GitAccountError(
            "device sign-in is only available for github.com",
            status=400,
            code="device_flow_unsupported",
        )
    cfg = _config(config)
    client_id = device_flow_client_id(cfg)
    if not client_id:
        raise GitAccountError(
            "GitHub sign-in is not configured on this machine. Add a token in "
            f"{git_credentials.SETTINGS_POINTER} instead.",
            status=400,
            code="device_flow_unavailable",
        )
    payload = _github_post(
        GITHUB_DEVICE_CODE_URL, {"client_id": client_id, "scope": GITHUB_DEVICE_SCOPES}
    )
    device_code = str(payload.get("device_code") or "")
    user_code = str(payload.get("user_code") or "")
    if not device_code or not user_code:
        message = str(payload.get("error_description") or payload.get("error") or "")
        raise GitAccountError(
            f"GitHub refused to start the sign-in{': ' + message if message else ''}",
            status=502,
            code="device_flow_error",
        )
    interval = max(1, int(payload.get("interval") or 5))
    expires_in = max(1, int(payload.get("expires_in") or 900))
    now = time.monotonic()
    flow_id = secrets.token_urlsafe(18)
    with _flows_lock:
        _prune_flows(now)
        _flows[flow_id] = {
            "device_code": device_code,
            "client_id": client_id,
            "host": name,
            "interval": interval,
            "expires_at": now + expires_in,
            "next_poll": 0.0,
        }
    return {
        "flow_id": flow_id,
        "user_code": user_code,
        "verification_uri": str(payload.get("verification_uri") or "https://github.com/login/device"),
        "interval": interval,
        "expires_in": expires_in,
    }


def _store_github_token(host: str, token: str) -> None:
    """Save through the Settings > Git & GitHub path (encrypted at rest)."""
    from navin.webui.settings_api import _apply_forge_host, load_config, save_config

    config = load_config()
    if _apply_forge_host(config, {"forge_token": [token], "forge_kind": [forge_api.GITHUB]}, host):
        save_config(config)


def poll_device_flow(flow_id: str, config: Any | None = None) -> dict[str, Any]:
    """``{status, login?, interval?}``; on ``ok`` the token is already saved."""
    key = (flow_id or "").strip()
    now = time.monotonic()
    with _flows_lock:
        flow = _flows.get(key)
        if flow is None:
            return {"status": "expired"}
        if flow["expires_at"] <= now:
            _flows.pop(key, None)
            return {"status": "expired"}
        if now < flow["next_poll"]:
            # Faster than GitHub allows: answer for it instead of earning a
            # slow_down that lengthens every later poll.
            return {"status": "pending", "interval": flow["interval"]}
        flow["next_poll"] = now + flow["interval"]
        snapshot = dict(flow)

    payload = _github_post(
        GITHUB_ACCESS_TOKEN_URL,
        {
            "client_id": snapshot["client_id"],
            "device_code": snapshot["device_code"],
            "grant_type": _DEVICE_GRANT,
        },
    )
    token = str(payload.get("access_token") or "")
    if token:
        with _flows_lock:
            _flows.pop(key, None)
        host = snapshot["host"]
        _store_github_token(host, token)
        clear_account_cache()
        login: str | None = None
        try:
            login = forge_api.current_user(_remote_for(host, forge_api.GITHUB), token).get("login")
        except forge_api.ForgeError:
            login = None
        git_credentials.remember_login(host, login)
        return {"status": "ok", "login": login}

    error = str(payload.get("error") or "")
    if error == "authorization_pending":
        return {"status": "pending", "interval": snapshot["interval"]}
    if error == "slow_down":
        interval = int(payload.get("interval") or snapshot["interval"] + 5)
        with _flows_lock:
            live = _flows.get(key)
            if live is not None:
                live["interval"] = interval
                live["next_poll"] = time.monotonic() + interval
        return {"status": "slow_down", "interval": interval}
    with _flows_lock:
        _flows.pop(key, None)
    if error == "expired_token":
        return {"status": "expired"}
    if error == "access_denied":
        return {"status": "denied"}
    return {
        "status": "error",
        "error": str(payload.get("error_description") or error or "unknown error")[:300],
    }
