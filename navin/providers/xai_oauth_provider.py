# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""xAI Grok subscription via SpaceXAI OAuth (device code).

Official CLI (`grok login --device-auth`) talks to ``auth.x.ai`` with the
public Grok-CLI client, stores the session in ``~/.grok/auth.json``, and
sends the access token as Bearer. SuperGrok / X Premium+ is accepted on
the CLI chat proxy, not on the metered ``api.x.ai`` developer API.

Navin keeps its own token file (same reason Codex does not import
``~/.codex/auth.json``): a leftover CLI session must not show "Signed in"
here. Sign-in is the RFC 8628 device-code path the official docs give for
headless / remote hosts, which is also the only path the WebUI can finish
without a localhost callback on the server.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from collections.abc import Awaitable, Callable

import httpx
from oauth_cli_kit.models import OAuthToken
from oauth_cli_kit.storage import FileTokenStorage, _FileLock

from navin.providers.openai_compat_provider import OpenAICompatProvider

ISSUER = "https://auth.x.ai"
DEVICE_CODE_URL = f"{ISSUER}/oauth2/device/code"
TOKEN_URL = f"{ISSUER}/oauth2/token"
# Public Grok-CLI client. auth.x.ai only allows this allowlisted id for
# the device-code grant (no secret; PKCE is not used on this grant).
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
SCOPE = (
    "openid profile email offline_access grok-cli:access api:access "
    "conversations:read conversations:write"
)
DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
DEFAULT_API_BASE = "https://cli-chat-proxy.grok.com/v1"
TOKEN_FILENAME = "xai-oauth.json"
TOKEN_APP_NAME = "navin"
USER_AGENT = "navin/0.1"
CLIENT_IDENTIFIER = "grok-shell"
CLIENT_VERSION = "0.2.93"
TOKEN_AUTH_HEADER = "xai-grok-cli"
_EXPIRY_SKEW_SECONDS = 300
_DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60


def _resolve(env_var: str, default: str) -> str:
    value = os.environ.get(env_var)
    return value.strip() if value and value.strip() else default


def get_storage() -> FileTokenStorage:
    return FileTokenStorage(
        token_filename=TOKEN_FILENAME,
        app_name=TOKEN_APP_NAME,
        import_codex_cli=False,
    )


def get_xai_oauth_login_status() -> OAuthToken | None:
    token = get_storage().load()
    if not token or not token.access:
        return None
    return token


def _account_from_id_token(id_token: str) -> str | None:
    parts = id_token.split(".")
    if len(parts) < 2:
        return None
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    for key in ("email", "preferred_username", "name", "sub"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _token_from_payload(payload: dict, *, fallback_refresh: str = "") -> OAuthToken:
    access = str(payload.get("access_token") or "").strip()
    if not access:
        raise RuntimeError("xAI token response had no access_token.")
    refresh = str(payload.get("refresh_token") or fallback_refresh or "").strip()
    expires_in = int(payload.get("expires_in") or _DEFAULT_TTL_SECONDS)
    account = payload.get("email") or payload.get("account_id")
    if not account:
        account = _account_from_id_token(str(payload.get("id_token") or ""))
    return OAuthToken(
        access=access,
        refresh=refresh,
        expires=int((time.time() + expires_in) * 1000),
        account_id=str(account) if account else None,
    )


def refresh_xai_oauth_token(token: OAuthToken, *, proxy: str | None = None) -> OAuthToken:
    if not token.refresh:
        raise RuntimeError("xAI session has no refresh token. Sign in again.")
    timeout = httpx.Timeout(20.0, connect=20.0)
    with httpx.Client(timeout=timeout, follow_redirects=True, proxy=proxy, trust_env=True) as client:
        response = client.post(
            _resolve("NAVIN_XAI_TOKEN_URL", TOKEN_URL),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": token.refresh,
                "client_id": _resolve("NAVIN_XAI_OAUTH_CLIENT_ID", CLIENT_ID),
            },
        )
    if response.status_code != 200:
        raise RuntimeError(f"xAI token refresh failed: {response.status_code} {response.text}")
    refreshed = _token_from_payload(response.json(), fallback_refresh=token.refresh)
    get_storage().save(refreshed)
    return refreshed


def get_valid_xai_oauth_token(*, min_ttl_seconds: int = _EXPIRY_SKEW_SECONDS) -> OAuthToken:
    storage = get_storage()
    token = storage.load()
    if not token or not token.access:
        raise RuntimeError(
            "Grok (x.ai subscription) is not signed in. "
            "Open Settings → Providers and sign in, or run: navin provider login xai-oauth"
        )
    now_ms = int(time.time() * 1000)
    if token.expires - now_ms > min_ttl_seconds * 1000:
        return token
    lock_path = storage.get_token_path().with_suffix(".lock")
    with _FileLock(lock_path):
        token = storage.load() or token
        now_ms = int(time.time() * 1000)
        if token.expires - now_ms > min_ttl_seconds * 1000:
            return token
        return refresh_xai_oauth_token(token)


