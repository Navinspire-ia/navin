# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings → Channels rows for Marketing social OAuth accounts.

These are not inbound chat listeners. Turning one On writes
``publish.<provider>.enabled`` so the Marketing desk, growth loop, and the
``marketing`` agent tool can publish with the same tokens.
"""

from __future__ import annotations

from typing import Any

from navin.marketing.oauth import SOCIAL_PROVIDERS, connection_status
from navin.marketing.store import MarketingStore

SOCIAL_CHANNEL_NAMES = frozenset(SOCIAL_PROVIDERS)
_DISPLAY = {
    "reddit": "Reddit",
    "linkedin": "LinkedIn",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "tiktok": "TikTok",
}
_CONNECTED = frozenset({"connected", "refresh_required"})


def is_social_channel(name: str) -> bool:
    return str(name or "").strip().lower() in SOCIAL_CHANNEL_NAMES


def _publish_row(store: MarketingStore, provider: str) -> dict[str, Any]:
    settings = store.load_settings()
    row = (settings.get("publish") or {}).get(provider) or {}
    return row if isinstance(row, dict) else {}


def social_feature(store: MarketingStore, provider: str) -> dict[str, Any]:
    provider = str(provider or "").strip().lower()
    status = connection_status(store, provider)
    publish = _publish_row(store, provider)
    connected = status["status"] in _CONNECTED
    enabled = bool(publish.get("enabled")) and connected
    secret_ok = bool(status["client_secret_set"]) or provider == "reddit"
    app_ready = bool(status["client_id"] and status["redirect_uri"] and secret_ok)
    config_values: dict[str, str] = {}
    configured_fields: list[str] = []
    if status["client_id"]:
        config_values["client_id"] = str(status["client_id"])
        configured_fields.append("client_id")
    if status["redirect_uri"]:
        config_values["redirect_uri"] = str(status["redirect_uri"])
        configured_fields.append("redirect_uri")
    if provider == "reddit" and publish.get("subreddit"):
        config_values["subreddit"] = str(publish["subreddit"])
        configured_fields.append("subreddit")
    ready = enabled and connected
    return {
        "name": provider,
        "display_name": _DISPLAY.get(provider, provider.title()),
        "type": "channel",
        "kind": "social",
        "enabled": enabled,
        "configured": connected,
        "installed": True,
        "ready": ready,
        "status": "enabled" if ready else "not_enabled",
        "install_supported": True,
        "requires_restart": False,
        "oauth_status": status["status"],
        "oauth_account": status["account"],
        "oauth_account_id": status["account_id"],
        "oauth_accounts": status["accounts"],
        "client_secret_set": status["client_secret_set"],
        "docs_url": status["docs_url"],
        "last_error": status["last_error"],
        "app_ready": app_ready,
        "suggested_redirect_uri": status.get("suggested_redirect_uri") or "",
        "config_values": config_values,
        "configured_fields": configured_fields,
    }


def social_features(store: MarketingStore | None = None) -> list[dict[str, Any]]:
    desk = store or MarketingStore()
    return [social_feature(desk, name) for name in SOCIAL_PROVIDERS]


def set_social_channel_enabled(name: str, enabled: bool, *, store: MarketingStore | None = None) -> dict[str, Any]:
    from navin.optional_features import OptionalFeatureError

    provider = str(name or "").strip().lower()
    if not is_social_channel(provider):
        raise OptionalFeatureError(f"Unknown social channel: {name}", status=404)
    desk = store or MarketingStore()
    status = connection_status(desk, provider)
    if enabled and status["status"] not in _CONNECTED:
        raise OptionalFeatureError(
            f"Connect { _DISPLAY.get(provider, provider) } with OAuth first, then turn it on for agents.",
            status=400,
        )
    desk.save_settings({"publish": {provider: {"enabled": bool(enabled)}}})
    return social_feature(desk, provider)


def social_ready_summary(store: MarketingStore | None = None) -> list[str]:
    """One line per network for the marketing agent tool."""
    lines: list[str] = []
    for row in social_features(store):
        flag = "on" if row["enabled"] else "off"
        account = f" @{row['oauth_account']}" if row.get("oauth_account") else ""
        lines.append(f"{row['name']}={row['oauth_status']}/{flag}{account}")
    return lines
