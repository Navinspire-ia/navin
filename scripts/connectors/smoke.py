#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Live smoke test of every connector, on Navin's own code path.

    python scripts/connectors/smoke.py [--json out.json] [--review] [--all] [service ...]

Run it where Navin is installed (``pip install -e .``). No real credential is
needed or used. Every probe sends deliberately invalid
values through the very functions Settings and the agent call, against the
real provider APIs:

- verify: ``runtime.verify_credential`` for each auth method (the check the
  Connect button runs before anything is stored).
- request: ``runtime.request`` on a stored connection that holds the same
  invalid values (the path of the agent's connectors tool).
- authorize / token / refresh (OAuth apps): ``oauth.start`` and a GET of the
  authorization URL, ``oauth.finish`` with an invalid code, ``oauth.refresh``
  with an invalid refresh token.

OK: the app refused (400 / 401 / 403, or its own error in a 2xx body), which
proves the URL, the credential placement and the host. FAIL: the app accepted
an invalid credential, answered 404 / 405 / 410 (wrong path), could not be
reached (wrong host), or a secret shows in a message. INCONCLUSIVE: rate
limit, 5xx, timeout, or a customer-only host that does not exist for a probe.
``--review`` lists the OK refusals that are not a plain 401 / 403, to read
them. Exit status 1 when a probe FAILs.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from navin.connectors import catalog, manager, oauth, providers, runtime
from navin.connectors.catalog import ConnectorError
from navin.connectors.store import ConnectorStore

INVALID = "navin-invalid-0000000000000000"
# Well-formed but invalid keys, built at run time: secret scanners and GitHub
# push protection flag these shapes even when every digit is zero.
MAILCHIMP_KEY = "0" * 32 + "-us1"
HUBSPOT_TOKEN = "pat-" + "na1-" + "-".join(["0" * 8, "0" * 4, "0" * 4, "0" * 4, "0" * 12])
REDIRECT = "http://127.0.0.1:8765" + oauth.CALLBACK_PATH
# Values that reach a real host for apps whose API lives on the customer's own domain.
TENANT: dict[str, dict[str, str]] = {
    "agora": {"customerId": "navin-invalid"},
    "zendesk": {"subdomain": "support", "email": "navin@example.com"},
    "wordpress": {"siteUrl": "https://make.wordpress.org", "username": "navin-invalid"},
    "erpnext": {"baseUrl": "https://frappe.io", "apiSecret": INVALID},
    "sage_hr": {"domain": "demo"},
    "shopify": {"shopDomain": "allbirds.myshopify.com", "shopSubdomain": "allbirds"},
    "woocommerce": {"storeUrl": "https://woocommerce.com", "consumerKey": "ck_" + INVALID, "consumerSecret": "cs_" + INVALID},
    "databricks": {"host": "https://community.cloud.databricks.com"},
    "grafana": {"baseUrl": "https://play.grafana.org"},
    "grafana_cloud": {"orgSlug": "navin"},
    "datadog": {"applicationKey": INVALID},
    "mongo_db_atlas_administration": {"privateKey": INVALID},
    "higgsfield_ai": {"apiSecret": INVALID},
    "mailchimp": {"apiKey": MAILCHIMP_KEY},
    "docker_hub": {"apiKey": "navinsmoke:dckr_pat_" + INVALID},
    "twitter": {"appBearerToken": INVALID},
    "googleads": {"developerToken": INVALID, "customerId": "1234567890"},
    # Well-formed invalid keys get the plain "invalid key" answer instead of a format error.
    "hubspot": {"apiKey": HUBSPOT_TOKEN},
    "cloudflare_dns": {"apiKey": "0123456789abcdefABCDEF0123456789abcdefAB"},
    "yelp": {"apiKey": "0" * 128},
}
ACCESS_TOKENS = {
    "hubspot": HUBSPOT_TOKEN,
    "cloudflare_dns": "0123456789abcdefABCDEF0123456789abcdefAB",
}
# Customer hosts that do not exist for a probe: a DNS failure or a 404 there is expected.
TENANT_ONLY = {"sage_hr", "databricks"}
# APIs without a cheap account call: a path that still checks the token first.
REQUEST_PATHS = {
    "googlesheets": "/spreadsheets/navin-smoke-probe",
    "googleforms": "/forms/navin-smoke-probe",
    # The key is exchanged for a bearer first; this path is reached only with a valid one.
    "docker_hub": "/v2/access-tokens",
}
WRONG_PATH = {404, 405, 410}


class _NoKeyring:
    """Probe stores keep their secrets in a throwaway Fernet file."""

    def ready(self) -> bool:
        return False


def _store() -> ConnectorStore:
    root = Path(tempfile.mkdtemp(prefix="navin-smoke-"))
    config = root / "config.json"
    config.write_text("{}", encoding="utf-8")
    return ConnectorStore(root / "connectors", vault=_NoKeyring(), config_path=config)


_SERVICE_ACCOUNT: dict[str, str] = {}


def _service_account_json() -> str:
    """A well-formed key Google has never issued: its token endpoint must refuse it."""
    if "json" not in _SERVICE_ACCOUNT:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode()
        _SERVICE_ACCOUNT["json"] = json.dumps({
            "type": "service_account",
            "project_id": "navin-smoke",
            "private_key_id": uuid.uuid4().hex,
            "private_key": pem,
            "client_email": "navin-smoke@navin-smoke.iam.gserviceaccount.com",
            "client_id": "100000000000000000000",
            "token_uri": "https://oauth2.googleapis.com/token",
        })
    return _SERVICE_ACCOUNT["json"]


def _key_values(service: str, method: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in method["fields"]:
        key = field["key"]
        if key == "serviceAccountJson":
            values[key] = _service_account_json()
        elif field.get("defaultValue") and not field.get("secret"):
            values[key] = str(field["defaultValue"])
        elif field.get("required") or key == "apiKey":
            values[key] = INVALID
    keys = {field["key"] for field in method["fields"]}
    values.update({k: v for k, v in TENANT.get(service, {}).items() if k in keys})
    return values


def _oauth_fields(service: str, method: dict[str, Any]) -> dict[str, str]:
    fields = {f["key"]: str(f.get("defaultValue") or "") for f in method["fields"]}
    fields.update({k: v for k, v in TENANT.get(service, {}).items() if k in fields})
    return {k: v for k, v in fields.items() if v}


def _secrets_of(method: dict[str, Any], values: dict[str, str]) -> tuple[str, ...]:
    secret_keys = {f["key"] for f in method["fields"] if f.get("secret")}
    found = [values[k] for k in secret_keys if values.get(k)]
    return tuple(value for value in (*found, INVALID) if len(value) >= 6 and not value.lstrip().startswith("{"))


def _leaks(text: str, secrets: tuple[str, ...]) -> bool:
    return any(secret in text for secret in secrets)


def _from_error(exc: ConnectorError, service: str) -> str:
    up = exc.upstream
    if up == 0:
        if exc.status == 504:
            return "INCONCLUSIVE"
        return "INCONCLUSIVE" if service in TENANT_ONLY and exc.status == 502 else "FAIL"
    if up in WRONG_PATH:
        return "INCONCLUSIVE" if service in TENANT_ONLY else "FAIL"
    if up == 429 or up >= 500:
        return "INCONCLUSIVE"
    return "OK"


def _from_status(status: int, ok: bool, service: str) -> str:
    if ok:
        return "FAIL"
    if 200 <= status < 300:
        return "OK"
    if status in WRONG_PATH:
        return "INCONCLUSIVE" if service in TENANT_ONLY else "FAIL"
    if status == 429 or status >= 500:
        return "INCONCLUSIVE"
    return "OK"


def _row(service: str, probe: str, result: str, status: int, url: str, detail: str, secrets: tuple[str, ...]) -> dict[str, Any]:
    if _leaks(detail, secrets) or _leaks(url, secrets):
        result, detail = "FAIL", "a secret shows in the answer: " + runtime.mask(detail, secrets)
    return {"service": service, "probe": probe, "result": result, "status": status, "url": url.split("?")[0],
            "detail": " ".join(detail.split())[:220]}


def probe_verify(service: str, method: dict[str, Any]) -> dict[str, Any]:
    kind = method["type"]
    probe = f"verify:{kind}"
    token = ""
    metadata: dict[str, Any] = {}
    if kind == "oauth2":
        values = _oauth_fields(service, method)
        if service == "salesforce":
            metadata = {"instance_url": "https://login.salesforce.com"}
        token = ACCESS_TOKENS.get(service, INVALID)
        secrets = (token,)
    else:
        values = _key_values(service, method)
        secrets = _secrets_of(method, values)
    try:
        result = runtime.verify_credential(service, kind, values, access_token=token, metadata=metadata)
    except ConnectorError as exc:
        return _row(service, probe, _from_error(exc, service), exc.upstream, "", exc.message, secrets)
    return _row(service, probe, "FAIL", 200, "", f"an invalid credential was accepted: {result.get('profile')}", secrets)


def probe_request(service: str, method: dict[str, Any]) -> dict[str, Any]:
    kind = method["type"]
    probe = f"request:{kind}"
    spec = providers.spec_for(service)
    store = _store()
    metadata: dict[str, Any] = {}
    try:
        if kind == "oauth2":
            public = providers.prepare(service, _oauth_fields(service, method))
            metadata = {"instance_url": "https://login.salesforce.com"} if service == "salesforce" else {}
            token = ACCESS_TOKENS.get(service, INVALID)
            store.save_connection(service, method="oauth2", fields=public, secret_values={}, profile={},
                                  metadata=metadata, access_token=token)
            values: dict[str, str] = dict(public)
            secrets: tuple[str, ...] = (token,)
        else:
            raw = _key_values(service, method)
            secrets = _secrets_of(method, raw)
            public, secret = manager.clean_values(method, raw)
            values = providers.prepare(service, {**public, **secret})
            store.save_connection(service, method=kind, profile={}, secret_values=secret,
                                  fields={k: str(values.get(k, v)) for k, v in public.items()})
    except ConnectorError as exc:
        return _row(service, probe, "FAIL", 0, "", f"probe setup: {exc.message}", (INVALID,))
    check = spec.verify_for(kind, values)
    if check is None:
        verb, path, body, headers = "GET", REQUEST_PATHS[service], None, {}
    else:
        verb, body, headers = check.method, check.json, dict(check.headers)
        path = providers.render(check.path, {**metadata, **values})
    try:
        result = runtime.request(service, verb, path, body=body, headers=headers, store=store)
    except ConnectorError as exc:
        return _row(service, probe, _from_error(exc, service), exc.upstream, "", exc.message, secrets)
    detail = str(result.get("error") or "")
    if result.get("ok"):
        detail = "an invalid credential was accepted"
    text = json.dumps(result, default=str)
    if _leaks(text, secrets):
        return _row(service, probe, "FAIL", int(result["status"]), result.get("url", ""),
                    "a secret shows in the result", ())
    return _row(service, probe, _from_status(int(result["status"]), bool(result.get("ok")), service),
                int(result["status"]), str(result.get("url") or ""), detail, secrets)


def probe_oauth(service: str, method: dict[str, Any]) -> list[dict[str, Any]]:
    store = _store()
    secrets = (INVALID,)
    fields = _oauth_fields(service, method)
    try:
        oauth.save_app(store, service, client_id=INVALID, client_secret=INVALID, redirect_uri=REDIRECT)
        started = oauth.start(store, service, fields)
    except ConnectorError as exc:
        return [_row(service, "oauth:start", "FAIL", 0, "", exc.message, ())]
    rows: list[dict[str, Any]] = []
    url = started["authorization_url"]
    try:
        with httpx.Client(timeout=runtime.TIMEOUT, follow_redirects=False,
                          headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Navin"}) as http:
            status = http.get(url).status_code
        result = "FAIL" if status in WRONG_PATH else "INCONCLUSIVE" if status == 429 or status >= 500 else "OK"
        rows.append(_row(service, "oauth:authorize", result, status, url, "", ()))
    except httpx.HTTPError as exc:
        rows.append(_row(service, "oauth:authorize", "FAIL", 0, url, type(exc).__name__, ()))
    names = {**oauth._AUTH_NAMES, **(method.get("authorizationRequestFields") or {})}
    state = parse_qs(urlsplit(url).query).get(str(names.get("state") or "state"), [""])[0]
    try:
        oauth.finish(store, {"state": state, "code": INVALID})
        rows.append(_row(service, "oauth:token", "FAIL", 200, method["tokenUrl"], "a token was issued for an invalid code", secrets))
    except ConnectorError as exc:
        rows.append(_row(service, "oauth:token", _from_error(exc, service), exc.upstream, method["tokenUrl"], exc.message, secrets))
    public = providers.prepare(service, fields)
    store.save_connection(service, method="oauth2", fields=public, secret_values={}, profile={},
                          access_token=INVALID, refresh_token=INVALID, expires_at=1)
    refresh_url = method.get("refreshTokenUrl") or method["tokenUrl"]
    if oauth.refresh(store, service):
        rows.append(_row(service, "oauth:refresh", "FAIL", 200, refresh_url, "a token was issued for an invalid refresh token", secrets))
    else:
        error = str(store.status(service).get("error") or "")
        found = re.search(r"HTTP (\d{3})", error)
        if "in time" in error:
            result, status = "INCONCLUSIVE", 0
        elif "could not be reached" in error:
            result, status = "FAIL", 0
        elif found:
            status = int(found.group(1))
            result = "FAIL" if status in WRONG_PATH else "INCONCLUSIVE" if status == 429 or status >= 500 else "OK"
        else:
            result, status = ("OK", 200) if "refused" in error else ("FAIL", 0)
        rows.append(_row(service, "oauth:refresh", result, status, refresh_url, error, secrets))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Live connector smoke test with invalid credentials.")
    parser.add_argument("services", nargs="*")
    parser.add_argument("--json", dest="json_path")
    parser.add_argument("--review", action="store_true", help="list OK refusals that are not a plain 401/403")
    parser.add_argument("--all", action="store_true", help="print every probe")
    args = parser.parse_args()
    wanted = {catalog.resolve_service(name) or name for name in args.services}
    jobs: list[tuple[Any, str, dict[str, Any]]] = []
    for row in catalog.providers():
        if wanted and row["service"] not in wanted:
            continue
        for method in row["methods"]:
            jobs.append((probe_verify, row["service"], method))
            jobs.append((probe_request, row["service"], method))
            if method["type"] == "oauth2":
                jobs.append((probe_oauth, row["service"], method))
    started = time.time()
    with ThreadPoolExecutor(max_workers=12) as pool:
        batches = list(pool.map(lambda job: job[0](job[1], job[2]), jobs))
    results = [item for batch in batches for item in (batch if isinstance(batch, list) else [batch])]
    counts: dict[str, int] = {}
    for item in results:
        counts[item["result"]] = counts.get(item["result"], 0) + 1
        plain = item["result"] == "OK" and item["status"] in {401, 403}
        if args.all or item["result"] != "OK" or (args.review and not plain and item["probe"] != "oauth:authorize"):
            print(f"{item['result']:<13} {item['service']:<30} {item['probe']:<24} {item['status']!s:<4} "
                  f"{item['url']} {item['detail']}")
    services = len({item["service"] for item in results})
    print(f"\n{len(results)} probes over {services} connectors in {time.time() - started:.1f}s: "
          + ", ".join(f"{key} {value}" for key, value in sorted(counts.items())))
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 1 if counts.get("FAIL") else 0


if __name__ == "__main__":
    raise SystemExit(main())
