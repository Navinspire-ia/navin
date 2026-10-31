# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""OAuth sign-in for connectors.

One OAuth app per provider family (all Google apps share one client, all
Microsoft apps another, Slack and Slack Bot a third). :func:`start` builds
the authorization URL with a one-use ``state`` (and a PKCE verifier where
the app requires it); the provider sends the browser back to
``/api/connectors/oauth/callback`` where :func:`finish` exchanges the code,
verifies the account and stores the tokens. :func:`refresh` renews an
expired access token.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from navin.connectors import catalog, providers, runtime
from navin.connectors.catalog import ConnectorError
from navin.connectors.store import ConnectorStore

CALLBACK_PATH = "/api/connectors/oauth/callback"
STATE_TTL = 600
_FAMILY_LABELS = {"google": "Google", "microsoft": "Microsoft", "slack": "Slack"}
_AUTH_NAMES = {"clientId": "client_id", "redirectUri": "redirect_uri", "responseType": "response_type",
               "state": "state", "scope": "scope"}
_TOKEN_NAMES: dict[str, Any] = {"grantType": "grant_type", "code": "code", "redirectUri": "redirect_uri",
                                "clientId": "client_id", "clientSecret": "client_secret",
                                "refreshToken": "refresh_token", "state": False}
_METADATA_KEYS = ("instance_url", "bot_user_id", "workspace_name", "workspace_id", "team_id", "user_id", "shop")


def family_of(service: str) -> str:
    item = catalog.method(service, "oauth2")
    return str(item.get("family") or catalog.provider(service)["service"])


def family_label(family: str) -> str:
    if family in _FAMILY_LABELS:
        return _FAMILY_LABELS[family]
    return catalog.provider(family)["name"]


def family_services(family: str) -> list[str]:
    return [
        row["service"] for row in catalog.providers()
        for item in row["methods"] if item["type"] == "oauth2" and item.get("family") == family
    ]


def suggested_redirect_uri() -> str:
    from navin.marketing.oauth import ide_gateway_origin

    return ide_gateway_origin() + CALLBACK_PATH


def _saved_redirect(value: Any) -> str:
    from navin.marketing.oauth import loopback_callback

    return loopback_callback(str(value or ""), CALLBACK_PATH)


def valid_redirect(value: Any) -> str:
    text = str(value or "").strip()
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise ConnectorError("The redirect URI is not a valid URL.") from None
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    if not host or parts.scheme not in {"https", "http"} or (parts.scheme == "http" and not loopback):
        raise ConnectorError("The redirect URI must use https://, or http://localhost on this machine.")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path != CALLBACK_PATH:
        raise ConnectorError(f"The redirect URI must end with {CALLBACK_PATH}, without query or fragment.")
    if len(text) > 500:
        raise ConnectorError("The redirect URI is too long.")
    return text


def _env_client(family: str) -> tuple[str, str]:
    key = re.sub(r"[^A-Z0-9]", "_", family.upper())
    return (
        os.environ.get(f"NAVIN_{key}_CLIENT_ID", "").strip(),
        os.environ.get(f"NAVIN_{key}_CLIENT_SECRET", "").strip(),
    )


def client(store: ConnectorStore, family: str) -> tuple[str, str, str]:
    """``(client_id, client_secret, redirect_uri)`` of a family's OAuth app."""
    app = store.app(family)
    env_id, env_secret = _env_client(family)
    client_id = str(app.get("client_id") or env_id)
    secret = store.app_secret(family) or env_secret
    redirect = _saved_redirect(app.get("redirect_uri")) or suggested_redirect_uri()
    return client_id, secret, redirect


def app_status(store: ConnectorStore, service: str) -> dict[str, Any]:
    item = catalog.method(service, "oauth2")
    family = family_of(service)
    client_id, secret, redirect = client(store, family)
    public_client = item.get("tokenEndpointAuthMethod") == "none"
    return {
        "family": family,
        "label": family_label(family),
        "client_id": client_id,
        "has_secret": bool(secret),
        "configured": bool(client_id and (secret or public_client)),
        "redirect_uri": redirect,
        "suggested_redirect_uri": suggested_redirect_uri(),
        "app_url": item.get("appUrl") or "",
        "setup": item.get("clientSetup") or {},
        "services": family_services(family),
        "scopes": list(item.get("scopes") or []),
    }