def login_xai_oauth(
    print_fn: Callable[[str], None] | None = None,
    prompt_fn: Callable[[str], str] | None = None,
) -> OAuthToken:
    """RFC 8628 device-code sign-in (``grok login --device-auth``)."""
    del prompt_fn
    printer = print_fn or print
    timeout = httpx.Timeout(20.0, connect=20.0)
    client_id = _resolve("NAVIN_XAI_OAUTH_CLIENT_ID", CLIENT_ID)
    device_url = _resolve("NAVIN_XAI_DEVICE_CODE_URL", DEVICE_CODE_URL)
    token_url = _resolve("NAVIN_XAI_TOKEN_URL", TOKEN_URL)

    with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=True) as client:
        response = client.post(
            device_url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
            },
            data={"client_id": client_id, "scope": SCOPE},
        )
        response.raise_for_status()
        payload = response.json()
        device_code = str(payload["device_code"])
        user_code = str(payload["user_code"])
        verify_url = str(
            payload.get("verification_uri_complete")
            or payload.get("verification_uri")
            or ""
        )
        interval = max(1, int(payload.get("interval") or 5))
        expires_in = int(payload.get("expires_in") or 900)

        printer(f"Open: {verify_url}")
        printer(f"Code: {user_code}")

        deadline = time.time() + expires_in
        current_interval = interval
        token_payload: dict | None = None
        while time.time() < deadline:
            poll = client.post(
                token_url,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": USER_AGENT,
                },
                data={
                    "client_id": client_id,
                    "device_code": device_code,
                    "grant_type": DEVICE_CODE_GRANT,
                },
            )
            if poll.status_code == 200:
                token_payload = poll.json()
                break
            try:
                poll_payload = poll.json()
            except ValueError:
                poll.raise_for_status()
                raise RuntimeError(f"xAI device flow failed: {poll.text}") from None
            error = poll_payload.get("error")
            if error == "authorization_pending":
                time.sleep(current_interval)
                continue
            if error == "slow_down":
                current_interval += 5
                time.sleep(current_interval)
                continue
            if error == "expired_token":
                raise RuntimeError("xAI device code expired. Sign in again.")
            if error == "access_denied":
                raise RuntimeError("xAI device flow was denied.")
            if error:
                raise RuntimeError(str(poll_payload.get("error_description") or error))
            time.sleep(current_interval)
        else:
            raise RuntimeError("xAI device flow timed out.")

    token = _token_from_payload(token_payload or {})
    get_storage().save(token)
    return token


def subscription_headers() -> dict[str, str]:
    """Identity headers the CLI chat proxy requires on a SuperGrok session."""
    return {
        "X-XAI-Token-Auth": TOKEN_AUTH_HEADER,
        "x-grok-client-identifier": CLIENT_IDENTIFIER,
        "x-grok-client-version": CLIENT_VERSION,
        "User-Agent": USER_AGENT,
    }


class XaiOAuthProvider(OpenAICompatProvider):
    """OpenAI-compatible client backed by a SuperGrok / X Premium+ session."""

    def __init__(self, default_model: str = "", proxy: str | None = None):
        from navin.providers.registry import find_by_name

        self._proxy = proxy or None
        self._token_lock = asyncio.Lock()
        super().__init__(
            api_key="no-key",
            api_base=_resolve("GROK_CLI_CHAT_PROXY_BASE_URL", DEFAULT_API_BASE),
            default_model=default_model,
            extra_headers=subscription_headers(),
            spec=find_by_name("xai_oauth"),
            proxy=proxy,
        )

    async def _refresh_client_api_key(self) -> str:
        async with self._token_lock:
            token = await asyncio.to_thread(get_valid_xai_oauth_token)
        client = await self._ensure_client()
        self.api_key = token.access
        client.api_key = token.access
        return token.access

    async def chat(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, object] | None = None,
    ):
        await self._refresh_client_api_key()
        return await super().chat(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            tool_choice=tool_choice,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, object] | None = None,
        on_content_delta: Callable[[str], None] | None = None,
        on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_call_delta: Callable[[dict[str, object]], Awaitable[None]] | None = None,
    ):
        await self._refresh_client_api_key()
        return await super().chat_stream(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            tool_choice=tool_choice,
            on_content_delta=on_content_delta,
            on_thinking_delta=on_thinking_delta,
            on_tool_call_delta=on_tool_call_delta,
        )
