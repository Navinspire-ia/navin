# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Verify a credential and call a connected app's API with it.

The model never sees a credential. The agent tool hands a path and a body
to :func:`request`; this module loads the stored values, injects them where
the app expects them and only toward the app's own hosts, refreshes an
expired OAuth token once, and returns the parsed response with every stored
secret masked.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from navin.connectors import catalog, providers
from navin.connectors.catalog import ConnectorError
from navin.connectors.providers import (
    GOOGLE_TOKENINFO,
    Spec,
    Verify,
    complete,
    host_allowed,
    render,
    spec_for,
)
from navin.connectors.store import ConnectorStore

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_RESPONSE_BYTES = 8_000_000
# A response written to a file (save_to) streams to disk, so it may be larger.
MAX_DOWNLOAD_BYTES = 200_000_000
MAX_UPLOAD_BYTES = 50_000_000
BODY_FORMATS = ("json", "form", "multipart", "raw")
MAX_ERROR_CHARS = 400
USER_AGENT = "Navin-Connectors/1.0"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
DOCKER_TOKEN_URL = "https://hub.docker.com/v2/auth/token"
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"})
_IDENTITY_SCOPES = frozenset({"openid", "email", "profile"})
_BLOCKED_HEADERS = frozenset({
    "authorization", "cookie", "host", "content-length", "transfer-encoding", "connection",
    "proxy-authorization", "x-api-key", "api-key", "x-goog-api-key", "xi-api-key", "circle-token",
    "dd-api-key", "dd-application-key", "x-shopify-access-token", "access-token", "x-auth-token",
    "x-cb-user-key", "x-figma-token",
})

# Tests swap the transport through this seam (httpx.MockTransport).
transport: httpx.BaseTransport | None = None

_cache: dict[str, tuple[str, float]] = {}
_cache_lock = threading.Lock()


def http_client() -> httpx.Client:
    # Credentials must never follow a redirect to another host.
    return httpx.Client(timeout=TIMEOUT, follow_redirects=False, transport=transport, headers={"User-Agent": USER_AGENT})


@dataclass
class Credential:
    service: str
    kind: str
    values: dict[str, str] = field(default_factory=dict)
    access_token: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    hidden: tuple[str, ...] = ()

    def template_values(self) -> dict[str, Any]:
        out: dict[str, Any] = {k: v for k, v in self.metadata.items() if isinstance(v, (str, int))}
        out.update(self.values)
        if self.access_token:
            out["accessToken"] = self.access_token
        return out


# -- small helpers -------------------------------------------------------------------


def json_or_none(response: httpx.Response) -> Any:
    """The parsed JSON body, or None when the body is not JSON.

    Not a failure by itself: every caller checks the HTTP status and the shape
    of the answer and raises a ConnectorError with that status when it is
    unusable.
    """
    try:
        return response.json()
    except ValueError:
        return None


def provider_message(data: Any) -> str:
    """The provider's own error sentence, from the usual JSON shapes."""
    if isinstance(data, str):
        return " ".join(data.split())[:MAX_ERROR_CHARS]
    if isinstance(data, list):
        return provider_message(data[0]) if data else ""
    if not isinstance(data, dict):
        return ""
    for key in ("error_description", "message", "error_message", "detail", "details", "title", "msg", "error",
                "errors", "reason", "description", "status_message"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:MAX_ERROR_CHARS]
        if isinstance(value, (dict, list)) and value:
            text = provider_message(value)
            if text:
                return text
    return ""