def save_app(store: ConnectorStore, service: str, *, client_id: Any, client_secret: Any = "", redirect_uri: Any = "") -> dict[str, Any]:
    family = family_of(service)
    cid = str(client_id or "").strip()
    if not cid or len(cid) > 500 or any(char.isspace() for char in cid):
        raise ConnectorError("Paste the client ID of the OAuth app.")
    secret = str(client_secret or "").strip()
    if len(secret) > 2000 or any(char in secret for char in "\r\n"):
        raise ConnectorError("The client secret is not valid.")
    if not secret and not store.app_secret(family) and catalog.method(service, "oauth2").get("tokenEndpointAuthMethod") != "none":
        raise ConnectorError("Paste the client secret of the OAuth app.")
    redirect = valid_redirect(redirect_uri or suggested_redirect_uri())
    store.save_app(family, client_id=cid, client_secret=secret, redirect_uri=redirect)
    return app_status(store, service)


def _digest(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _fields(service: str, item: dict[str, Any], raw: Any) -> tuple[dict[str, str], dict[str, str]]:
    public: dict[str, str] = {}
    secret: dict[str, str] = {}
    values = raw if isinstance(raw, dict) else {}
    for spec in item.get("fields") or []:
        text = str(values.get(spec["key"]) or "").strip() or str(spec.get("defaultValue") or "")
        if spec.get("required") and not text:
            raise ConnectorError(f"{spec['label']} is required.")
        if len(text) > 4000 or "\n" in text:
            raise ConnectorError(f"{spec['label']} is not valid.")
        if text:
            (secret if spec.get("secret") else public)[spec["key"]] = text
    return providers.prepare(service, public), secret


def endpoint(template: str, values: dict[str, Any], service: str) -> str:
    if not providers.complete(template, values):
        missing = ", ".join(providers.placeholders(template))
        raise ConnectorError(f"Fill in the connection fields first ({missing}).")
    url = providers.render(template, values)
    if service == "salesforce" and values.get("loginUrl"):
        url = url.replace("https://login.salesforce.com", str(values["loginUrl"]), 1)
    if not url.startswith("https://"):
        raise ConnectorError("The OAuth endpoint must use HTTPS.")
    return url


def start(store: ConnectorStore, service: str, fields: Any = None, *, session_key: str = "") -> dict[str, Any]:
    """Authorization URL for the user's browser, valid ten minutes, once."""
    row = catalog.provider(service)
    service = row["service"]
    item = catalog.method(service, "oauth2")
    family = family_of(service)
    client_id, client_secret, redirect_uri = client(store, family)
    if not client_id or (item.get("tokenEndpointAuthMethod") != "none" and not client_secret):
        raise ConnectorError(f"Add the {family_label(family)} OAuth app first: client ID and client secret.", 409)
    redirect_uri = valid_redirect(redirect_uri)
    public, secret = _fields(service, item, fields)
    names = {**_AUTH_NAMES, **(item.get("authorizationRequestFields") or {})}
    state = secrets.token_urlsafe(32)
    params: dict[str, str] = {}

    def put(key: str, value: str) -> None:
        name = names.get(key)
        if name:
            params[str(name)] = value

    put("clientId", client_id)
    put("redirectUri", redirect_uri)
    put("responseType", "code")
    put("state", state)
    scopes = [str(s) for s in item.get("scopes") or []]
    if scopes:
        put("scope", str(item.get("scopeSeparator") or " ").join(scopes))
    params.update({str(k): str(v) for k, v in (item.get("authorizationParams") or {}).items()})
    verifier = ""
    if (item.get("pkce") or {}).get("method") == "S256":
        verifier, challenge = _pkce()
        params.update({"code_challenge": challenge, "code_challenge_method": "S256"})
    url = endpoint(item["authorizationUrl"], public, service)
    now = time.time()
    store.add_pending(_digest(state), {
        "service": service, "redirect_uri": redirect_uri, "verifier": verifier, "fields": public,
        "expires_at": now + STATE_TTL, "session_key": str(session_key or "")[:200],
    })
    if secret:
        store.put_secrets(f"pending:{service}", {"values": secret})
    else:
        store.delete_secrets(f"pending:{service}")
    joiner = "&" if "?" in url else "?"
    return {
        "service": service,
        "authorization_url": url + joiner + urlencode(params),
        "expires_at": int(now + STATE_TTL),
        "redirect_uri": redirect_uri,
    }


def _token_call(
    label: str, item: dict[str, Any], url: str, body: dict[str, Any], client_id: str, client_secret: str, names: dict[str, Any]
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    method = item.get("tokenEndpointAuthMethod") or "client_secret_post"
    id_name = names.get("clientId", "client_id")
    secret_name = names.get("clientSecret", "client_secret")
    if method == "client_secret_basic":
        headers["Authorization"] = "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")
    if id_name:
        body[str(id_name)] = client_id
    if method == "client_secret_post" and secret_name:
        body[str(secret_name)] = client_secret
    as_json = item.get("tokenRequestFormat") == "json"
    hidden = (client_secret, str(body.get("code") or ""), str(body.get("refresh_token") or ""))
    try:
        with runtime.http_client() as http:
            if as_json:
                response = http.post(url, json=body, headers=headers)
            else:
                response = http.post(url, data=body, headers=headers)
    except httpx.TimeoutException:
        raise ConnectorError(f"{label} did not answer the token request in time; try again.", 504) from None
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise ConnectorError(f"{label} could not be reached for the token ({type(exc).__name__}).", 502) from None
    data = runtime.json_or_none(response)
    status = response.status_code
    envelope = item.get("tokenResponseEnvelope")
    if envelope and isinstance(data, dict):
        code_field = envelope.get("codeField")
        if code_field and data.get(code_field) != envelope.get("successCode", 0):
            message = data.get(envelope.get("messageField") or "message") or data.get(code_field)
            raise ConnectorError(f"{label} refused the token request: {runtime.mask(str(message), hidden)}.", upstream=status)
        data = data.get(envelope.get("dataField") or "data")
    if status >= 400 or not isinstance(data, dict):
        detail = runtime.mask(runtime.provider_message(data), hidden) or "no details"
        raise ConnectorError(f"{label} refused the token request (HTTP {status}): {detail}.", upstream=status)
    if data.get("ok") is False:
        raise ConnectorError(
            f"{label} refused the token request: {data.get('error') or 'unknown error'}.", upstream=status
        )
    if not data.get("access_token") and isinstance(data.get("authed_user"), dict) and data["authed_user"].get("access_token"):
        data = {**data, **data["authed_user"]}
    if not data.get("access_token"):
        raise ConnectorError(f"{label} returned no access token.", upstream=status)
    return data


def _expires_at(tokens: dict[str, Any]) -> int:
    try:
        seconds = int(float(tokens.get("expires_in") or 0))
    except (TypeError, ValueError):
        return 0
    return int(time.time()) + seconds if seconds > 0 else 0


def _scopes(tokens: dict[str, Any]) -> list[str]:
    scope = tokens.get("scope")
    if isinstance(scope, list):
        return [str(s) for s in scope][:100]
    return [s for s in re.split(r"[,\s]+", str(scope or "").strip()) if s][:100]


def finish(store: ConnectorStore, query: dict[str, Any]) -> dict[str, Any]:
    """Exchange the callback code, verify the account, store the connection."""
    state = str(query.get("state") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", state):
        raise ConnectorError("The sign-in answer has no valid state. Start again from Navin.")
    entry = store.take_pending(_digest(state))
    if not entry:
        raise ConnectorError("This sign-in link expired or was already used. Start again from Navin.")
    service = str(entry.get("service") or "")
    row = catalog.provider(service)
    item = catalog.method(service, "oauth2")
    if query.get("error"):
        store.delete_secrets(f"pending:{service}")
        detail = str(query.get("error_description") or query.get("error"))[:300]
        raise ConnectorError(f"{row['name']} refused the sign-in: {detail}.")
    code = str(query.get("code") or query.get("auth_code") or "")
    if not code or len(code) > 4000:
        raise ConnectorError(f"{row['name']} did not return an authorization code.")
    client_id, client_secret, _ = client(store, family_of(service))
    public = {str(k): str(v) for k, v in (entry.get("fields") or {}).items()}
    pending = {str(k): str(v) for k, v in (store.secrets(f"pending:{service}").get("values") or {}).items()}
    fields = dict(item.get("tokenRequestFields") or {})
    names = {**_TOKEN_NAMES, **fields, **(fields.get("authorizationCode") or {})}
    body: dict[str, Any] = {}
    if names.get("grantType"):
        body[str(names["grantType"])] = "authorization_code"
    body[str(names.get("code") or "code")] = code
    if names.get("redirectUri"):
        body[str(names["redirectUri"])] = entry.get("redirect_uri") or ""
    if entry.get("verifier"):
        body["code_verifier"] = entry["verifier"]
    if names.get("state"):
        body[str(names["state"])] = state
    for param in item.get("tokenRequestCallbackParameters") or []:
        if query.get(param):
            body[str(param)] = str(query[param])
    tokens = _token_call(row["name"], item, endpoint(item["tokenUrl"], public, service), body, client_id, client_secret, names)
    access = str(tokens["access_token"])
    metadata = {key: tokens[key] for key in _METADATA_KEYS if isinstance(tokens.get(key), (str, int))}
    if isinstance(tokens.get("team"), dict) and tokens["team"].get("name"):
        metadata["team"] = str(tokens["team"]["name"])
    if service == "salesforce":
        instance = providers.https_url(tokens.get("instance_url"), "Salesforce instance URL")
        host = urlsplit(instance).hostname or ""
        if not (host.endswith(".salesforce.com") or host.endswith(".force.com")):
            raise ConnectorError("Salesforce returned an unexpected instance URL.")
        metadata["instance_url"] = instance
    result = runtime.verify_credential(service, "oauth2", {**public, **pending}, access_token=access, metadata=metadata)
    profile = dict(result["profile"] or {})
    if not profile.get("name") and metadata.get("team"):
        profile["name"] = str(metadata["team"])
    prepared = result["values"]
    store.save_connection(
        service,
        method="oauth2",
        fields={key: str(prepared.get(key, value)) for key, value in public.items()},
        secret_values=pending,
        profile=profile,
        metadata=metadata,
        scopes=_scopes(tokens),
        access_token=access,
        refresh_token=str(tokens.get("refresh_token") or ""),
        expires_at=_expires_at(tokens),
        token_type=str(tokens.get("token_type") or "Bearer"),
    )
    store.delete_secrets(f"pending:{service}")
    return {"service": service, "name": row["name"], "account": str(profile.get("name") or ""),
            "session_key": str(entry.get("session_key") or "")}


def refresh(store: ConnectorStore, service: str) -> str:
    """A fresh access token from the stored refresh token, or ``""``."""
    row = catalog.provider(service)
    service = row["service"]
    item = catalog.method(service, "oauth2")
    bag = store.secrets(f"conn:{service}")
    token = str(bag.get("refresh_token") or "")
    if not token:
        return ""
    client_id, client_secret, _ = client(store, family_of(service))
    if not client_id:
        return ""
    connection = store.connection(service) or {}
    fields = dict(item.get("tokenRequestFields") or {})
    names = {**_TOKEN_NAMES, **fields, **(fields.get("refresh") or {})}
    body: dict[str, Any] = {}
    if names.get("grantType"):
        body[str(names["grantType"])] = "refresh_token"
    body[str(names.get("refreshToken") or "refresh_token")] = token
    try:
        url = endpoint(item.get("refreshTokenUrl") or item["tokenUrl"], dict(connection.get("fields") or {}), service)
        tokens = _token_call(row["name"], item, url, body, client_id, client_secret, names)
    except ConnectorError as exc:
        store.mark_error(service, f"Token refresh failed: {exc.message}")
        return ""
    access = str(tokens["access_token"])
    kept = store.update_tokens(
        service,
        access_token=access,
        refresh_token=str(tokens.get("refresh_token") or token),
        expires_at=_expires_at(tokens),
    )
    # Disconnected while the refresh ran: no token for a connection that is gone.
    return access if kept else ""
