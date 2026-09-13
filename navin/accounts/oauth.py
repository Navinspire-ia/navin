"""Desktop authorization code + PKCE, using a single-use loopback callback."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from navin.accounts.providers import PROVIDERS, ProviderError, registration, request, scopes_for
from navin.accounts.store import DEFAULT_PERMISSIONS, AccountError, AccountStore

_flows: dict[str, dict[str, Any]] = {}
_flow_lock = threading.RLock()
TTL = 600


def _granted(provider: str, permissions: dict[str, bool], scopes: list[str]) -> dict[str, bool]:
    granted = set(scopes)
    if provider == "microsoft":
        granted.update(scope.rsplit("/", 1)[-1] for scope in scopes)
    return {key: bool(value and PROVIDERS[provider]["scopes"][key] in granted) for key, value in permissions.items()}


def finish(store: AccountStore, flow: dict[str, Any], code: str) -> str:
    provider = flow["provider"]
    app = registration(provider)
    data = {"grant_type": "authorization_code", "code": code, "client_id": app["client_id"],
            "redirect_uri": flow["redirect_uri"], "code_verifier": flow["verifier"]}
    if app["client_secret"]:
        data["client_secret"] = app["client_secret"]
    tokens = request("POST", PROVIDERS[provider]["token"], data=data, form=True)
    if not tokens.get("access_token") or not tokens.get("refresh_token"):
        raise AccountError("Offline access was not granted. Reconnect the account and accept the permissions.", 409)
    identity = request("GET", PROVIDERS[provider]["identity"], token=tokens["access_token"])
    subject = str((identity.get("sub") if provider == "google" else identity.get("id")) or "")
    address = str(identity.get("email") if provider == "google" else identity.get("mail") or identity.get("userPrincipalName") or "")
    if not subject or "@" not in address or (provider == "google" and identity.get("email_verified") is not True):
        raise AccountError("The account identity could not be verified.", 403)
    account_id = provider + "-" + hashlib.sha256(subject.encode()).hexdigest()[:24]
    scopes = str(tokens.get("scope") or " ".join(flow["scopes"])).split()
    permissions = _granted(provider, flow["permissions"], scopes)
    with store.lock():
        state = store.load()
        store.vault.put(account_id, {"refresh_token": tokens["refresh_token"], "access_token": tokens["access_token"],
                                     "expires_at": time.time() + int(tokens.get("expires_in") or 3600)})
        state["accounts"][account_id] = {"id": account_id, "provider": provider, "email": address,
            "name": str(identity.get("name") or identity.get("displayName") or address), "permissions": permissions,
            "scopes": scopes, "connected_at": time.time()}
        store.save(state)
    return account_id


def start(store: AccountStore, provider: str, permissions: dict[str, Any] | None = None, *, locale: str = "en") -> dict[str, Any]:
    app = registration(provider)
    if not app["client_id"]:
        raise AccountError("The " + PROVIDERS[provider]["name"] + " connection must be enabled by Navinspire in this Navin build.", 503)
    if not store.vault.ready():
        raise AccountError("Unlock the system credential vault to connect your account.", 503)
    allowed = {key: (permissions or {}).get(key, default) is True for key, default in DEFAULT_PERMISSIONS.items()}
    french = str(locale).lower().startswith("fr")
    flow_id, state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    # Draft permissions may include provider-level sending, but local autonomous
    # sending remains off. This supports exact, one-time user approvals.
    requested = {**allowed, "send_mail": allowed["send_mail"] or allowed["draft_mail"]}
    flow: dict[str, Any] = {"id": flow_id, "provider": provider, "state": state, "verifier": verifier,
                            "permissions": allowed, "scopes": scopes_for(provider, requested),
                            "expires_at": time.time() + TTL, "status": "pending", "root": str(store.root.resolve())}

    class Callback(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *args):
            pass  # The authorization code must never enter HTTP logs.

        def do_GET(self):
            parts = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parts.query)
            expected_host = urllib.parse.urlsplit(flow["redirect_uri"]).netloc
            with _flow_lock:
                valid = (parts.path == "/callback" and self.headers.get("Host") == expected_host
                         and flow["status"] == "pending" and flow["expires_at"] > time.time()
                         and hmac.compare_digest(query.get("state", [""])[0], state))
                if valid:
                    flow["status"] = "exchanging"
            if not valid:
                self.send_error(400, "Invalid authorization callback")
                return
            try:
                if query.get("error") or not query.get("code"):
                    raise AccountError("Connection cancelled or denied.")
                account_id = finish(store, flow, query["code"][0])
                flow.update({"status": "connected", "account_id": account_id})
                text = "Compte connecté. Vous pouvez revenir dans Navin." if french else "Account connected. You can return to Navin."
            except Exception as exc:
                text = str(exc) if isinstance(exc, AccountError) else "Connection failed. Try again in Navin."
                if french:
                    text = {
                        "Connection cancelled or denied.": "Connexion annulée ou autorisation refusée.",
                        "Offline access was not granted. Reconnect the account and accept the permissions.": "L'accès hors connexion n'a pas été autorisé. Reconnectez le compte et acceptez les permissions.",
                        "The account identity could not be verified.": "L'identité du compte n'a pas pu être vérifiée.",
                        "Tokens could not be saved in the local secure credential vault.": "Les autorisations n'ont pas pu être enregistrées dans le trousseau local. Vérifiez qu'il est déverrouillé.",
                    }.get(text, "La connexion a échoué. Réessayez dans Navin.")
                flow.update({"status": "failed", "error": text})
            import html
            language, title = ("fr", "Connexion") if french else ("en", "Connection")
            body = (f"<!doctype html><html lang='{language}'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
                    f"<title>Navin - {title}</title><body><h1>Navin</h1><p>" + html.escape(text) + "</p></body></html>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), Callback)
    server.timeout = 1
    host = "127.0.0.1" if provider == "google" else "localhost"
    flow["redirect_uri"] = f"http://{host}:{server.server_port}/callback"
    with _flow_lock:
        for old_id, old in list(_flows.items()):
            if old["expires_at"] < time.time():
                _flows.pop(old_id, None)
        if len(_flows) >= 8:
            server.server_close()
            raise AccountError("Complete or cancel the current connection first.", 409)
        _flows[flow_id] = flow

    def listen():
        try:
            while flow["status"] == "pending" and time.time() < flow["expires_at"]:
                server.handle_request()
            if flow["status"] == "pending":
                flow.update({"status": "expired", "error": "La connexion a expiré. Réessayez." if french else "Connection expired. Try again."})
        finally:
            flow.pop("verifier", None)
            flow.pop("state", None)
            server.server_close()

    threading.Thread(target=listen, name="navin-account-oauth", daemon=True).start()
    params = {"client_id": app["client_id"], "redirect_uri": flow["redirect_uri"], "response_type": "code",
              "scope": " ".join(flow["scopes"]), "state": state, "code_challenge_method": "S256",
              "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")}
    if provider == "google":
        params.update({"access_type": "offline", "prompt": "consent select_account"})
    else:
        params["prompt"] = "select_account"
    return {"flow_id": flow_id, "url": PROVIDERS[provider]["auth"] + "?" + urllib.parse.urlencode(params)}


def flow_status(store: AccountStore, flow_id: str, *, cancel: bool = False) -> dict[str, Any]:
    with _flow_lock:
        flow = _flows.get(flow_id, {})
        if flow.get("root") != str(store.root.resolve()):
            raise AccountError("Connection not found.", 404)
        if cancel and flow.get("status") == "pending":
            flow["status"] = "cancelled"
        return {key: flow[key] for key in ("status", "account_id", "error", "expires_at") if key in flow}


def access_token(store: AccountStore, account_id: str) -> str:
    with store.lock():
        account = store.account(account_id)
        saved = store.vault.get(account_id)
        if saved.get("access_token") and float(saved.get("expires_at") or 0) > time.time() + 60:
            return str(saved["access_token"])
        if not saved.get("refresh_token"):
            raise AccountError("Reconnect this account in Navin.", 401)
        app = registration(account["provider"])
        data = {"client_id": app["client_id"], "grant_type": "refresh_token", "refresh_token": saved["refresh_token"]}
        if app["client_secret"]:
            data["client_secret"] = app["client_secret"]
        try:
            tokens = request("POST", PROVIDERS[account["provider"]]["token"], data=data, form=True)
        except ProviderError as exc:
            if exc.status in {400, 401}:
                raise AccountError("Authorization expired or was revoked. Reconnect the account.", 401) from None
            raise
        if not tokens.get("access_token"):
            raise AccountError("The account token could not be refreshed.", 401)
        store.vault.put(account_id, {"refresh_token": tokens.get("refresh_token") or saved["refresh_token"],
                                    "access_token": tokens["access_token"], "expires_at": time.time() + int(tokens.get("expires_in") or 3600)})
        return str(tokens["access_token"])
