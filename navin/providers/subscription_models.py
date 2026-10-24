# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Live model lists of the account-based subscriptions.

OpenAI Codex (ChatGPT plan) and Grok (SuperGrok / X Premium+) sign in with an
account, not an API key, and their model list changes with the plan and over
time. Once signed in, the list comes from the subscription itself, exactly
what the official clients show; the provider's built-in list is only the
fallback when the account is signed out or the call fails.

Each answer is kept a few minutes so opening Settings or the model picker
does not hit the subscription every time.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from loguru import logger

_CACHE_TTL_S = 10 * 60
_TIMEOUT_S = 10.0
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

#: Codex backend model list (the one the official Codex client reads).
CODEX_MODELS_URL = "https://chatgpt.com/backend-api/codex/models"
CODEX_CLIENT_VERSION = "0.80.0"
#: Not chat models: never offered as the conversation model.
_NOT_CHAT = ("embed", "image", "imagine", "tts", "whisper", "transcribe", "realtime", "voice", "moderation")


def _cached(key: str) -> list[dict[str, Any]] | None:
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL_S:
        return hit[1]
    return None


def _store(key: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    _cache[key] = (time.monotonic(), rows)
    return rows


def _is_chat(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(token in lowered for token in _NOT_CHAT)


def parse_xai_models(payload: Any) -> list[dict[str, Any]]:
    """Rows from the Grok CLI chat proxy ``/v1/models`` (OpenAI list shape)."""
    rows: list[dict[str, Any]] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    for item in data if isinstance(data, list) else []:
        model_id = str((item or {}).get("id") or "").strip() if isinstance(item, dict) else ""
        if not model_id or not _is_chat(model_id):
            continue
        rows.append({
            "id": f"xai-oauth/{model_id}",
            "label": str(item.get("name") or item.get("display_name") or "") or None,
            "owned_by": "Grok (x.ai subscription)",
            "context_window": item.get("context_window") or item.get("context_length"),
        })
    return rows


def parse_codex_models(payload: Any) -> list[dict[str, Any]]:
    """Rows from the Codex backend: ``{"models": [{"slug", "display_name", "visibility"...}]}``
    (or an OpenAI-style ``data`` list)."""
    rows: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return rows
    items = payload.get("models") if isinstance(payload.get("models"), list) else payload.get("data")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or item.get("id") or "").strip()
        visibility = str(item.get("visibility") or "list").strip().lower()
        if not slug or visibility in {"hide", "hidden", "none"} or not _is_chat(slug):
            continue
        rows.append({
            "id": f"openai-codex/{slug}",
            "label": str(item.get("display_name") or "") or None,
            "description": str(item.get("description") or "") or None,
            "owned_by": "OpenAI Codex",
            "context_window": item.get("context_window"),
        })
    return rows


def xai_oauth_models(*, proxy: str | None = None) -> list[dict[str, Any]] | None:
    """Grok subscription models, or None when signed out / unreachable."""
    cached = _cached("xai_oauth")
    if cached is not None:
        return cached
    from navin.providers.xai_oauth_provider import (
        DEFAULT_API_BASE,
        _resolve,
        get_valid_xai_oauth_token,
        get_xai_oauth_login_status,
        subscription_headers,
    )

    if get_xai_oauth_login_status() is None:
        return None
    try:
        token = get_valid_xai_oauth_token()
        base = _resolve("GROK_CLI_CHAT_PROXY_BASE_URL", DEFAULT_API_BASE).rstrip("/")
        response = httpx.get(
            f"{base}/models",
            headers={**subscription_headers(), "Authorization": f"Bearer {token.access}"},
            timeout=_TIMEOUT_S,
            proxy=proxy,
        )
        response.raise_for_status()
        rows = parse_xai_models(response.json())
    except Exception as exc:  # noqa: BLE001 - the built-in list stays as the fallback
        logger.info("Grok subscription model list unavailable: {}", str(exc)[:200])
        return None
    return _store("xai_oauth", rows) if rows else None


def openai_codex_models(*, proxy: str | None = None) -> list[dict[str, Any]] | None:
    """Codex (ChatGPT plan) models, or None when signed out / unreachable."""
    cached = _cached("openai_codex")
    if cached is not None:
        return cached
    from navin.providers.openai_codex_provider import DEFAULT_ORIGINATOR, codex_token_storage

    stored = codex_token_storage().load()
    if not stored or not getattr(stored, "access", None):
        return None
    try:
        from oauth_cli_kit import get_token as get_codex_token

        token = get_codex_token(storage=codex_token_storage(), proxy=proxy)
        response = httpx.get(
            CODEX_MODELS_URL,
            params={"client_version": CODEX_CLIENT_VERSION},
            headers={
                "Authorization": f"Bearer {token.access}",
                "chatgpt-account-id": token.account_id,
                "originator": DEFAULT_ORIGINATOR,
                "User-Agent": "navin (python)",
                "accept": "application/json",
            },
            timeout=_TIMEOUT_S,
            proxy=proxy,
        )
        response.raise_for_status()
        rows = parse_codex_models(response.json())
    except Exception as exc:  # noqa: BLE001 - the built-in list stays as the fallback
        logger.info("Codex subscription model list unavailable: {}", str(exc)[:200])
        return None
    return _store("openai_codex", rows) if rows else None


LIVE_SUBSCRIPTION_LISTS = {"xai_oauth": xai_oauth_models, "openai_codex": openai_codex_models}
