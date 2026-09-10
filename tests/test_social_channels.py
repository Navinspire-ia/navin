# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import pytest

from navin.marketing import oauth
from navin.marketing.social_channels import (
    is_social_channel,
    set_social_channel_enabled,
    social_feature,
    social_features,
    social_ready_summary,
)
from navin.marketing.store import MarketingStore
from navin.optional_features import OptionalFeatureError, optional_features_payload


def _connect(store: MarketingStore, provider: str = "reddit") -> None:
    oauth.configure_connection(store, provider, {
        "client_id": "app-123",
        "client_secret": "private-client-secret",
        "redirect_uri": "https://navin.example.com" + oauth.CALLBACK_PATH,
    })
    store.save_secret(oauth._TOKEN_KEYS[provider], "private-access-token")
    oauth._update(store, provider, {"status": "connected", "account": "demo"})


def test_social_feature_stays_off_until_oauth_and_toggle(tmp_path):
    store = MarketingStore(tmp_path)
    row = social_feature(store, "instagram")
    assert row["kind"] == "social" and row["enabled"] is False and row["configured"] is False
    assert row["suggested_redirect_uri"] == ""
    reddit = social_feature(store, "reddit")
    assert reddit["suggested_redirect_uri"].endswith(oauth.CALLBACK_PATH)
    assert ":5173" not in reddit["suggested_redirect_uri"]
    with pytest.raises(OptionalFeatureError, match="Connect"):
        set_social_channel_enabled("instagram", True, store=store)
    _connect(store, "instagram")
    set_social_channel_enabled("instagram", True, store=store)
    on = social_feature(store, "instagram")
    assert on["enabled"] is True and on["ready"] is True
    assert store.load_settings()["publish"]["instagram"]["enabled"] is True
    assert "instagram=connected/on @demo" in social_ready_summary(store)


def test_optional_features_include_marketing_social_networks():
    names = {row["name"] for row in optional_features_payload()["features"]}
    assert {"reddit", "linkedin", "instagram", "facebook", "tiktok"} <= names
    assert is_social_channel("tiktok")
    assert not is_social_channel("telegram")


def test_social_features_list_all_providers(tmp_path):
    names = [row["name"] for row in social_features(MarketingStore(tmp_path))]
    assert names == list(oauth.SOCIAL_PROVIDERS)
