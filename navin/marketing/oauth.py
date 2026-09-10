# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Social account authorization with server-side tokens and one-use callbacks.

The browser authorizes its own app with each provider. A connection never
enables automatic publication. Providers still enforce app review and scopes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from http.cookies import SimpleCookie
from typing import Any

from navin.marketing.errors import MarketingError
from navin.marketing.store import MarketingStore, _atomic_write, _read_json
from navin.utils.atomic_io import InterProcessLock

CALLBACK_PATH = "/api/marketing/oauth/callback"
STATE_TTL = 600
GRAPH_VERSION = "v23.0"
SOCIAL_PROVIDERS = ("reddit", "linkedin", "instagram", "facebook", "tiktok")
_TOKEN_KEYS = {
    "reddit": "reddit_access_token", "linkedin": "linkedin_token",
    "instagram": "instagram_access_token", "facebook": "facebook_page_token",
    "tiktok": "tiktok_access_token",
}
_SCOPES = {
    "reddit": ("identity", "submit"),
    "linkedin": ("openid", "profile", "w_member_social"),
    "instagram": ("instagram_business_basic", "instagram_business_content_publish"),
    "facebook": ("pages_show_list", "pages_read_engagement", "pages_manage_posts"),
    "tiktok": ("user.info.basic", "video.publish"),
}
_AUTH_URLS = {
    "reddit": "https://www.reddit.com/api/v1/authorize",
    "linkedin": "https://www.linkedin.com/oauth/v2/authorization",
    "instagram": "https://www.instagram.com/oauth/authorize",
    "facebook": f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth",
    "tiktok": "https://www.tiktok.com/v2/auth/authorize/",
}
_TOKEN_URLS = {
    "reddit": "https://www.reddit.com/api/v1/access_token",
    "linkedin": "https://www.linkedin.com/oauth/v2/accessToken",
    "instagram": "https://api.instagram.com/oauth/access_token",
    "facebook": f"https://graph.facebook.com/{GRAPH_VERSION}/oauth/access_token",
    "tiktok": "https://open.tiktokapis.com/v2/oauth/token/",
}
_DOCS = {
    "reddit": "https://github.com/reddit-archive/reddit/wiki/OAuth2",
    "linkedin": "https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow",
    "instagram": "https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login/",
    "facebook": "https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow/",
    "tiktok": "https://developers.tiktok.com/docs/en/login-kit-web",
}

Http = Callable[..., dict[str, Any]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Tokens may never follow a redirect to another host.
        return None


def _request(method: str, url: str, *, data: Mapping[str, Any] | None = None,
             headers: Mapping[str, str] | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "Navin/SocialConnect/1.0", **(headers or {})}
    encoded = urllib.parse.urlencode(data or {}).encode() if data is not None else None
    if encoded is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=encoded, headers=headers, method=method)
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=25) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise MarketingError("Social provider response is too large", status=502)
        payload = json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        # Provider bodies and exception URLs can contain tokens or client secrets.
        raise MarketingError(f"Social authorization refused (HTTP {exc.code}); check the app permissions and callback URL", status=502) from None
    except (OSError, ValueError):
        raise MarketingError("Social authorization could not reach the provider or read its response", status=502) from None
    if not isinstance(payload, dict):
        raise MarketingError("Invalid social provider response", status=502)
    error = payload.get("error")
    if error and (not isinstance(error, dict) or str(error.get("code", "ok")) not in {"ok", "0"}):
        raise MarketingError("Social provider refused authorization; check the granted permissions", status=502)
    return payload


def _provider(value: object) -> str:
    key = str(value or "").strip().lower()
    if key not in SOCIAL_PROVIDERS:
        raise MarketingError("Unsupported social OAuth provider", status=400)
    return key


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def cookie_name(state: str) -> str:
    return "navin_social_" + _digest(state)[:16]


