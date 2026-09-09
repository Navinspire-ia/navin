# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Provider contracts and credential boundaries for the social login round-trip."""

from __future__ import annotations

import json
import stat
import time
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from navin.marketing import oauth
from navin.marketing.errors import MarketingError
from navin.marketing.store import MarketingStore


def configured(tmp_path, provider):
    store = MarketingStore(tmp_path)
    oauth.configure_connection(store, provider, {
        "client_id": "app-123", "client_secret": "private-client-secret",
        "redirect_uri": "https://navin.example.com" + oauth.CALLBACK_PATH,
    })
    return store


def pending(store, provider):
    start = oauth.start_connection(store, provider, session_key="websocket:test-social")
    query = parse_qs(urlsplit(start["authorization_url"]).query)
    state = query["state"][0]
    cookie = start["_set_cookie"].split(";", 1)[0]
    return start, {"state": state, "code": "private-code"}, cookie


@pytest.mark.parametrize("provider", oauth.SOCIAL_PROVIDERS)
def test_provider_authorization_uses_exact_scopes_and_private_server_exchange(tmp_path, provider):
    store = configured(tmp_path, provider)
    store.save_settings({"publish": {provider: {"enabled": True}}})
    start, query, cookie = pending(store, provider)
    params = parse_qs(urlsplit(start["authorization_url"]).query)
    assert "private-client-secret" not in start["authorization_url"]
    assert params["state"] == [query["state"]]
    assert "HttpOnly" in start["_set_cookie"] and "SameSite=Lax" in start["_set_cookie"]
    scope = ",".join(oauth._SCOPES[provider])
    token = {"access_token": "private-access-token", "expires_in": 3600, "scope": scope}
    if provider in {"reddit", "linkedin", "tiktok"}:
        token["refresh_token"] = "private-refresh-token"
    identity = {
        "reddit": {"id": "123", "name": "fictional-author"},
        "linkedin": {"sub": "123", "name": "Fictional Author"},
        "instagram": {"user_id": "123", "username": "fictional-author"},
        "facebook": {"data": [{"id": "123", "name": "Fictional Page"}, {"id": "456", "name": "Second Page"}]},
        "tiktok": {"data": {"user": {"open_id": "123", "display_name": "Fictional Author"}}, "error": {"code": "ok"}},
    }[provider]
    responses = [token, identity]
    if provider == "instagram":
        responses = [token, {"access_token": "private-long-token", "expires_in": 5184000}, identity]
    http = Mock(side_effect=responses)
    result = oauth.finish_connection(store, query, cookie, http=http)
    assert result["session_key"] == "websocket:test-social"
    status = result["connection"]
    assert status["status"] == ("select_account" if provider == "facebook" else "connected")
    assert not store.load_settings()["publish"][provider]["enabled"], "A connection must not activate old queued posts"
    assert status["granted_scopes"] == list(oauth._SCOPES[provider])
    public = json.dumps(oauth.connections_status(store))
    for value in ("private-client-secret", "private-access-token", "private-refresh-token", "private-long-token", "private-code"):
        assert value not in public
    assert stat.S_IMODE(store.secrets_path().stat().st_mode) == 0o600
    if provider == "reddit":
        assert params["duration"] == ["permanent"]
        assert http.call_args_list[0].kwargs["headers"]["Authorization"].startswith("Basic ")
    if provider == "linkedin":
        assert store.load_settings()["publish"][provider]["author"] == "urn:li:person:123"
    with pytest.raises(MarketingError, match="already used"):
        oauth.finish_connection(store, query, cookie, http=Mock())