def mask(text: str, hidden: tuple[str, ...]) -> str:
    for secret in sorted({s for s in hidden if isinstance(s, str) and len(s) >= 6}, key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return text


def _mask_data(data: Any, hidden: tuple[str, ...]) -> Any:
    if isinstance(data, str):
        return mask(data, hidden)
    if isinstance(data, list):
        return [_mask_data(item, hidden) for item in data]
    if isinstance(data, dict):
        return {key: _mask_data(value, hidden) for key, value in data.items()}
    return data


def dig(data: Any, path: str) -> Any:
    """``a.b.0.c`` in nested JSON; ``x|y`` returns the first non-empty match."""
    for option in (path or "").split("|"):
        if not option:
            continue
        current = data
        found = True
        for part in option.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                found = False
                break
        if found and current not in (None, "", [], {}):
            return current
    return None


def _text(response: httpx.Response, body: bytes) -> str | None:
    """The body as text, or None when it is not text in its declared charset."""
    try:
        return body.decode(response.encoding or "utf-8")
    except (UnicodeDecodeError, LookupError):
        return None


def _decode(response: httpx.Response, body: bytes) -> Any:
    """Parsed JSON, text, or None for a binary body (reported as a byte count)."""
    if not body:
        return None
    ctype = response.headers.get("content-type", "").lower()
    textual = not ctype or ctype.startswith("text/") or any(word in ctype for word in ("json", "xml", "html"))
    if "json" in ctype or body[:1] in (b"{", b"["):
        try:
            return json.loads(body)
        except ValueError:
            # Not JSON after all (an HTML error page, a cut body): keep its words if it is text.
            return _text(response, body) if textual else None
    return _text(response, body) if textual else None


def _cached(key: str) -> str:
    with _cache_lock:
        token, expires = _cache.get(key, ("", 0.0))
    return token if token and expires - 60 > time.time() else ""


def _remember(key: str, token: str, expires_at: float) -> None:
    with _cache_lock:
        _cache[key] = (token, expires_at)


def clear_token_cache() -> None:
    with _cache_lock:
        _cache.clear()


# -- token exchanges -------------------------------------------------------------------


def _rs256_jwt(header: dict[str, Any], claims: dict[str, Any], pem: str) -> str:
    from cryptography.exceptions import UnsupportedAlgorithm
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    signing_input = (
        b64(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + b64(json.dumps(claims, separators=(",", ":")).encode())
    )
    try:
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise ConnectorError("The service account private_key could not be read.") from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ConnectorError("The service account private_key must be an RSA key, as Google issues them.")
    signature = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + b64(signature)


def google_scopes(row: dict[str, Any]) -> list[str]:
    for item in row["methods"]:
        if item["type"] == "oauth2":
            return [s for s in item.get("scopes") or [] if s not in _IDENTITY_SCOPES and "/userinfo." not in s]
    return []


def google_service_token(row: dict[str, Any], values: dict[str, str]) -> tuple[str, dict[str, Any]]:
    """Access token for a Google service account key (JWT bearer grant)."""
    try:
        info = json.loads(values.get("serviceAccountJson") or "")
    except ValueError:
        raise ConnectorError("The service account JSON is not valid JSON.") from None
    if not isinstance(info, dict) or info.get("type") != "service_account" or not info.get("client_email") or not info.get("private_key"):
        raise ConnectorError("Paste the whole service account key JSON (type service_account, with client_email and private_key).")
    token_uri = str(info.get("token_uri") or GOOGLE_TOKEN_URL)
    if urlsplit(token_uri).hostname not in {"oauth2.googleapis.com", "accounts.google.com", "www.googleapis.com"}:
        raise ConnectorError("The service account token_uri must be a Google endpoint.")
    scopes = google_scopes(row)
    subject = str(values.get("subject") or "").strip()
    key = hashlib.sha256(
        f"{info['client_email']}|{subject}|{' '.join(scopes)}|{info.get('private_key_id', '')}".encode()
    ).hexdigest()
    token = _cached(key)
    if token:
        return token, info
    now = int(time.time())
    claims: dict[str, Any] = {"iss": info["client_email"], "scope": " ".join(scopes), "aud": token_uri, "iat": now, "exp": now + 3600}
    if subject:
        claims["sub"] = subject
    header = {"alg": "RS256", "typ": "JWT"}
    if info.get("private_key_id"):
        header["kid"] = str(info["private_key_id"])
    assertion = _rs256_jwt(header, claims, str(info["private_key"]))
    with http_client() as client:
        response = client.post(token_uri, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
    data = json_or_none(response)
    if response.status_code >= 400 or not isinstance(data, dict) or not data.get("access_token"):
        raise ConnectorError(
            f"Google refused the service account (HTTP {response.status_code}): {provider_message(data) or 'no access token'}.",
            upstream=response.status_code,
        )
    token = str(data["access_token"])
    _remember(key, token, now + int(data.get("expires_in") or 3600))
    return token, info


def docker_token(values: dict[str, str]) -> str:
    ident, secret = values.get("identifier", ""), values.get("secret", "")
    key = hashlib.sha256(f"docker|{ident}|{secret}".encode()).hexdigest()
    token = _cached(key)
    if token:
        return token
    with http_client() as client:
        response = client.post(DOCKER_TOKEN_URL, json={"identifier": ident, "secret": secret})
    data = json_or_none(response)
    if response.status_code >= 400 or not isinstance(data, dict) or not data.get("access_token"):
        detail = mask(provider_message(data), (secret,)) or "no access token"
        raise ConnectorError(
            f"Docker Hub refused this credential (HTTP {response.status_code}): {detail}.", upstream=response.status_code
        )
    token = str(data["access_token"])
    _remember(key, token, time.time() + 600)
    return token


def google_tokeninfo(token: str) -> dict[str, Any]:
    with http_client() as client:
        response = client.get(GOOGLE_TOKENINFO, params={"access_token": token})
    data = json_or_none(response)
    if response.status_code >= 400 or not isinstance(data, dict):
        raise ConnectorError(f"Google refused the access token (HTTP {response.status_code}).", upstream=response.status_code)
    return data


# -- requests -------------------------------------------------------------------------


def _base(spec: Spec, values: dict[str, Any], name: str) -> str:
    if not complete(spec.base, values):
        missing = ", ".join(group for group in providers.placeholders(spec.base) if not complete("{" + group + "}", values))
        raise ConnectorError(f"The {name} connection is missing {missing}; reconnect it in Settings > Channels > Connectors.", 409)
    return render(spec.base, values).rstrip("/")


def build_url(spec: Spec, cred: Credential, path: str, name: str) -> str:
    """Absolute URL for ``path``; credentials only go to the app's own hosts."""
    values = cred.template_values()
    base = _base(spec, values, name)
    text = str(path or "").strip()
    if text.startswith(("https://", "http://")):
        url = text
    else:
        if text and text[0] not in "/?:":
            text = "/" + text
        url = base + text
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise ConnectorError("The request URL is not valid.") from None
    if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
        raise ConnectorError("The request URL is not valid.")
    allowed = {(urlsplit(base).hostname or "").lower(), *spec.hosts}
    if not host_allowed(host, allowed):
        raise ConnectorError(f"{name} credentials are only sent to {', '.join(sorted(allowed))}, not to {host}.")
    if parts.scheme == "http" and not base.startswith("http://"):
        raise ConnectorError(f"{name} only accepts HTTPS URLs.")
    return url


def _auth(spec: Spec, cred: Credential, headers: dict[str, str], params: dict[str, Any]) -> httpx.Auth | None:
    rules = spec.oauth_auth if cred.kind in {"oauth2", "service_account"} else spec.key_auth
    values = cred.template_values()
    auth: httpx.Auth | None = None
    for kind, name, template in rules:
        if not complete(template, values) or not complete(name, values):
            continue
        if kind == "header":
            headers[name] = render(template, values)
        elif kind == "query":
            params[name] = render(template, values)
        elif kind == "basic":
            auth = httpx.BasicAuth(render(name, values), render(template, values))
        elif kind == "digest":
            auth = httpx.DigestAuth(render(name, values), render(template, values))
    return auth


def _safe_headers(headers: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(headers, dict):
        return out
    for key, value in list(headers.items())[:30]:
        name, text = str(key).strip(), str(value)
        if not name or name.lower() in _BLOCKED_HEADERS or name.lower().startswith("proxy-"):
            continue
        if "\n" in text or "\r" in text or len(text) > 4000:
            continue
        out[name] = text
    return out


def _clean_params(params: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(params, dict):
        return out
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[str(key)] = "true" if value else "false"
        elif isinstance(value, (list, tuple)):
            out[str(key)] = [str(item).lower() if isinstance(item, bool) else str(item) for item in value]
        elif isinstance(value, (dict,)):
            out[str(key)] = json.dumps(value)
        else:
            out[str(key)] = str(value)
    return out


def form_pairs(data: Any, lists: str = "repeat") -> list[tuple[str, str]]:
    """Flatten a JSON body for form encoding: ``{"a": {"b": 1}}`` -> ``a[b]=1``.

    ``lists="index"`` writes ``items[0][price]`` and ``expand[0]`` (Stripe's own
    client libraries); ``"repeat"`` repeats the key for a list of scalars
    (``to=a&to=b``, Mailgun) and indexes lists of objects.
    """
    if data is None:
        return []
    if not isinstance(data, dict):
        raise ConnectorError("A form body must be a JSON object of fields.")
    out: list[tuple[str, str]] = []

    def walk(prefix: str, value: Any) -> None:
        if value is None:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                walk(f"{prefix}[{key}]" if prefix else str(key), child)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                if lists == "index" or isinstance(child, (dict, list, tuple)):
                    walk(f"{prefix}[{index}]", child)
                else:
                    walk(prefix, child)
        elif isinstance(value, bool):
            out.append((prefix, "true" if value else "false"))
        else:
            out.append((prefix, str(value)))

    for key, value in data.items():
        walk(str(key), value)
    return out


def _form_dict(pairs: list[tuple[str, str]]) -> dict[str, str | list[str]]:
    grouped: dict[str, list[str]] = {}
    for key, value in pairs:
        grouped.setdefault(key, []).append(value)
    return {key: values[0] if len(values) == 1 else values for key, values in grouped.items()}


def _read_upload(path: Path, label: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError:
        raise ConnectorError(f"{label} {path.name} was not found.") from None
    if not path.is_file():
        raise ConnectorError(f"{label} {path.name} is not a file.")
    if size > MAX_UPLOAD_BYTES:
        raise ConnectorError(f"{label} {path.name} is larger than {MAX_UPLOAD_BYTES // 1_000_000} MB.")
    return path.read_bytes()


def _guess_type(path: Path | None, fallback: str = "application/octet-stream") -> str:
    if path is None:
        return fallback
    return mimetypes.guess_type(path.name)[0] or fallback


def send(
    spec: Spec,
    cred: Credential,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    raw: str | None = None,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
    form: list[tuple[str, str]] | None = None,
    files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
    sink: Path | None = None,
) -> tuple[httpx.Response, bytes, bool]:
    """One authenticated call. With ``sink``, a 2xx body streams to that file."""
    values = cred.template_values()
    request_headers = {"Accept": "application/json"}
    for name, template in spec.headers.items():
        if complete(template, values):
            request_headers[name] = render(template, values)
    request_headers.update(headers or {})
    query = dict(params or {})
    auth = _auth(spec, cred, request_headers, query)
    kwargs: dict[str, Any] = {}
    if raw is not None:
        kwargs["content"] = raw.encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    elif content is not None:
        kwargs["content"] = content
    elif files:
        # httpx writes the multipart boundary into Content-Type itself.
        request_headers = {k: v for k, v in request_headers.items() if k.lower() != "content-type"}
        kwargs["files"] = files
        if form:
            kwargs["data"] = _form_dict(form)
    elif form is not None:
        kwargs["content"] = urlencode(form).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif json_body is not None:
        kwargs["json"] = json_body
    chunks: list[bytes] = []
    size = 0
    truncated = False
    try:
        with http_client() as client:
            with client.stream(method, url, params=query or None, headers=request_headers, auth=auth, **kwargs) as response:
                if sink is not None and 200 <= response.status_code < 300:
                    sink.parent.mkdir(parents=True, exist_ok=True)
                    part = sink.with_name(sink.name + ".part")
                    try:
                        with open(part, "wb") as handle:
                            for chunk in response.iter_bytes():
                                size += len(chunk)
                                if size > MAX_DOWNLOAD_BYTES:
                                    raise ConnectorError(
                                        f"The download is larger than {MAX_DOWNLOAD_BYTES // 1_000_000} MB.", 413
                                    )
                                handle.write(chunk)
                        os.replace(part, sink)
                    finally:
                        if part.exists():
                            part.unlink()
                else:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            truncated = True
                            break
                        chunks.append(chunk)
    except httpx.TimeoutException:
        raise ConnectorError("The app did not answer in time; try again.", 504) from None
    except httpx.HTTPError as exc:
        raise ConnectorError(f"The app could not be reached ({type(exc).__name__}).", 502) from None
    return response, b"".join(chunks), truncated


def verify_call(spec: Spec, cred: Credential, check: Verify, name: str) -> Any:
    path = render(check.path, cred.template_values())
    url = build_url(spec, cred, path, name)
    response, body, _ = send(spec, cred, check.method, url, json_body=check.json, raw=check.raw, headers=dict(check.headers))
    data = _decode(response, body)
    status = response.status_code
    if status in check.accept:
        return data
    detail = mask(provider_message(data), cred.hidden)
    suffix = f": {detail}" if detail else ""
    if status == 429:
        raise ConnectorError(
            f"{name} is rate limiting this credential (HTTP 429){suffix}. Try again in a minute.", 429, upstream=status
        )
    if status >= 500:
        raise ConnectorError(f"{name} is unavailable (HTTP {status}){suffix}.", 502, upstream=status)
    if status == 404:
        raise ConnectorError(
            f"{name} answered 404 Not Found at {mask(url.split('?')[0], cred.hidden)}: check the URL, domain or "
            "subdomain you entered.",
            400,
            upstream=status,
        )
    if not 200 <= status < 300:
        raise ConnectorError(f"{name} refused this credential (HTTP {status}){suffix}.", 400, upstream=status)
    problem = spec.check(data) if spec.check else ""
    if problem:
        raise ConnectorError(mask(problem, cred.hidden), 400, upstream=status)
    return data


def verify_credential(
    service: str,
    kind: str,
    values: dict[str, str],
    *,
    access_token: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prove a credential with one cheap call; return the account profile."""
    row = catalog.provider(service)
    service = row["service"]
    spec = spec_for(service)
    prepared = providers.prepare(service, values)
    secrets = tuple(v for v in prepared.values() if isinstance(v, str))
    cred = Credential(service, kind, prepared, access_token, dict(metadata or {}), hidden=secrets + (access_token,))
    profile: dict[str, str] = {}
    if kind == "service_account":
        cred.access_token, info = google_service_token(row, prepared)
        email = str(info.get("client_email") or "")
        profile = {"id": email, "name": prepared.get("subject") or email}
        if spec.verify_for(kind, prepared) is None:
            # No cheap list call in this API (Sheets, Forms): prove the token itself.
            google_tokeninfo(cred.access_token)
    elif spec.exchange == "docker_hub":
        cred.access_token = docker_token(prepared)
        profile = {"id": prepared.get("identifier", ""), "name": prepared.get("identifier", "")}
    elif kind == "oauth2" and spec.google:
        info = google_tokeninfo(access_token)
        if info.get("email"):
            profile = {"id": str(info.get("sub") or info["email"]), "name": str(info["email"])}
    cred.hidden = cred.hidden + (cred.access_token,)
    check = spec.verify_for(kind, prepared)
    if check is not None:
        data = verify_call(spec, cred, check, row["name"])
        found_id = dig(data, check.id) if check.id else None
        found_name = dig(data, check.name) if check.name else None
        if found_id is not None or found_name is not None:
            profile = {
                "id": str(found_id if found_id is not None else profile.get("id") or found_name or ""),
                "name": str(profile.get("name") if spec.google and profile.get("name") else (found_name or found_id or "")),
            }
    return {"profile": profile, "values": prepared, "access_token": cred.access_token}


def load_credential(service: str, store: ConnectorStore) -> Credential:
    """The stored credential, ready to send (OAuth refreshed, exchanges done)."""
    row = catalog.provider(service)
    service = row["service"]
    connection = store.connection(service)
    if not connection or not connection.get("connected"):
        raise ConnectorError(
            f"{row['name']} is not connected. Connect it in Settings > Channels > Connectors, or call connectors action=connect.",
            409,
        )
    if not connection.get("enabled", True):
        raise ConnectorError(f"{row['name']} is turned off in Settings > Channels > Connectors.", 409)
    bag = store.secrets(f"conn:{service}")
    secret_values = {str(k): str(v) for k, v in (bag.get("values") or {}).items()}
    values = providers.prepare(service, {**dict(connection.get("fields") or {}), **secret_values})
    kind = str(connection.get("method") or "api_key")
    hidden = tuple(v for v in (*secret_values.values(), bag.get("access_token"), bag.get("refresh_token")) if isinstance(v, str) and v)
    cred = Credential(service, kind, values, str(bag.get("access_token") or ""), dict(connection.get("metadata") or {}), hidden)
    if kind == "oauth2":
        expires = int(connection.get("expires_at") or 0)
        if expires and expires - 60 <= time.time() and bag.get("refresh_token"):
            from navin.connectors import oauth

            cred.access_token = oauth.refresh(store, service) or cred.access_token
        if not cred.access_token:
            raise ConnectorError(f"{row['name']} has no access token; reconnect it in Settings > Channels > Connectors.", 409)
    elif kind == "service_account":
        cred.access_token, _ = google_service_token(row, values)
    elif spec_for(service).exchange == "docker_hub":
        cred.access_token = docker_token(values)
    cred.hidden = cred.hidden + (cred.access_token,)
    return cred


def _next_link(link: str) -> str:
    for part in link.split(","):
        if 'rel="next"' in part and "<" in part and ">" in part:
            return part[part.index("<") + 1:part.index(">")]
    return ""


def api_base(service: str, store: ConnectorStore | None = None) -> str:
    """The API base of a connected app with its own values filled in (no secret in it).

    ``https://{dc}.api.mailchimp.com/3.0`` reads ``https://us21.api.mailchimp.com/3.0``
    once Mailchimp is connected; the template is returned while it is not.
    """
    store = store or ConnectorStore()
    row = catalog.provider(service)
    service = row["service"]
    spec = spec_for(service)
    connection = store.connection(service)
    if not connection or not connection.get("connected"):
        return spec.base
    secret_values = {
        str(k): str(v) for k, v in (store.secrets(f"conn:{service}").get("values") or {}).items()
    }
    try:
        values = providers.prepare(service, {**dict(connection.get("fields") or {}), **secret_values})
    except ConnectorError:
        return spec.base
    template: dict[str, Any] = {
        k: v for k, v in (connection.get("metadata") or {}).items() if isinstance(v, (str, int))
    }
    template.update(values)
    if not complete(spec.base, template):
        return spec.base
    return mask(render(spec.base, template).rstrip("/"), tuple(secret_values.values()))


def _body_parts(
    spec: Spec,
    verb: str,
    body: Any,
    body_format: str,
    files: Any,
    body_file: Path | None,
    content_type: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """``send`` keyword arguments and extra headers for the request body."""
    fmt = (body_format or "").strip().lower()
    if not fmt:
        fmt = "multipart" if files else "raw" if body_file is not None else spec.body_format
    if fmt not in BODY_FORMATS:
        raise ConnectorError(f"body_format must be one of {', '.join(BODY_FORMATS)}.")
    has_body = body is not None or bool(files) or body_file is not None
    if verb in {"GET", "HEAD"}:
        if files or body_file is not None:
            raise ConnectorError("Uploads need POST, PUT or PATCH.")
        return {}, {}
    if not has_body:
        return {}, {}
    if fmt == "json":
        if files or body_file is not None:
            raise ConnectorError("Use body_format=multipart with files, or raw with body_file.")
        return {"json_body": body}, {}
    if fmt == "form":
        if files or body_file is not None:
            raise ConnectorError("Use body_format=multipart to send files with form fields.")
        return {"form": form_pairs(body, spec.form_lists)}, {}
    if fmt == "multipart":
        if body_file is not None:
            raise ConnectorError("body_file goes with body_format=raw; use files for multipart.")
        if not files or not isinstance(files, dict):
            raise ConnectorError("body_format=multipart needs files (form field -> file); use form for fields only.")
        parts: list[tuple[str, tuple[str, bytes, str]]] = []
        total = 0
        for name, path in list(files.items())[:20]:
            target = Path(path)
            data = _read_upload(target, "File")
            total += len(data)
            if total > MAX_UPLOAD_BYTES:
                raise ConnectorError(f"The files are larger than {MAX_UPLOAD_BYTES // 1_000_000} MB together.")
            parts.append((str(name), (target.name, data, _guess_type(target))))
        return {"files": parts, "form": form_pairs(body, spec.form_lists)}, {}
    # raw: a file or a text body sent as is.
    if body_file is not None:
        data = _read_upload(Path(body_file), "File")
        kind = content_type or _guess_type(Path(body_file))
    elif isinstance(body, (dict, list)):
        data = json.dumps(body).encode("utf-8")
        kind = content_type or "application/json"
    else:
        data = str(body).encode("utf-8")
        kind = content_type or "text/plain; charset=utf-8"
    return {"content": data}, {"Content-Type": kind}


def request(
    service: str,
    method: str = "GET",
    path: str = "",
    *,
    params: Any = None,
    body: Any = None,
    headers: Any = None,
    store: ConnectorStore | None = None,
    save_to: Path | None = None,
    body_format: str = "",
    files: Any = None,
    body_file: Path | None = None,
    content_type: str = "",
) -> dict[str, Any]:
    """Call the connected app; the credential is injected here, never shown.

    The body goes as JSON unless the app or ``body_format`` says otherwise:
    ``form`` (Stripe, Mailgun), ``multipart`` with ``files`` (field -> file),
    ``raw`` with ``body_file`` (uploads). ``save_to`` streams the answer to a file.
    """
    store = store or ConnectorStore()
    row = catalog.provider(service)
    service = row["service"]
    spec = spec_for(service)
    verb = str(method or "GET").strip().upper()
    if verb not in METHODS:
        raise ConnectorError("method must be GET, POST, PUT, PATCH, DELETE or HEAD.")
    body_kwargs, body_headers = _body_parts(spec, verb, body, body_format, files, body_file, content_type)
    cred = load_credential(service, store)
    url = build_url(spec, cred, path, row["name"])
    query = _clean_params(params)
    extra = {**body_headers, **_safe_headers(headers)}
    response, raw, truncated = send(spec, cred, verb, url, params=query, headers=extra, sink=save_to, **body_kwargs)
    if response.status_code == 401 and cred.kind == "oauth2":
        from navin.connectors import oauth

        fresh = oauth.refresh(store, service)
        if fresh:
            cred.access_token = fresh
            cred.hidden = cred.hidden + (fresh,)
            response, raw, truncated = send(spec, cred, verb, url, params=query, headers=extra, sink=save_to, **body_kwargs)
    data = _decode(response, raw)
    status = response.status_code
    problem = spec.check(data) if spec.check and data is not None else ""
    result: dict[str, Any] = {
        "service": service,
        "status": status,
        "ok": 200 <= status < 300 and not problem,
        "url": mask(str(response.request.url), cred.hidden),
        "content_type": response.headers.get("content-type", ""),
    }
    if problem:
        result["error"] = mask(problem, cred.hidden)
    elif status >= 400:
        result["error"] = mask(provider_message(data) or f"HTTP {status}", cred.hidden)
    if save_to is not None and 200 <= status < 300:
        result.update({"saved_to": str(save_to), "bytes": save_to.stat().st_size if save_to.exists() else 0})
    elif data is None and raw:
        result.update({"bytes": len(raw), "note": "Binary response not shown; call again with save_to to write it to a file."})
    else:
        result["data"] = _mask_data(data, cred.hidden)
    if truncated:
        result["truncated"] = True
    following = _next_link(response.headers.get("link", ""))
    if following:
        result["next"] = mask(following, cred.hidden)
    return result
