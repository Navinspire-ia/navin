from __future__ import annotations

import base64
import json
from unittest.mock import patch

import pytest

from navin.accounts.oauth import access_token, finish
from navin.accounts.providers import capabilities, scopes_for
from navin.accounts.service import execute, send_mime
from navin.accounts.store import DEFAULT_PERMISSIONS, AccountError, AccountStore
from navin.webui.accounts_api import account_snapshot, handle_accounts_action


class MemoryVault:
    def __init__(self):
        self.values = {}

    def ready(self):
        return True

    def get(self, key):
        return self.values.get(key, {})

    def put(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture
def store(tmp_path):
    store = AccountStore(tmp_path / "accounts")
    store.vault = MemoryVault()
    return store


def connect(store, provider="google", permissions=None):
    allowed = {**DEFAULT_PERMISSIONS, **(permissions or {})}
    scopes = scopes_for(provider, {key: True for key in DEFAULT_PERMISSIONS})
    flow = {"provider": provider, "redirect_uri": "http://127.0.0.1:54321/callback", "verifier": "proof-key",
            "scopes": scopes, "permissions": allowed}
    tokens = {"access_token": "secret-access", "refresh_token": "secret-refresh", "expires_in": 3600, "scope": " ".join(scopes)}
    identity = {"sub": "subject-1", "email": "user@example.com", "email_verified": True} if provider == "google" else {"id": "subject-1", "mail": "user@example.com"}
    with patch("navin.accounts.oauth.request", side_effect=[tokens, identity]), patch("navin.accounts.oauth.registration", return_value={"client_id": "publisher", "client_secret": ""}):
        account_id = finish(store, flow, "authorization-code")
    return account_id


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_tokens_are_local_only_and_default_permissions_prevent_autonomous_sends(store, provider):
    account_id = connect(store, provider)
    public = account_snapshot(store)
    assert public["local_tokens"] and not public["cloud_relay"]
    assert not public["accounts"][0]["permissions"]["send_mail"]
    assert store.vault.get(account_id)["refresh_token"] == "secret-refresh"
    assert "secret-refresh" not in json.dumps(public)
    assert "secret-access" not in (store.root / "accounts.json").read_text()
    with pytest.raises(AccountError, match="permissions"):
        send_mime(store, account_id, b"Subject: x\r\n\r\ny")


def test_refresh_rotation_is_saved_without_returning_refresh_tokens(store):
    account_id = connect(store)
    store.vault.values[account_id]["expires_at"] = 0
    with patch("navin.accounts.oauth.registration", return_value={"client_id": "publisher", "client_secret": ""}), patch("navin.accounts.oauth.request", return_value={"access_token": "renewed", "refresh_token": "rotated", "expires_in": 3600}) as http:
        assert access_token(store, account_id) == "renewed"
        assert access_token(store, account_id) == "renewed"
        assert http.call_count == 1
    assert store.vault.get(account_id)["refresh_token"] == "rotated"
    assert "rotated" not in (store.root / "accounts.json").read_text()


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_exact_send_requires_ui_approval_and_retries_do_not_resend(store, provider):
    account_id = connect(store, provider)
    body = {"to": "client@example.com", "subject": "Interview", "text": "The proposed slot is 10:00 UTC.", "request_id": "interview-1"}
    with patch("navin.accounts.service.request", return_value={"id": "provider-message"}) as http:
        pending = execute(store, account_id, "mail.send", body)
        assert pending["status"] == "approval_required"
        http.assert_not_called()
        approved = handle_accounts_action("approve", {"approval_id": pending["approval_id"]}, store=store)
        assert approved["result"]["status"] == "accepted"
        assert execute(store, account_id, "mail.send", body)["status"] == "accepted"
        assert http.call_count == 1
        payload = http.call_args.kwargs["data"]
        raw = base64.urlsafe_b64decode(payload["raw"]) if provider == "google" else base64.b64decode(payload)
        assert b"client@example.com" in raw and b"10:00 UTC" in raw
    assert not store.account(account_id)["permissions"]["send_mail"]
    with pytest.raises(AccountError, match="different action"):
        execute(store, account_id, "mail.send", {**body, "to": "other@example.com"})


def test_rejection_and_unknown_acceptance_never_resend(store):
    account_id = connect(store)
    body = {"to": "client@example.com", "subject": "Proposal", "text": "Details", "request_id": "proposal"}
    pending = execute(store, account_id, "mail.send", body)
    handle_accounts_action("reject", {"approval_id": pending["approval_id"]}, store=store)
    with patch("navin.accounts.service.request") as http:
        assert execute(store, account_id, "mail.send", body)["status"] == "rejected"
        http.assert_not_called()
    body["request_id"] = "different-proposal"
    pending = execute(store, account_id, "mail.send", body)
    with patch("navin.accounts.service.request", side_effect=AccountError("Connection lost", 503)) as http:
        result = handle_accounts_action("approve", {"approval_id": pending["approval_id"]}, store=store)
        assert result["result"]["status"] == "unknown"
        assert execute(store, account_id, "mail.send", body)["status"] == "unknown"
        assert http.call_count == 1


def test_disconnect_removes_tokens_and_cancels_pending_actions(store):
    account_id = connect(store)
    execute(store, account_id, "mail.send", {"to": "client@example.com", "text": "Draft", "request_id": "pending"})
    handle_accounts_action("disconnect", {"account_id": account_id}, store=store)
    assert not store.vault.get(account_id)
    assert not account_snapshot(store)["accounts"]
    assert not account_snapshot(store)["pending"]


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_calendar_dates_and_provider_payload_preserve_the_instant(store, provider):
    account_id = connect(store, provider, {"write_calendar": True})
    body = {"title": "Interview", "start": "2027-09-15T14:00:00+04:00", "end": "2027-09-15T15:00:00+04:00", "request_id": "event-1"}
    with patch("navin.accounts.service.request", return_value={"id": "event-1"}) as http:
        result = execute(store, account_id, "calendar.create", body)
    assert result["status"] == "accepted"
    sent = http.call_args.kwargs["data"]
    assert sent["start"]["dateTime"] == (body["start"] if provider == "google" else "2027-09-15T10:00:00")
    assert "attendees" not in sent
    with patch("navin.accounts.service.request") as http:
        result = execute(store, account_id, "calendar.create", {**body, "start": "2027-09-15T14:00", "request_id": "bad-date"})
        assert result["status"] != "accepted" and "timezone" in result["error"]
        http.assert_not_called()


def test_grants_cannot_be_elevated_without_provider_consent(store):
    account_id = connect(store)
    state = store.load()
    state["accounts"][account_id]["scopes"] = scopes_for("google", {"read_mail": True})
    store.save(state)
    with pytest.raises(AccountError, match="Reconnect"):
        handle_accounts_action("permissions", {"account_id": account_id, "permissions": {"send_mail": True}}, store=store)
    assert capabilities("google", ["https://www.googleapis.com/auth/gmail.compose"])["send_mail"]


def test_vault_refuses_plaintext_backends(store):
    from navin.accounts.store import TokenVault

    with patch("keyring.get_keyring", return_value=object()):
        assert not TokenVault(str(store.root)).ready()
        with pytest.raises(AccountError, match="vault"):
            TokenVault(str(store.root)).put("account", {"refresh_token": "secret"})


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_career_uses_official_apis_to_contact_and_process_the_candidates_reply(store, provider, tmp_path):
    from email.message import EmailMessage

    from navin.career.mail import load_mail_state
    from navin.career.mailbox import sync_mailbox
    from navin.career.prospecting import _match, _save, _state, handle_prospecting
    from navin.career.sourcing_mail import _send
    from navin.career.store import CareerStore

    account_id = connect(store, provider, {"send_mail": True})
    career = CareerStore(tmp_path / "career")
    handle_prospecting(career, "prospecting_config", {"activate_company": True, "criteria": {
        "company": {"name": "Agency"}, "roles": ["Nurse"], "skills": ["care"], "countries": ["MA"], "auto_contact": True}})
    career.save_profile({"mailbox": {"enabled": True, "account_id": account_id, "sender_email": "user@example.com", "read_replies": True}})
    offer = {"id": "nurse-job", "title": "Nurse", "description": "Care", "stack": ["care"], "country": "MA", "url": "https://example.com/nurse"}
    career.upsert_opportunities([offer])
    state = _state(career)
    state["candidates"] = [{"id": "nurse", "name": "Candidate", "headline": "Nurse", "skills": ["care"], "country": "MA", "email": "nurse@example.com"}]
    _match(career, state, offer)
    _save(career, state)
    match = state["matches"][offer["id"]]["results"][0]
    with patch("navin.accounts.store.AccountStore", return_value=store), patch("navin.accounts.career.AccountStore", return_value=store), patch("navin.accounts.service.request", return_value={"id": "sent-message"}) as http:
        receipt = _send(career, state, match, offer, "candidate")
        _save(career, state)
    assert receipt["status"] == "accepted"
    assert "/messages/send" in http.call_args.args[1] if provider == "google" else "/sendMail" in http.call_args.args[1]
    record = next(iter(load_mail_state(career)["outbox"].values()))
    reply = EmailMessage()
    reply["From"], reply["To"] = "nurse@example.com", "user@example.com"
    reply["Subject"] = "Re: " + record["subject"]
    reply["In-Reply-To"], reply["Message-ID"] = record["message_id"], "<reply-api@example.com>"
    reply.set_content("I am not interested in this mission. Please contact another candidate.")
    raw = reply.as_bytes()
    page = {"messages" if provider == "google" else "value": [{"id": "reply-1"}]}
    content = {"raw": base64.urlsafe_b64encode(raw).decode()} if provider == "google" else raw
    with patch("navin.accounts.career.AccountStore", return_value=store), patch("navin.accounts.career.api", return_value=page), patch("navin.accounts.service.request", return_value=content), patch("navin.career.notify.deliver_alert"):
        first = sync_mailbox(career)
        second = sync_mailbox(career)
    assert first["received"] == 1 and second["received"] == 0, (first, second)
    assert _state(career)["matches"][offer["id"]]["results"][0]["stage"] == "rejected"


@pytest.fixture
def publisher_config(tmp_path, monkeypatch):
    from navin.accounts import publisher

    monkeypatch.setattr(publisher, "REGISTRATION_FILE", tmp_path / "publisher.json")
    for name in ("NAVIN_GOOGLE_CLIENT_ID", "NAVIN_GOOGLE_CLIENT_SECRET", "NAVIN_MICROSOFT_CLIENT_ID"):
        monkeypatch.delenv(name, raising=False)
    return publisher


def test_release_bundles_the_publisher_ids_without_requiring_end_user_configuration(publisher_config, tmp_path, monkeypatch):
    from navin.accounts.providers import registration

    google = "123-preview.apps.googleusercontent.com"
    microsoft = "12345678-1234-1234-1234-123456789abc"
    monkeypatch.setenv("NAVIN_GOOGLE_CLIENT_ID", google)
    monkeypatch.setenv("NAVIN_GOOGLE_CLIENT_SECRET", "desktop-registration-parameter")
    monkeypatch.setenv("NAVIN_MICROSOFT_CLIENT_ID", microsoft)
    staged = publisher_config.bundle_registration(tmp_path / "bundle")
    assert len(staged) == 1 and staged[0][1] == "navin/accounts"
    for name in ("NAVIN_GOOGLE_CLIENT_ID", "NAVIN_GOOGLE_CLIENT_SECRET", "NAVIN_MICROSOFT_CLIENT_ID"):
        monkeypatch.delenv(name)
    from pathlib import Path

    monkeypatch.setattr(publisher_config, "REGISTRATION_FILE", Path(staged[0][0]))
    assert registration("google") == {"client_id": google, "client_secret": "desktop-registration-parameter"}
    assert registration("microsoft") == {"client_id": microsoft, "client_secret": ""}
    monkeypatch.setenv("NAVIN_GOOGLE_CLIENT_ID", "456-another-app.apps.googleusercontent.com")
    assert not registration("google")["client_secret"]


def test_publisher_import_accepts_only_desktop_registration_and_never_user_tokens(publisher_config, tmp_path, monkeypatch, capsys):
    import sys

    downloaded = tmp_path / "downloaded.json"
    downloaded.write_text(json.dumps({"installed": {"client_id": "123-preview.apps.googleusercontent.com",
        "client_secret": "desktop-parameter", "refresh_token": "must-not-be-published", "project_id": "project"}}))
    monkeypatch.setattr(sys, "argv", ["publisher", "--google-client-json", str(downloaded)])
    publisher_config.main()
    assert json.loads(capsys.readouterr().out)["google"]["configured"]
    assert "must-not-be-published" not in publisher_config.REGISTRATION_FILE.read_text()
    before = publisher_config.REGISTRATION_FILE.read_bytes()
    downloaded.write_text(json.dumps({"web": {"client_id": "123-web.apps.googleusercontent.com"}}))
    with pytest.raises(SystemExit):
        publisher_config.main()
    assert publisher_config.REGISTRATION_FILE.read_bytes() == before
    assert "web client or service account" in capsys.readouterr().err


def test_missing_or_invalid_publisher_identity_never_opens_a_browser_flow(store, publisher_config, monkeypatch):
    from navin.accounts.oauth import start

    assert publisher_config.bundle_registration(store.root / "bundle") == []
    with patch("navin.accounts.oauth.HTTPServer") as server:
        with pytest.raises(AccountError, match="enabled by Navinspire"):
            start(store, "google")
        monkeypatch.setenv("NAVIN_GOOGLE_CLIENT_ID", "not-a-google-client-id")
        with pytest.raises(AccountError, match="configuration is invalid"):
            start(store, "google")
        server.assert_not_called()


@pytest.mark.parametrize("provider,locale", [("google", "fr"), ("microsoft", "en")])
def test_browser_loopback_completes_pkce_and_keeps_tokens_in_the_local_vault(store, publisher_config, monkeypatch, provider, locale):
    import hashlib
    import http.client
    from urllib.parse import parse_qs, urlencode, urlsplit

    from navin.accounts.oauth import _flows, flow_status, start

    client_id = "123-preview.apps.googleusercontent.com" if provider == "google" else "12345678-1234-1234-1234-123456789abc"
    monkeypatch.setenv(f"NAVIN_{provider.upper()}_CLIENT_ID", client_id)
    started = start(store, provider, locale=locale)
    query = parse_qs(urlsplit(started["url"]).query)
    callback = urlsplit(query["redirect_uri"][0])

    def visit(params, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", callback.port, timeout=5)
        try:
            connection.request("GET", callback.path + "?" + urlencode(params), headers={"Host": host or callback.netloc})
            response = connection.getresponse()
            return response.status, response.read().decode(), response.getheader("Cache-Control")
        finally:
            connection.close()

    identity = {"sub": "subject", "email": "test@example.com", "email_verified": True} if provider == "google" else {"id": "subject", "mail": "test@example.com"}
    tokens = {"access_token": "private-access-token", "refresh_token": "private-refresh-token", "scope": query["scope"][0]}
    try:
        assert query["client_id"] == [client_id] and query["code_challenge_method"] == ["S256"]
        with patch("navin.accounts.oauth.request", side_effect=[tokens, identity]) as provider_http:
            assert visit({"state": "wrong", "code": "must-not-exchange"})[0] == 400
            assert visit({"state": query["state"][0], "code": "must-not-exchange"}, host="untrusted.example")[0] == 400
            provider_http.assert_not_called()
            status, html, cache = visit({"state": query["state"][0], "code": "single-use-code"})
            assert status == 200 and cache == "no-store"
            assert ("Compte connecté" if locale == "fr" else "Account connected") in html
            assert f"lang='{locale}'" in html
            assert provider_http.call_count == 2
            exchange = provider_http.call_args_list[0].kwargs["data"]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(exchange["code_verifier"].encode()).digest()).decode().rstrip("=")
            assert challenge == query["code_challenge"][0]
            assert exchange["redirect_uri"] == query["redirect_uri"][0]
            assert exchange["client_id"] == client_id
        result = flow_status(store, started["flow_id"], cancel=True)
        assert result["status"] == "connected"
        assert store.vault.get(result["account_id"])["refresh_token"] == "private-refresh-token"
        public = json.dumps(account_snapshot(store)) + (store.root / "accounts.json").read_text()
        assert all(value not in public for value in ("private-refresh-token", "private-access-token", "single-use-code", exchange["code_verifier"]))
        assert not store.account(result["account_id"])["permissions"]["send_mail"]
    finally:
        flow_status(store, started["flow_id"], cancel=True)
        _flows.pop(started["flow_id"], None)


def test_oauth_denied_consent_is_translated_and_does_not_store_tokens(store, publisher_config, monkeypatch):
    import http.client
    from urllib.parse import parse_qs, urlencode, urlsplit

    from navin.accounts.oauth import _flows, flow_status, start

    monkeypatch.setenv("NAVIN_GOOGLE_CLIENT_ID", "123-preview.apps.googleusercontent.com")
    started = start(store, "google", locale="fr-FR")
    query = parse_qs(urlsplit(started["url"]).query)
    callback = urlsplit(query["redirect_uri"][0])
    connection = http.client.HTTPConnection("127.0.0.1", callback.port, timeout=5)
    try:
        with patch("navin.accounts.oauth.request") as http:
            connection.request("GET", callback.path + "?" + urlencode({"state": query["state"][0], "error": "access_denied"}))
            response = connection.getresponse()
            assert "Connexion annulée ou autorisation refusée" in response.read().decode()
            http.assert_not_called()
        assert flow_status(store, started["flow_id"])["status"] == "failed"
        assert not store.vault.values and not store.load()["accounts"]
    finally:
        connection.close()
        flow_status(store, started["flow_id"], cancel=True)
        _flows.pop(started["flow_id"], None)