def _valid_redirect(value: str, provider: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    allowed_http = loopback and provider == "reddit"
    if (parsed.scheme != "https" and not (parsed.scheme == "http" and allowed_http)) or not parsed.netloc:
        raise MarketingError("A public HTTPS callback is required for this provider (Reddit also accepts local HTTP)", status=400)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path != CALLBACK_PATH:
        raise MarketingError(f"The callback URL must end with {CALLBACK_PATH}, without credentials, query or fragment", status=400)
    if len(value) > 500:
        raise MarketingError("Callback URL is too long", status=400)
    return value


def _node_field(node: Any, name: str) -> Any:
    if node is None:
        return None
    if isinstance(node, dict):
        return node.get(name)
    return getattr(node, name, None)


def ide_gateway_origin(config: Any | None = None) -> str:
    """HTTP origin of the local IDE gateway (not the Vite :5173 proxy)."""
    from navin.config.loader import load_config

    websocket = _node_field(_node_field(config or load_config(), "channels"), "websocket")
    host = str(_node_field(websocket, "host") or "127.0.0.1").strip()
    if host in {"0.0.0.0", "::", "[::]", ""}:
        host = "127.0.0.1"
    elif host == "localhost":
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = int(_node_field(websocket, "port") or 8765)
    except (TypeError, ValueError):
        port = 8765
    return f"http://{host}:{port}"


def public_install_origin(store: MarketingStore) -> str:
    raw = str((store.load_settings() or {}).get("media_base_url") or "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def suggested_redirect_uri(store: MarketingStore, provider: str, *, config: Any | None = None) -> str:
    """Callback to register: public HTTPS install, else the IDE gateway for Reddit."""
    provider = _provider(provider)
    saved = str(_book(store).get(provider, {}).get("redirect_uri") or "").strip()
    if saved:
        return saved
    public = public_install_origin(store)
    if public:
        return public + CALLBACK_PATH
    if provider == "reddit":
        return ide_gateway_origin(config) + CALLBACK_PATH
    return ""


def _env_value(*names: str) -> str:
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def env_app_credentials(provider: str) -> tuple[str, str]:
    key = provider.upper()
    client_id = _env_value(f"NAVIN_{key}_CLIENT_ID", f"{key}_CLIENT_ID")
    secret = _env_value(f"NAVIN_{key}_CLIENT_SECRET", f"{key}_CLIENT_SECRET")
    if provider == "tiktok":
        client_id = client_id or _env_value("NAVIN_TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_KEY", "TIKTOK_APP_ID")
        secret = secret or _env_value("TIKTOK_SECRET")
    return client_id, secret


def secret_required(provider: str) -> bool:
    return provider != "reddit"


def _book(store: MarketingStore) -> dict[str, Any]:
    raw = _read_json(store.path("oauth.json"), {})
    return raw if isinstance(raw, dict) else {}


def _update(store: MarketingStore, provider: str, fields: dict[str, Any]) -> dict[str, Any]:
    current = _book(store)
    row = {**current.get(provider, {}), **fields}
    current[provider] = row
    _atomic_write(store.path("oauth.json"), current)
    return row


def connection_status(store: MarketingStore, provider: str) -> dict[str, Any]:
    provider = _provider(provider)
    row = _book(store).get(provider, {})
    token_present = bool(store.get_secret(_TOKEN_KEYS[provider]))
    expiry = float(row.get("expires_at") or 0)
    status = str(row.get("status") or "not_connected")
    env_id, env_secret = env_app_credentials(provider)
    client_id = str(row.get("client_id") or env_id or "")
    redirect_uri = str(row.get("redirect_uri") or "") or suggested_redirect_uri(store, provider)
    secret_set = bool(store.get_secret(f"{provider}_client_secret") or env_secret)
    if status == "connected" and not token_present:
        status = "not_connected"
    if status == "connected" and expiry and expiry <= time.time():
        status = "refresh_required" if row.get("refreshable") else "expired"
    return {
        "provider": provider, "status": status,
        "client_id": client_id,
        "client_secret_set": secret_set,
        "redirect_uri": redirect_uri,
        "account": str(row.get("account") or ""), "account_id": str(row.get("account_id") or ""),
        "accounts": [{"id": str(item.get("id") or ""), "name": str(item.get("name") or "")} for item in row.get("accounts", [])],
        "expires_at": expiry, "refreshable": bool(row.get("refreshable")),
        "requested_scopes": list(_SCOPES[provider]), "granted_scopes": list(row.get("granted_scopes") or []),
        "scope_status": str(row.get("scope_status") or "not_checked"),
        "last_error": str(row.get("last_error") or ""), "docs_url": _DOCS[provider],
        "suggested_redirect_uri": suggested_redirect_uri(store, provider),
    }


def connections_status(store: MarketingStore) -> list[dict[str, Any]]:
    return [connection_status(store, key) for key in SOCIAL_PROVIDERS]


def configure_connection(store: MarketingStore, provider: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    provider = _provider(provider)
    client_id = str(fields.get("client_id") or "").strip()
    redirect_uri = _valid_redirect(str(fields.get("redirect_uri") or "").strip(), provider)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,256}", client_id):
        raise MarketingError("A valid application client ID is required", status=400)
    with InterProcessLock(store.path(".oauth.lock"), timeout=5):
        previous = _book(store).get(provider, {})
        if "client_secret" in fields:
            store.save_secret(f"{provider}_client_secret", str(fields.get("client_secret") or ""))
        # Rebinding an app invalidates its cached authorization and pending codes.
        rebound = previous.get("client_id") not in {None, "", client_id} or previous.get("redirect_uri") not in {None, "", redirect_uri}
        if rebound:
            _forget(store, provider)
        _update(store, provider, {"client_id": client_id, "redirect_uri": redirect_uri, "last_error": ""})
    return connection_status(store, provider)


def start_connection(
    store: MarketingStore,
    provider: str,
    *,
    session_key: str = "",
    return_to: str = "",
) -> dict[str, Any]:
    provider = _provider(provider)
    with InterProcessLock(store.path(".oauth.lock"), timeout=5):
        config = dict(_book(store).get(provider, {}))
        env_id, env_secret = env_app_credentials(provider)
        client_id = str(config.get("client_id") or env_id or "").strip()
        if env_secret and not store.get_secret(f"{provider}_client_secret"):
            store.save_secret(f"{provider}_client_secret", env_secret)
        redirect_uri = _valid_redirect(
            str(config.get("redirect_uri") or "") or suggested_redirect_uri(store, provider),
            provider,
        )
        if client_id != config.get("client_id") or redirect_uri != config.get("redirect_uri"):
            _update(store, provider, {"client_id": client_id, "redirect_uri": redirect_uri, "last_error": ""})
            config["client_id"] = client_id
            config["redirect_uri"] = redirect_uri
        if not client_id or (secret_required(provider) and not store.get_secret(f"{provider}_client_secret")):
            raise MarketingError(
                "Sign in with the provider. Create the app once, or set its CLIENT_ID in the environment. "
                "Your account id is filled automatically after Connect.",
                status=400,
            )
        state, cookie = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = time.time()
        states = _read_json(store.path("oauth-pending.json"), {})
        states = {key: value for key, value in states.items() if isinstance(value, dict) and float(value.get("expires_at") or 0) > now}
        if len(states) >= 12:
            raise MarketingError("Too many pending connections; finish one or wait ten minutes", status=429)
        landing = return_to if return_to in {"channels", "marketing"} else ""
        states[_digest(state)] = {
            "provider": provider, "cookie_hash": _digest(cookie), "expires_at": now + STATE_TTL,
            "client_id": client_id, "redirect_uri": redirect_uri, "session_key": session_key,
            "return_to": landing,
        }
        _atomic_write(store.path("oauth-pending.json"), states)
    params = {"client_key" if provider == "tiktok" else "client_id": client_id, "redirect_uri": redirect_uri,
              "response_type": "code", "state": state,
              "scope": ("," if provider in {"tiktok", "instagram", "facebook"} else " ").join(_SCOPES[provider])}
    if provider == "reddit":
        params["duration"] = "permanent"
    if provider == "instagram":
        params.update({"enable_fb_login": "0", "force_authentication": "1"})
    cookie_header = f"{cookie_name(state)}={cookie}; Path={CALLBACK_PATH}; Max-Age={STATE_TTL}; HttpOnly; SameSite=Lax"
    if redirect_uri.startswith("https:"):
        cookie_header += "; Secure"
    return {"provider": provider, "authorization_url": _AUTH_URLS[provider] + "?" + urllib.parse.urlencode(params),
            "expires_at": now + STATE_TTL, "_set_cookie": cookie_header}


def _token_form(store: MarketingStore, provider: str, config: Mapping[str, Any], **fields: str) -> tuple[dict[str, Any], dict[str, str]]:
    secret = store.get_secret(f"{provider}_client_secret")
    form: dict[str, Any] = dict(fields)
    headers: dict[str, str] = {}
    if provider == "reddit":
        pair = base64.b64encode(f"{config['client_id']}:{secret}".encode()).decode()
        headers["Authorization"] = "Basic " + pair
        user_agent = store.load_settings()["publish"]["reddit"].get("user_agent")
        if user_agent:
            headers["User-Agent"] = str(user_agent)
    else:
        form["client_key" if provider == "tiktok" else "client_id"] = config["client_id"]
        form["client_secret"] = secret
    return form, headers


def _get(http: Http, url: str, token: str) -> dict[str, Any]:
    return http("GET", url, headers={"Authorization": "Bearer " + token})


def _identity(store: MarketingStore, provider: str, token: str, response: dict[str, Any], http: Http) -> dict[str, Any]:
    if provider == "reddit":
        row = _get(http, "https://oauth.reddit.com/api/v1/me", token)
        return {"id": row.get("id"), "name": row.get("name")}
    if provider == "linkedin":
        row = _get(http, "https://api.linkedin.com/v2/userinfo", token)
        return {"id": row.get("sub"), "name": row.get("name")}
    if provider == "instagram":
        row = _get(http, f"https://graph.instagram.com/{GRAPH_VERSION}/me?fields=user_id,username", token)
        return {"id": row.get("user_id") or row.get("id") or response.get("user_id"), "name": row.get("username")}
    if provider == "tiktok":
        row = _get(http, "https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name", token)
        user = (row.get("data") or {}).get("user") or {}
        return {"id": user.get("open_id") or response.get("open_id"), "name": user.get("display_name")}
    # A page must be chosen explicitly when the user has more than one.
    rows = _get(http, f"https://graph.facebook.com/{GRAPH_VERSION}/me/accounts?fields=id,name,tasks&limit=100", token)
    accounts = [{"id": str(row.get("id") or ""), "name": str(row.get("name") or "")} for row in rows.get("data", []) if row.get("id")]
    if (rows.get("paging") or {}).get("next"):
        raise MarketingError("More than 100 Facebook Pages: narrow the pages authorized for this application", status=400)
    if not accounts:
        raise MarketingError("No Facebook Page was authorized; grant access to a Page you manage", status=400)
    return {"accounts": accounts}


def _accept_token(store: MarketingStore, provider: str, config: dict[str, Any], response: dict[str, Any], http: Http) -> None:
    if isinstance(response.get("data"), list) and response["data"]:
        response = response["data"][0]
    token = str(response.get("access_token") or "")
    if not token or len(token) > 20000:
        raise MarketingError("Provider did not return a usable access token", status=502)
    reported = response.get("scope")
    granted = re.split(r"[,\s]+", str(reported or "").strip()) if reported else []
    if reported and set(_SCOPES[provider]) - set(granted):
        raise MarketingError("Required permissions were not granted; authorize the publishing scopes for this app", status=400)
    if provider == "instagram":
        params = {"grant_type": "ig_exchange_token", "client_secret": store.get_secret("instagram_client_secret"), "access_token": token}
        long_lived = http("GET", "https://graph.instagram.com/access_token?" + urllib.parse.urlencode(params))
        token = str(long_lived.get("access_token") or "")
        if not token:
            raise MarketingError("Instagram did not return a long-lived token", status=502)
        response = {**response, **long_lived}
    identity = _identity(store, provider, token, response, http)
    if provider != "facebook" and (not identity.get("id") or not identity.get("name")):
        raise MarketingError("Provider did not confirm the connected account identity", status=502)
    now = time.time()
    expires = float(response.get("expires_in") or (3600 if provider == "reddit" else 0))
    refresh = str(response.get("refresh_token") or "")
    token_fields = {_TOKEN_KEYS[provider] if provider != "facebook" else "facebook_user_token": token}
    if provider != "facebook":
        token_fields[f"{provider}_refresh_token"] = refresh
    else:
        token_fields["facebook_page_token"] = ""
    store.save_secrets(token_fields)
    fields = {"status": "select_account" if provider == "facebook" else "connected", "last_error": "",
              "account_id": str(identity.get("id") or ""), "account": str(identity.get("name") or ""),
              "connected_at": now, "expires_at": now + expires if expires else 0,
              "refreshable": bool(refresh) or provider == "instagram", "granted_scopes": granted,
              "scope_status": "confirmed" if reported else "provider_not_reported", "accounts": identity.get("accounts", [])}
    _update(store, provider, fields)
    publish = store.load_settings()["publish"][provider]
    patch: dict[str, Any] = {"enabled": False, "account": identity.get("name") or "", "tested_at": now, "last_error": ""}
    if provider == "linkedin":
        patch["author"] = "urn:li:person:" + str(identity["id"])
    if provider == "instagram":
        patch.update({"instagram_user_id": str(identity["id"]), "auth_mode": "instagram"})
    if provider == "reddit" and not publish.get("user_agent"):
        patch["user_agent"] = f"web:Navin:1.0 (by /u/{identity['name']})"
    store.save_settings({"publish": {provider: patch}})
    if provider == "facebook":
        configured = str(publish.get("page_id") or "")
        choices = identity["accounts"]
        selected = next((row["id"] for row in choices if row["id"] == configured), None)
        if selected or len(choices) == 1:
            _select_facebook(store, selected or choices[0]["id"], http)


def finish_connection(store: MarketingStore, query: Mapping[str, str], cookie_header: str, *, http: Http | None = None) -> dict[str, Any]:
    state = str(query.get("state") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,100}", state):
        raise MarketingError("Invalid or missing authorization state", status=400)
    cookies = SimpleCookie()
    try:
        cookies.load(cookie_header)
    except Exception:
        raise MarketingError("Invalid authorization cookie", status=400) from None
    cookie = cookies.get(cookie_name(state))
    with InterProcessLock(store.path(".oauth.lock"), timeout=5):
        states = _read_json(store.path("oauth-pending.json"), {})
        pending = states.get(_digest(state), {})
        landing = str(pending.get("return_to") or "")
        if not pending or float(pending.get("expires_at") or 0) <= time.time():
            raise MarketingError("Authorization expired or already used; reconnect from Settings > Channels", status=400)
        if cookie is None or not hmac.compare_digest(_digest(cookie.value), str(pending.get("cookie_hash") or "")):
            raise MarketingError("Authorization must finish in the browser that started it", status=403)
        provider = _provider(pending["provider"])
        config = _book(store).get(provider, {})
        states.pop(_digest(state))
        _atomic_write(store.path("oauth-pending.json"), states)
        if any(pending.get(key) != config.get(key) for key in ("client_id", "redirect_uri")):
            raise MarketingError("Application settings changed; start authorization again", status=400)
        if query.get("error"):
            _update(store, provider, {"last_error": "Authorization was declined or interrupted"})
            raise MarketingError("Authorization was declined or interrupted", status=400)
        code = str(query.get("code") or "")
        if not code or len(code) > 20000:
            raise MarketingError("Provider did not return an authorization code", status=400)
        form, headers = _token_form(store, provider, config, grant_type="authorization_code", code=code, redirect_uri=config["redirect_uri"])
        try:
            if provider == "facebook":
                response = (http or _request)("GET", _TOKEN_URLS[provider] + "?" + urllib.parse.urlencode(form), headers=headers)
            else:
                response = (http or _request)("POST", _TOKEN_URLS[provider], data=form, headers=headers)
            _accept_token(store, provider, config, response, http or _request)
        except MarketingError as exc:
            _update(store, provider, {"last_error": exc.message})
            raise
    return {
        "connection": connection_status(store, provider),
        "session_key": pending.get("session_key", ""),
        "cookie_name": cookie_name(state),
        "return_to": landing,
    }


def _select_facebook(store: MarketingStore, account_id: str, http: Http) -> None:
    config = _book(store).get("facebook", {})
    if account_id not in {row.get("id") for row in config.get("accounts", [])} or not re.fullmatch(r"[0-9]+", account_id):
        raise MarketingError("Choose one of the Facebook Pages returned by authorization", status=400)
    token = store.get_secret("facebook_user_token")
    row = _get(http, f"https://graph.facebook.com/{GRAPH_VERSION}/{account_id}?fields=id,name,access_token", token)
    if str(row.get("id")) != account_id or not row.get("access_token"):
        raise MarketingError("Facebook did not return a token for the selected Page", status=502)
    store.save_secret("facebook_page_token", str(row["access_token"]))
    store.save_settings({"publish": {"facebook": {"page_id": account_id, "account": str(row.get("name") or account_id), "tested_at": time.time(), "last_error": ""}}})
    _update(store, "facebook", {"status": "connected", "account_id": account_id, "account": str(row.get("name") or account_id), "last_error": ""})


def select_account(store: MarketingStore, provider: str, account_id: str, *, http: Http | None = None) -> dict[str, Any]:
    if _provider(provider) != "facebook":
        raise MarketingError("Account selection is available for Facebook Pages", status=400)
    with InterProcessLock(store.path(".oauth.lock"), timeout=5):
        _select_facebook(store, str(account_id), http or _request)
    return connection_status(store, provider)


def ensure_access_token(store: MarketingStore, provider: str, *, http: Http | None = None) -> str:
    """Refresh a known OAuth token when needed. Manual token setup stays valid."""
    if provider not in SOCIAL_PROVIDERS:
        return ""
    with InterProcessLock(store.path(".oauth.lock"), timeout=5):
        config = _book(store).get(provider, {})
        token = store.get_secret(_TOKEN_KEYS[provider])
        expires = float(config.get("expires_at") or 0)
        if not token or not expires or expires > time.time() + 300:
            return token
        client = http or _request
        if provider == "instagram" and expires > time.time() and float(config.get("connected_at") or 0) < time.time() - 86400:
            response = client("GET", "https://graph.instagram.com/refresh_access_token?" + urllib.parse.urlencode({"grant_type": "ig_refresh_token", "access_token": token}))
        elif provider in {"reddit", "linkedin", "tiktok"} and store.get_secret(f"{provider}_refresh_token"):
            form, headers = _token_form(store, provider, config, grant_type="refresh_token", refresh_token=store.get_secret(f"{provider}_refresh_token"))
            response = client("POST", _TOKEN_URLS[provider], data=form, headers=headers)
        else:
            raise MarketingError(f"Reconnect {provider}: its authorization expires soon or has expired", status=401)
        new_token = str(response.get("access_token") or "")
        if not new_token or not response.get("expires_in"):
            raise MarketingError(f"{provider} did not return a renewed access token", status=502)
        values = {_TOKEN_KEYS[provider]: new_token}
        if response.get("refresh_token"):
            values[f"{provider}_refresh_token"] = str(response["refresh_token"])
        store.save_secrets(values)
        _update(store, provider, {"status": "connected", "expires_at": time.time() + float(response["expires_in"]), "last_error": ""})
        return new_token


def _forget(store: MarketingStore, provider: str) -> None:
    values = {_TOKEN_KEYS[provider]: ""}
    values["facebook_user_token" if provider == "facebook" else f"{provider}_refresh_token"] = ""
    store.save_secrets(values)
    store.save_settings({"publish": {provider: {"enabled": False, "account": "", "tested_at": 0, "last_error": ""}}})
    _update(store, provider, {"status": "not_connected", "expires_at": 0, "refreshable": False,
                              "account": "", "account_id": "", "accounts": [], "granted_scopes": [], "last_error": ""})
    states = _read_json(store.path("oauth-pending.json"), {})
    _atomic_write(store.path("oauth-pending.json"), {key: row for key, row in states.items() if row.get("provider") != provider})


def disconnect(store: MarketingStore, provider: str) -> dict[str, Any]:
    provider = _provider(provider)
    with InterProcessLock(store.path(".oauth.lock"), timeout=5):
        _forget(store, provider)
    return {**connection_status(store, provider), "remote_revoked": False,
            "detail": "Tokens removed from Navin and publication disabled. You can also revoke the app in your social account settings."}