def test_facebook_page_choice_does_not_expose_tokens_or_choose_first_page(tmp_path):
    store = configured(tmp_path, "facebook")
    store.save_secret("facebook_page_token", "old-page-token")
    _, query, cookie = pending(store, "facebook")
    http = Mock(side_effect=[{"access_token": "user-token", "expires_in": 5000},
                             {"data": [{"id": "123", "name": "A"}, {"id": "456", "name": "B"}]}])
    oauth.finish_connection(store, query, cookie, http=http)
    assert store.get_secret("facebook_page_token") == ""
    assert oauth.connection_status(store, "facebook")["status"] == "select_account"
    with pytest.raises(MarketingError, match="Choose one"):
        oauth.select_account(store, "facebook", "999", http=Mock())
    chosen = oauth.select_account(store, "facebook", "456", http=Mock(return_value={"id": "456", "name": "B", "access_token": "chosen-page-token"}))
    assert chosen["status"] == "connected" and chosen["account_id"] == "456"
    assert store.get_secret("facebook_page_token") == "chosen-page-token"
    assert store.load_settings()["publish"]["facebook"]["page_id"] == "456"


def test_cookie_binding_blocks_cross_browser_callback_before_exchange(tmp_path):
    store = configured(tmp_path, "reddit")
    _, query, _ = pending(store, "reddit")
    http = Mock()
    with pytest.raises(MarketingError, match="browser"):
        oauth.finish_connection(store, query, "", http=http)
    http.assert_not_called()
    assert not store.get_secret("reddit_access_token")


def test_missing_scope_never_commits_a_token(tmp_path):
    store = configured(tmp_path, "tiktok")
    _, query, cookie = pending(store, "tiktok")
    with pytest.raises(MarketingError, match="permissions"):
        oauth.finish_connection(store, query, cookie, http=Mock(return_value={"access_token": "denied-token", "scope": "user.info.basic"}))
    assert not store.get_secret("tiktok_access_token")


def test_refresh_rotates_both_tiktok_tokens_and_keeps_other_secrets(tmp_path):
    store = configured(tmp_path, "tiktok")
    store.save_secrets({"tiktok_access_token": "old-access", "tiktok_refresh_token": "old-refresh", "linkedin_token": "other-network"})
    oauth._update(store, "tiktok", {"status": "connected", "expires_at": time.time() - 5, "refreshable": True})
    http = Mock(return_value={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 86400})
    assert oauth.ensure_access_token(store, "tiktok", http=http) == "new-access"
    assert http.call_args.kwargs["data"]["refresh_token"] == "old-refresh"
    assert store.get_secret("tiktok_refresh_token") == "new-refresh"
    assert store.get_secret("linkedin_token") == "other-network"
    assert oauth.connection_status(store, "tiktok")["expires_at"] > time.time()


def test_disconnect_disables_publishing_removes_tokens_and_invalidates_pending_callbacks(tmp_path):
    store = configured(tmp_path, "reddit")
    store.save_secrets({"reddit_access_token": "old-access", "reddit_refresh_token": "old-refresh"})
    store.save_settings({"publish": {"reddit": {"enabled": True}}})
    _, query, cookie = pending(store, "reddit")
    result = oauth.disconnect(store, "reddit")
    assert result["status"] == "not_connected" and result["remote_revoked"] is False
    assert not store.get_secret("reddit_access_token") and not store.get_secret("reddit_refresh_token")
    assert not store.load_settings()["publish"]["reddit"]["enabled"]
    with pytest.raises(MarketingError, match="already used"):
        oauth.finish_connection(store, query, cookie, http=Mock())


@pytest.mark.parametrize("uri", ["https://user:pass@example.com" + oauth.CALLBACK_PATH,
                                  "https://example.com" + oauth.CALLBACK_PATH + "?next=evil",
                                  "javascript:alert(1)", "http://127.0.0.1" + oauth.CALLBACK_PATH])
def test_invalid_callback_cannot_be_configured(tmp_path, uri):
    with pytest.raises(MarketingError):
        oauth.configure_connection(MarketingStore(tmp_path), "tiktok", {"client_id": "app-123", "redirect_uri": uri})


def test_app_rebinding_clears_old_authorization(tmp_path):
    store = configured(tmp_path, "linkedin")
    store.save_secret("linkedin_token", "old-account")
    _, query, cookie = pending(store, "linkedin")
    oauth.configure_connection(store, "linkedin", {"client_id": "other-app", "redirect_uri": "https://navin.example.com" + oauth.CALLBACK_PATH})
    assert not store.get_secret("linkedin_token")
    with pytest.raises(MarketingError, match="already used"):
        oauth.finish_connection(store, query, cookie, http=Mock())
