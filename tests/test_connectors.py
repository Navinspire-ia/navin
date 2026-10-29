# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Native connectors: catalog, request rules, vault, OAuth, HTTP API, agent tool."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from navin.connectors import catalog, manager, oauth, providers, runtime
from navin.connectors.catalog import ConnectorError
from navin.connectors.store import ConnectorStore

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "webui" / "src" / "assets" / "connectors"

# The table the user asked for: category order, then (service, auth) in order.
REQUESTED = [
    ("communication", [("slack", "oauth"), ("slackbot", "oauth"), ("microsoft_teams", "oauth"), ("discord", "oauth"),
                       ("gmail", "oauth"), ("outlook", "oauth")]),
    ("video", [("googlemeet", "oauth"), ("zoom", "oauth"), ("agora", "apikey")]),
    ("calendar", [("googlecalendar", "oauth"), ("outlook_calendar", "oauth"), ("calendly", "both"),
                  ("googletasks", "oauth"), ("motion", "apikey")]),
    ("documents", [("notion", "both"), ("googledrive", "oauth"), ("googlesheets", "oauth"), ("dropbox", "oauth"),
                   ("one_drive", "oauth"), ("excel", "oauth"), ("airtable", "both")]),
    ("forms", [("googleforms", "oauth"), ("typeform", "both")]),
    ("cms", [("wordpress", "apikey"), ("readme", "apikey")]),
    ("crm", [("salesforce", "oauth"), ("hubspot", "oauth"), ("zendesk", "apikey")]),
    ("prospecting", [("hunter", "apikey"), ("apollo", "apikey"), ("anymail_finder", "apikey"), ("crunchbase", "apikey")]),
    ("erp", [("erpnext", "apikey")]),
    ("hr", [("sage_hr", "apikey")]),
    ("ecommerce", [("shopify", "oauth"), ("woocommerce", "apikey")]),
    ("payments", [("stripe", "apikey")]),
    ("social", [("tiktok_business", "oauth"), ("youtube", "both"), ("twitter", "oauth")]),
    ("marketing", [("google_analytics", "oauth"), ("google_search_console", "oauth"), ("googleads", "oauth"),
                   ("ahrefs", "apikey")]),
    ("emailing", [("mailchimp", "apikey"), ("brevo", "apikey"), ("mailgun", "apikey"), ("sendgrid", "apikey"),
                  ("emaillistverify", "apikey")]),
    ("search", [("tavily", "apikey"), ("serpapi", "apikey"), ("apify", "apikey")]),
    ("geo", [("google_maps", "apikey"), ("google_address_validation", "apikey"), ("yelp", "apikey"), ("iplocate", "apikey")]),
    ("data", [("supabase", "both"), ("pinecone", "apikey"), ("mongo_db_atlas_administration", "apikey"),
              ("databricks", "apikey")]),
    ("cloud", [("cloudflare_dns", "both"), ("vercel", "apikey"), ("laravel_cloud", "apikey"), ("docker_hub", "apikey"),
               ("circleci", "apikey"), ("ngrok", "apikey")]),
    ("security", [("permit_io", "apikey")]),
    ("monitoring", [("sentry", "oauth"), ("datadog", "apikey"), ("grafana", "apikey"), ("grafana_cloud", "apikey")]),
    ("design", [("figma", "both"), ("canva", "oauth")]),
    ("media", [("heygen", "apikey"), ("higgsfield_ai", "apikey")]),
    ("voice", [("elevenlabs", "apikey"), ("deepgram", "apikey")]),
    ("crypto", [("alchemy", "apikey"), ("crypto_apis", "apikey")]),
]
ALL = [(cat, service, auth) for cat, rows in REQUESTED for service, auth in rows]

# Display names exactly as the requested table spells them.
NAMES = {
    "slack": "Slack", "slackbot": "Slack Bot", "microsoft_teams": "Microsoft Teams", "discord": "Discord",
    "gmail": "Gmail", "outlook": "Outlook", "googlemeet": "Google Meet", "zoom": "Zoom", "agora": "Agora",
    "googlecalendar": "Google Calendar", "outlook_calendar": "Outlook Calendar", "calendly": "Calendly",
    "googletasks": "Google Tasks", "motion": "Motion", "notion": "Notion", "googledrive": "Google Drive",
    "googlesheets": "Google Sheets", "dropbox": "Dropbox", "one_drive": "OneDrive", "excel": "Excel",
    "airtable": "Airtable", "googleforms": "Google Forms", "typeform": "Typeform", "wordpress": "WordPress",
    "readme": "ReadMe", "salesforce": "Salesforce", "hubspot": "HubSpot", "zendesk": "Zendesk", "hunter": "Hunter",
    "apollo": "Apollo", "anymail_finder": "Anymail Finder", "crunchbase": "Crunchbase", "erpnext": "ERPNext",
    "sage_hr": "Sage HR", "shopify": "Shopify", "woocommerce": "WooCommerce", "stripe": "Stripe",
    "tiktok_business": "TikTok", "youtube": "YouTube", "twitter": "X (Twitter)", "google_analytics": "Google Analytics",
    "google_search_console": "Google Search Console", "googleads": "Google Ads", "ahrefs": "Ahrefs",
    "mailchimp": "Mailchimp", "brevo": "Brevo", "mailgun": "Mailgun", "sendgrid": "SendGrid",
    "emaillistverify": "EmailListVerify", "tavily": "Tavily", "serpapi": "SerpApi", "apify": "Apify",
    "google_maps": "Google Maps", "google_address_validation": "Google Address Validation", "yelp": "Yelp",
    "iplocate": "IPLocate", "supabase": "Supabase", "pinecone": "Pinecone",
    "mongo_db_atlas_administration": "MongoDB Atlas Administration", "databricks": "Databricks",
    "cloudflare_dns": "Cloudflare DNS", "vercel": "Vercel", "laravel_cloud": "Laravel Cloud", "docker_hub": "Docker Hub",
    "circleci": "CircleCI", "ngrok": "ngrok", "permit_io": "Permit.io", "sentry": "Sentry", "datadog": "Datadog",
    "grafana": "Grafana", "grafana_cloud": "Grafana Cloud", "figma": "Figma", "canva": "Canva", "heygen": "HeyGen",
    "higgsfield_ai": "Higgsfield AI", "elevenlabs": "ElevenLabs", "deepgram": "Deepgram", "alchemy": "Alchemy",
    "crypto_apis": "Crypto APIs",
}
PROBE = "/navin-probe"

SAMPLES = {
    "apiKey": "key-SECRET-123456", "subdomain": "acme", "domain": "acme", "email": "agent@example.com",
    "siteUrl": "https://blog.example.com", "storeUrl": "https://shop.example.com", "baseUrl": "https://erp.example.com",
    "host": "https://dbc-1.cloud.databricks.com", "shopDomain": "acme.myshopify.com", "customerId": "cust-42",
    "apiSecret": "apisecret-SECRET-9", "username": "editor", "privateKey": "private-SECRET-77",
    "applicationKey": "appkey-SECRET-55", "site": "eu", "orgSlug": "my-org", "consumerKey": "ck_live_1234",
    "consumerSecret": "cs-SECRET-8888", "userAccessToken": "usertoken-SECRET-1", "appBearerToken": "apptoken-SECRET-2",
    "subject": "admin@example.com",
}
OVERRIDES = {
    "mailchimp": {"apiKey": "0123456789abcdef-us21"},
    "docker_hub": {"apiKey": "navinbot:dckr_pat_SECRET123"},
    "grafana": {"baseUrl": "https://acme.grafana.net"},
    "sendgrid": {"baseUrl": "https://api.sendgrid.com"},
}


class FakeVault:
    def __init__(self, ready: bool = True) -> None:
        self._ready = ready
        self.data: dict[str, dict[str, Any]] = {}

    def ready(self) -> bool:
        return self._ready

    def get(self, account: str) -> dict[str, Any]:
        return dict(self.data.get(account) or {})

    def put(self, account: str, tokens: dict[str, Any]) -> None:
        self.data[account] = dict(tokens)

    def delete(self, account: str) -> None:
        self.data.pop(account, None)


class Recorder:
    """httpx.MockTransport handler: records requests, answers like a happy provider."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status_for: dict[str, int] = {}
        self.json_for: dict[str, Any] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        host = request.url.host
        for needle, status in self.status_for.items():
            if needle in url:
                return httpx.Response(status, json=self.json_for.get(needle, {"error": "refused"}))
        for needle, payload in self.json_for.items():
            if needle in url:
                return httpx.Response(200, json=payload)
        if host == "hub.docker.com" and request.url.path == "/v2/auth/token":
            return httpx.Response(200, json={"access_token": "docker-bearer-SECRET"})
        if host == "oauth2.googleapis.com" and request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "ya29.service-SECRET", "expires_in": 3600})
        if host == "oauth2.googleapis.com" and request.url.path == "/tokeninfo":
            return httpx.Response(200, json={"email": "me@example.com", "sub": "42"})
        if host == "cloud.mongodb.com" and "authorization" not in request.headers:
            return httpx.Response(401, headers={"WWW-Authenticate": 'Digest realm="atlas", nonce="abc123", qop="auth"'})
        if request.url.path.endswith(PROBE):
            # An app that echoes what it got: secrets must come back masked.
            seen = {
                name: value for name, value in request.headers.items()
                if not (name.lower() == "authorization" and value.startswith(("Basic ", "Digest ")))
            }
            return httpx.Response(200, json={"probe": True, "headers": seen, "query": request.url.query.decode()})
        return httpx.Response(200, json={
            "ok": True, "code": 0, "success": True, "is_logged_in": True, "valid": True,
            "id": "acct-1", "name": "Agent Smith", "user": {"id": 7, "name": "Agent Smith"},
        })


@pytest.fixture()
def http(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorder = Recorder()
    monkeypatch.setattr(runtime, "transport", httpx.MockTransport(recorder))
    runtime.clear_token_cache()
    return recorder


@pytest.fixture()
def store(tmp_path: Path) -> ConnectorStore:
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    return ConnectorStore(tmp_path / "connectors", vault=FakeVault(ready=False), config_path=config)


@pytest.fixture(autouse=True)
def _gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oauth, "suggested_redirect_uri", lambda: "http://127.0.0.1:8765" + oauth.CALLBACK_PATH)


def _use_store(monkeypatch: pytest.MonkeyPatch, store: ConnectorStore) -> None:
    """Every ``ConnectorStore()`` the code builds returns the test store."""
    from navin.connectors import store as store_module
    from navin.webui import connectors_api

    for module in (store_module, runtime, manager, connectors_api):
        monkeypatch.setattr(module, "ConnectorStore", lambda *a, **k: store)


def _values(service: str, item: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in item["fields"]:
        key = field["key"]
        if key == "serviceAccountJson":
            values[key] = _service_account_json()
        elif key == "apiBaseUrl":
            continue
        elif key in SAMPLES:
            values[key] = SAMPLES[key]
        elif field.get("required"):
            values[key] = f"{key}-SECRET-0"
    values.update(OVERRIDES.get(service, {}))
    return values


_SA_CACHE: dict[str, Any] = {}


def _service_account_json() -> str:
    if "json" not in _SA_CACHE:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        _SA_CACHE["key"] = key
        _SA_CACHE["json"] = json.dumps({
            "type": "service_account", "client_email": "bot@proj.iam.gserviceaccount.com",
            "private_key": pem, "private_key_id": "kid1", "token_uri": "https://oauth2.googleapis.com/token",
        })
    return _SA_CACHE["json"]


# -- catalog -----------------------------------------------------------------------


def test_catalog_has_every_requested_connector_in_order() -> None:
    assert [row["id"] for row in catalog.categories()] == [cat for cat, _ in REQUESTED]
    got = [(row["category"], row["service"], row["auth"]) for row in catalog.providers()]
    assert got == ALL
    assert len(got) == 79
    assert sum(len(row["actions"]) for row in catalog.providers()) == 1444


def test_catalog_names_match_the_requested_table() -> None:
    assert {row["service"]: row["name"] for row in catalog.providers()} == NAMES


def test_catalog_keeps_nothing_of_the_hosted_service() -> None:
    import re

    rows = json.dumps(catalog.providers(), ensure_ascii=False)
    assert not re.search(r"\bMCP\b|oomol|OpenConnector|connector\.oomol", rows, re.I)


def test_hubspot_actions_name_their_rest_endpoints() -> None:
    actions = catalog.provider("hubspot")["actions"]
    assert len(actions) == 20 and all(item.get("endpoint") for item in actions)
    by_name = {item["name"]: item for item in actions}
    assert by_name["create_contact"]["endpoint"] == "POST /crm/v3/objects/contacts"
    assert by_name["search_crm_objects"]["endpoint"] == "POST /crm/v3/objects/{objectType}/search"
    assert by_name["archive_deal"]["op"] == "destructive"
    # Every HubSpot REST path stays within the scopes the OAuth app asks for.
    scopes = set(catalog.method("hubspot", "oauth2")["scopes"])
    assert {"crm.objects.contacts.write", "crm.objects.companies.write", "crm.objects.deals.write"} <= scopes


@pytest.mark.parametrize(("category", "service", "auth"), ALL)
def test_each_connector_has_icon_methods_and_matching_auth(category: str, service: str, auth: str) -> None:
    row = catalog.provider(service)
    assert row["icon"] and (ICONS / row["icon"]).is_file()
    types = [item["type"] for item in row["methods"]]
    assert ("oauth2" in types) == (auth in {"oauth", "both"})
    if auth in {"apikey", "both"}:
        assert any(kind != "oauth2" for kind in types)
    assert row["actions"], service
    assert service in providers.SPECS


# Darkness (255 - luminance) under which a color does not show on the white icon tile.
_LIGHT = 40
_COLOR = re.compile(r"(?:fill|stroke|stop-color)\s*[=:]\s*[\"']?([^\"';>)]+\)?)")


def _darkness(color: str) -> float | None:
    """255 - luminance of a CSS color; None for url(), none, currentColor and other names."""
    value = {"white": "#ffffff", "black": "#000000"}.get(color.strip().lower(), color.strip().lower())
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        value = "#" + "".join(ch * 2 for ch in value[1:])
    match = re.fullmatch(r"#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})(?:[0-9a-f]{2})?", value)
    if not match:
        match = re.fullmatch(r"rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+).*\)", value)
    if not match:
        return None
    red, green, blue = (int(part, 16) if value.startswith("#") else int(part) for part in match.groups())
    return round(255 - (0.2126 * red + 0.7152 * green + 0.0722 * blue), 1)


@pytest.mark.parametrize("service", [service for _, service, _ in ALL])
def test_each_icon_is_a_valid_image_that_shows_on_the_white_tile(service: str) -> None:
    import xml.etree.ElementTree as ET

    path = ICONS / catalog.provider(service)["icon"]
    if path.suffix == ".svg":
        # Bundled assets of this repository, not untrusted input.
        root = ET.parse(path).getroot()
        assert root.tag.rsplit("}", 1)[-1] == "svg", service
        assert root.get("viewBox") or (root.get("width") and root.get("height")), service
        shades = [d for d in (_darkness(c) for c in _COLOR.findall(path.read_text(encoding="utf-8"))) if d is not None]
        # No explicit color means black; otherwise one color must stand out from white.
        assert not shades or max(shades) >= _LIGHT, f"{service}: every color is too light for the white tile"
        return
    from PIL import Image

    with Image.open(path) as image:
        assert min(image.size) >= 32, f"{service}: {image.size} is too small"
        tile = Image.new("RGBA", image.size, "white")
        tile.alpha_composite(image.convert("RGBA"))
        # Pixels at least _LIGHT darker than white, counted from the grey histogram.
        ink = sum(tile.convert("L").histogram()[: 256 - _LIGHT])
    assert ink / (image.size[0] * image.size[1]) > 0.01, f"{service}: the logo does not show on white"


def test_light_logos_are_caught() -> None:
    assert _darkness("#F8FF2C") < _LIGHT and _darkness("white") == 0
    assert _darkness("#141414") > _LIGHT and _darkness("rgb(99, 91, 255)") > _LIGHT
    assert _darkness("url(#gradient)") is None and _darkness("none") is None


def test_catalog_text_has_no_long_dashes() -> None:
    raw = catalog.DATA_PATH.read_text(encoding="utf-8")
    assert "\u2014" not in raw and "\u2013" not in raw


def test_search_paginates_and_counts_categories() -> None:
    first = catalog.search(page=1, page_size=12)
    assert (first["total"], first["pages"], len(first["items"])) == (79, 7, 12)
    last = catalog.search(page=99, page_size=12)
    assert last["page"] == 7 and len(last["items"]) == 79 - 72
    assert sum(row["count"] for row in first["categories"]) == 79
    crm = catalog.search(category="crm")
    assert [item["service"] for item in crm["items"]] == ["salesforce", "hubspot", "zendesk"]
    google = catalog.search(query="google", page_size=50)
    assert {"gmail", "googledrive", "google_maps", "googleads"} <= {item["service"] for item in google["items"]}
    oauth_only = catalog.search(auth="oauth", page_size=100)
    assert oauth_only["total"] == 35
    connected = catalog.search(status="connected", statuses={"hunter": {"connected": True}})
    assert [item["service"] for item in connected["items"]] == ["hunter"]
    assert connected["connected_total"] == 1
    with pytest.raises(ConnectorError):
        catalog.search(category="nope")


def test_resolve_service_accepts_names_and_aliases() -> None:
    assert catalog.resolve_service("Google Calendar") == "googlecalendar"
    assert catalog.resolve_service("X (Twitter)") == "twitter"
    assert catalog.resolve_service("TikTok") == "tiktok_business"
    assert catalog.resolve_service("MongoDB Atlas") == "mongo_db_atlas_administration"
    assert catalog.resolve_service("OneDrive") == "one_drive"
    assert catalog.resolve_service("nothing-like-this") == ""


def test_actions_are_searchable_and_paginated() -> None:
    page = catalog.actions("slack", page=1, page_size=5)
    assert page["total"] == 28 and len(page["items"]) == 5 and page["pages"] == 6
    found = catalog.actions("slack", query="message")
    assert found["total"] >= 1
    assert all("message" in catalog.norm(item["name"] + item["description"]) for item in found["items"])


# -- request rules ------------------------------------------------------------------


def _auth_seen(request: httpx.Request, rule: providers.AuthRule, values: dict[str, Any]) -> bool:
    kind, name, template = rule
    if kind == "header":
        return request.headers.get(name) == providers.render(template, values)
    if kind == "query":
        return parse_qs(request.url.query.decode()).get(name, [""])[0] == providers.render(template, values)
    if kind == "basic":
        pair = f"{providers.render(name, values)}:{providers.render(template, values)}"
        return request.headers.get("authorization") == "Basic " + base64.b64encode(pair.encode()).decode()
    if kind == "digest":
        return request.headers.get("authorization", "").startswith("Digest ") and \
            f'username="{providers.render(name, values)}"' in request.headers["authorization"]
    return False


KEY_METHODS = [
    (row["service"], item["type"]) for row in catalog.providers() for item in row["methods"] if item["type"] != "oauth2"
]


@pytest.mark.parametrize(("service", "kind"), KEY_METHODS)
def test_key_methods_send_the_credential_where_the_app_expects_it(http: Recorder, service: str, kind: str) -> None:
    item = catalog.method(service, kind)
    values = _values(service, item)
    result = runtime.verify_credential(service, kind, values)
    spec = providers.spec_for(service)
    if spec.exchange:
        exchange = next(r for r in http.requests if r.url.path == "/v2/auth/token")
        assert json.loads(exchange.content) == {"identifier": "navinbot", "secret": "dckr_pat_SECRET123"}
        assert result["profile"] == {"id": "navinbot", "name": "navinbot"}
        return
    if spec.verify_for(kind, result["values"]) is None:
        tokeninfo = [r for r in http.requests if r.url.path == "/tokeninfo"]
        assert tokeninfo and parse_qs(tokeninfo[-1].url.query.decode())["access_token"] == ["ya29.service-SECRET"]
        return
    sent = [r for r in http.requests if r.url.host not in {"oauth2.googleapis.com"}]
    assert sent, f"{service} made no verification call"
    last = sent[-1]
    assert last.url.scheme == "https"
    prepared = result["values"]
    expected_host = urlsplit(providers.render(spec.base, prepared)).hostname
    assert providers.host_allowed(last.url.host, {expected_host, *spec.hosts})
    if kind == "service_account":
        assert last.headers["authorization"] == "Bearer ya29.service-SECRET"
        return
    template_values = {**prepared}
    rules = [rule for rule in spec.key_auth if providers.complete(rule[2], template_values) and providers.complete(rule[1], template_values)]
    assert rules, f"{service} has no usable auth rule"
    for rule in rules:
        assert _auth_seen(last, rule, template_values), f"{service}: {rule} not applied to {last.url}"
    for name, template in spec.headers.items():
        if providers.complete(template, template_values):
            assert last.headers.get(name) == providers.render(template, template_values)


OAUTH_SERVICES = [row["service"] for row in catalog.providers() if catalog.has_oauth(row)]


@pytest.mark.parametrize("service", OAUTH_SERVICES)
def test_oauth_tokens_are_sent_as_the_app_expects(http: Recorder, service: str) -> None:
    item = catalog.method(service, "oauth2")
    values = providers.prepare(service, {f["key"]: SAMPLES.get(f["key"], f.get("defaultValue") or "x-SECRET-1")
                                         for f in item["fields"]})
    metadata = {"instance_url": "https://acme.my.salesforce.com"} if service == "salesforce" else {}
    runtime.verify_credential(service, "oauth2", values, access_token="tok-SECRET-abc", metadata=metadata)
    spec = providers.spec_for(service)
    calls = [r for r in http.requests if r.url.host != "oauth2.googleapis.com"]
    if spec.verify is None:
        assert spec.google and any(r.url.path == "/tokeninfo" for r in http.requests)
        return
    last = calls[-1]
    rule = spec.oauth_auth[0]
    assert last.headers.get(rule[1]) == providers.render(rule[2], {"accessToken": "tok-SECRET-abc"})


def test_google_service_account_jwt_is_signed_rs256(http: Recorder) -> None:
    item = catalog.method("gmail", "service_account")
    runtime.verify_credential("gmail", "service_account", _values("gmail", item))
    token_call = next(r for r in http.requests if r.url.path == "/token")
    form = parse_qs(token_call.content.decode())
    assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    header_b64, claims_b64, signature_b64 = form["assertion"][0].split(".")

    def decode(part: str) -> bytes:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))

    claims = json.loads(decode(claims_b64))
    assert claims["sub"] == "admin@example.com" and "gmail.readonly" in claims["scope"]
    assert "openid" not in claims["scope"].split()
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    _SA_CACHE["key"].public_key().verify(decode(signature_b64), f"{header_b64}.{claims_b64}".encode(),
                                         padding.PKCS1v15(), hashes.SHA256())


def test_verify_reports_the_provider_refusal(http: Recorder) -> None:
    http.status_for["api.hunter.io"] = 401
    http.json_for["api.hunter.io"] = {"errors": [{"id": "authentication_failed", "code": 401,
                                                  "details": "No user found for the API key supplied"}]}
    with pytest.raises(ConnectorError) as err:
        runtime.verify_credential("hunter", "api_key", {"apiKey": "bad-key-000000"})
    assert err.value.status == 400 and "HTTP 401" in err.value.message and "No user found" in err.value.message
    assert "bad-key-000000" not in err.value.message


def test_slack_ok_false_is_a_refusal(http: Recorder) -> None:
    http.json_for["slack.com/api/auth.test"] = {"ok": False, "error": "invalid_auth"}
    with pytest.raises(ConnectorError, match="invalid_auth"):
        runtime.verify_credential("slack", "api_key", {"apiKey": "xoxb-bad"})


def test_prepare_normalizes_hosts_and_rejects_bad_ones() -> None:
    assert providers.prepare("wordpress", {"siteUrl": "blog.example.com/wp-json/wp/v2/", "apiKey": "k", "username": "u"})["siteUrl"] == "https://blog.example.com"
    assert providers.prepare("shopify", {"shopDomain": "https://admin.shopify.com/store/acme"})["shop"] == "acme.myshopify.com"
    assert providers.prepare("zendesk", {"subdomain": "acme.zendesk.com"})["subdomain"] == "acme"
    assert providers.prepare("datadog", {"site": "datadoghq.eu"})["apiBase"] == "https://api.datadoghq.eu"
    assert providers.prepare("mailchimp", {"apiKey": "abc-us7"})["dc"] == "us7"
    for service, values in [("wordpress", {"siteUrl": "http://public.example.com"}), ("mailchimp", {"apiKey": "nodc"}),
                            ("docker_hub", {"apiKey": "missing-colon"}), ("datadog", {"site": "mars"})]:
        with pytest.raises(ConnectorError):
            providers.prepare(service, values)
    assert providers.prepare("erpnext", {"baseUrl": "http://192.168.1.20:8000"})["baseUrl"] == "http://192.168.1.20:8000"


# -- vault ---------------------------------------------------------------------------


@pytest.mark.parametrize("keyring", [False, True])
def test_connect_stores_secrets_outside_the_index(http: Recorder, tmp_path: Path, keyring: bool) -> None:
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    vault = FakeVault(ready=keyring)
    store = ConnectorStore(tmp_path / "c", vault=vault, config_path=config)
    out = manager.connect(store, "zendesk", "api_key", {"apiKey": "zd-SECRET-token", "subdomain": "Acme", "email": "a@b.co"})
    assert out["status"]["connected"] and out["status"]["account"] == "Agent Smith"
    index = (tmp_path / "c" / "index.json").read_text()
    assert "zd-SECRET-token" not in index and '"subdomain": "acme"' in index
    if keyring:
        assert vault.data["conn:zendesk"]["values"]["apiKey"] == "zd-SECRET-token"
        assert not (tmp_path / "c" / "secrets.enc").exists()
    else:
        assert b"zd-SECRET-token" not in (tmp_path / "c" / "secrets.enc").read_bytes()
        assert store.secrets("conn:zendesk")["values"]["apiKey"] == "zd-SECRET-token"
    assert store.statuses()["zendesk"]["fields"] == {"subdomain": "acme", "email": "a@b.co"}
    manager.set_enabled(store, "zendesk", False)
    assert manager.connected_summary(store) == []
    manager.disconnect(store, "zendesk")
    assert store.status("zendesk") == {"connected": False}
    assert store.secrets("conn:zendesk") == {}


def test_connect_requires_fields_and_refuses_oauth(store: ConnectorStore) -> None:
    with pytest.raises(ConnectorError, match="required"):
        manager.connect(store, "zendesk", "api_key", {"apiKey": "x"})
    with pytest.raises(ConnectorError, match="OAuth"):
        manager.connect(store, "gmail", "oauth2", {})


# -- requests ------------------------------------------------------------------------


def _connect_hunter(store: ConnectorStore) -> None:
    manager.connect(store, "hunter", "api_key", {"apiKey": "hunter-SECRET-key"})


def test_request_injects_auth_masks_secrets_and_stays_on_the_app_host(http: Recorder, store: ConnectorStore) -> None:
    _connect_hunter(store)
    http.json_for["/v2/domain-search"] = {"data": {"echo": "hunter-SECRET-key", "emails": []}}
    result = runtime.request("hunter", "GET", "/domain-search", params={"domain": "stripe.com", "limit": 5}, store=store)
    sent = http.requests[-1]
    assert sent.headers["x-api-key"] == "hunter-SECRET-key"
    assert parse_qs(sent.url.query.decode()) == {"domain": ["stripe.com"], "limit": ["5"]}
    assert result["ok"] and result["data"]["data"]["echo"] == "[redacted]"
    with pytest.raises(ConnectorError, match="only sent to"):
        runtime.request("hunter", "GET", "https://evil.example.com/steal", store=store)
    result = runtime.request("hunter", "GET", "/domain-search", headers={"Authorization": "Bearer x", "X-Trace": "1"}, store=store)
    assert http.requests[-1].headers["x-api-key"] == "hunter-SECRET-key"
    assert http.requests[-1].headers.get("authorization") is None and http.requests[-1].headers["x-trace"] == "1"


def test_request_needs_a_connected_and_enabled_app(store: ConnectorStore, http: Recorder) -> None:
    with pytest.raises(ConnectorError) as err:
        runtime.request("stripe", "GET", "/v1/balance", store=store)
    assert err.value.status == 409 and "Settings > Channels > Connectors" in err.value.message
    _connect_hunter(store)
    store.set_enabled("hunter", False)
    with pytest.raises(ConnectorError, match="turned off"):
        runtime.request("hunter", "GET", "/account", store=store)


def test_request_saves_binary_bodies(http: Recorder, store: ConnectorStore, tmp_path: Path) -> None:
    _connect_hunter(store)
    http.json_for.clear()
    target = tmp_path / "out" / "file.bin"

    def binary(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x89PNG\x00\x01", headers={"content-type": "image/png"})

    with patch.object(runtime, "transport", httpx.MockTransport(binary)):
        shown = runtime.request("hunter", "GET", "/x", store=store)
        saved = runtime.request("hunter", "GET", "/x", store=store, save_to=target)
    assert shown["bytes"] == 6 and "data" not in shown
    assert saved["saved_to"] == str(target) and target.read_bytes() == b"\x89PNG\x00\x01"


# -- OAuth ---------------------------------------------------------------------------


@pytest.mark.parametrize("service", OAUTH_SERVICES)
def test_oauth_start_builds_a_valid_authorization_url(store: ConnectorStore, service: str) -> None:
    item = catalog.method(service, "oauth2")
    with pytest.raises(ConnectorError) as err:
        oauth.start(store, service, {})
    assert err.value.status == 409
    oauth.save_app(store, service, client_id="cid-123", client_secret="csecret-SECRET")
    fields = {f["key"]: SAMPLES.get(f["key"], "") or f.get("defaultValue") or "acme" for f in item["fields"]}
    started = oauth.start(store, service, fields)
    url = urlsplit(started["authorization_url"])
    query = parse_qs(url.query)
    expected = urlsplit(providers.render(item["authorizationUrl"], providers.prepare(service, {
        k: v for k, v in fields.items() if not any(f["key"] == k and f.get("secret") for f in item["fields"])})))
    assert url.scheme == "https" and url.hostname == expected.hostname and "{" not in started["authorization_url"]
    names = {**oauth._AUTH_NAMES, **(item.get("authorizationRequestFields") or {})}
    assert query[names["clientId"]] == ["cid-123"]
    assert query[names["redirectUri"]] == ["http://127.0.0.1:8765/api/connectors/oauth/callback"]
    assert len(query["state"][0]) >= 40
    if names.get("scope") and item.get("scopes"):
        assert query[names["scope"]][0] == (item.get("scopeSeparator") or " ").join(item["scopes"])
    assert ("code_challenge" in query) == bool(item.get("pkce"))
    for key, value in (item.get("authorizationParams") or {}).items():
        assert query[key] == [value]
    assert "csecret-SECRET" not in started["authorization_url"]


def _token_handler(recorder: Recorder, token_payload: dict[str, Any]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and ("token" in request.url.path or "access_token" in request.url.path):
            recorder.requests.append(request)
            return httpx.Response(200, json=token_payload)
        return recorder(request)
    return handler


def test_oauth_finish_exchanges_code_verifies_and_stores_tokens(store: ConnectorStore, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(runtime, "transport", httpx.MockTransport(_token_handler(recorder, {
        "access_token": "at-SECRET-1", "refresh_token": "rt-SECRET-1", "expires_in": 3600, "scope": "a b",
    })))
    oauth.save_app(store, "airtable", client_id="cid", client_secret="cs-SECRET")
    started = oauth.start(store, "airtable", {})
    state = parse_qs(urlsplit(started["authorization_url"]).query)["state"][0]
    result = oauth.finish(store, {"state": state, "code": "code-xyz"})
    assert result == {"service": "airtable", "name": "Airtable", "account": "acct-1", "session_key": ""}
    token_call = next(r for r in recorder.requests if r.url.path.endswith("/token"))
    assert token_call.headers["authorization"] == "Basic " + base64.b64encode(b"cid:cs-SECRET").decode()
    form = parse_qs(token_call.content.decode())
    assert form["grant_type"] == ["authorization_code"] and form["code"] == ["code-xyz"] and form["code_verifier"]
    status = store.status("airtable")
    assert status["connected"] and status["method"] == "oauth2" and status["scopes"] == ["a", "b"]
    assert "at-SECRET-1" not in (store.root / "index.json").read_text()
    with pytest.raises(ConnectorError, match="expired or was already used"):
        oauth.finish(store, {"state": state, "code": "code-xyz"})


def test_tiktok_token_exchange_uses_json_renamed_fields_and_envelope(store: ConnectorStore, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(runtime, "transport", httpx.MockTransport(_token_handler(recorder, {
        "code": 0, "message": "OK", "data": {"access_token": "tt-SECRET", "scope": [1, 2]},
    })))
    oauth.save_app(store, "tiktok_business", client_id="app1", client_secret="sec-SECRET")
    started = oauth.start(store, "tiktok_business", {})
    query = parse_qs(urlsplit(started["authorization_url"]).query)
    assert query["app_id"] == ["app1"] and "response_type" not in query and "scope" not in query
    oauth.finish(store, {"state": query["state"][0], "auth_code": "ac-1"})
    body = json.loads(next(r for r in recorder.requests if "access_token" in r.url.path).content)
    assert body == {"auth_code": "ac-1", "redirect_uri": started["redirect_uri"], "app_id": "app1", "secret": "sec-SECRET"}
    assert recorder.requests[-1].headers["access-token"] == "tt-SECRET"


def test_oauth_error_and_bad_state_are_reported(store: ConnectorStore) -> None:
    with pytest.raises(ConnectorError, match="valid state"):
        oauth.finish(store, {"state": "x"})
    oauth.save_app(store, "zoom", client_id="cid", client_secret="cs")
    state = parse_qs(urlsplit(oauth.start(store, "zoom", {})["authorization_url"]).query)["state"][0]
    with pytest.raises(ConnectorError, match="access_denied"):
        oauth.finish(store, {"state": state, "error": "access_denied"})


def test_expired_token_is_refreshed_before_the_call(store: ConnectorStore, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(runtime, "transport", httpx.MockTransport(_token_handler(recorder, {
        "access_token": "fresh-SECRET", "expires_in": 3600,
    })))
    oauth.save_app(store, "zoom", client_id="cid", client_secret="cs")
    store.save_connection("zoom", method="oauth2", fields={}, secret_values={}, profile={"id": "1", "name": "me"},
                          access_token="old-SECRET", refresh_token="rt-SECRET", expires_at=int(time.time()) - 5)
    result = runtime.request("zoom", "GET", "/users/me", store=store)
    assert result["ok"]
    refresh_call = next(r for r in recorder.requests if r.url.path == "/oauth/token")
    assert parse_qs(refresh_call.content.decode()) == {"grant_type": ["refresh_token"], "refresh_token": ["rt-SECRET"], "client_id": ["cid"]}
    assert recorder.requests[-1].headers["authorization"] == "Bearer fresh-SECRET"
    assert store.secrets("conn:zoom")["access_token"] == "fresh-SECRET"


def test_oauth_app_is_shared_by_the_family(store: ConnectorStore) -> None:
    status = oauth.save_app(store, "gmail", client_id="google-cid", client_secret="g-secret")
    assert status["configured"] and status["family"] == "google"
    assert oauth.app_status(store, "youtube")["client_id"] == "google-cid"
    assert set(status["services"]) >= {"gmail", "googledrive", "youtube", "googleads"}
    assert "g-secret" not in (store.root / "index.json").read_text()
    with pytest.raises(ConnectorError, match="redirect URI"):
        oauth.save_app(store, "gmail", client_id="x", client_secret="y", redirect_uri="http://example.com/api/connectors/oauth/callback")


# -- HTTP API --------------------------------------------------------------------------


def test_api_actions(store: ConnectorStore, http: Recorder) -> None:
    from navin.webui.connectors_api import handle_connectors_action, oauth_callback_page

    page = handle_connectors_action("catalog", {"page": "2", "page_size": "10", "category": ""}, {}, store=store)
    assert page["page"] == 2 and len(page["items"]) == 10
    detail = handle_connectors_action("detail", {"service": "notion"}, {}, store=store)
    assert [m["type"] for m in detail["methods"]] == ["oauth2", "api_key"] and detail["oauth_app"]["configured"] is False
    assert detail["actions_page"]["total"] == 31
    out = handle_connectors_action("connect", {}, {"service": "tavily", "method": "api_key", "values": {"apiKey": "tvly-SECRET-1"}}, store=store)
    assert out["status"]["connected"]
    assert handle_connectors_action("status", {"service": "tavily"}, {}, store=store)["status"]["connected"]
    assert handle_connectors_action("test", {}, {"service": "tavily"}, store=store)["ok"]
    assert handle_connectors_action("toggle", {}, {"service": "tavily", "enabled": False}, store=store)["status"]["enabled"] is False
    assert handle_connectors_action("disconnect", {}, {"service": "tavily"}, store=store)["status"] == {"connected": False}
    saved = handle_connectors_action("oauth_app", {}, {"service": "slack", "client_id": "s1", "client_secret": "s2"}, store=store)
    assert saved["configured"] and saved["services"] == ["slack", "slackbot"]
    started = handle_connectors_action("oauth_start", {}, {"service": "slackbot", "fields": {}}, store=store)
    assert started["authorization_url"].startswith("https://slack.com/oauth/v2/authorize?")
    with pytest.raises(ConnectorError):
        handle_connectors_action("explode", {}, {}, store=store)
    status, html = oauth_callback_page({"state": "short"}, store=store)
    assert status == 400 and "did not finish" in html and "\u2014" not in html


# -- agent tool and journal card ---------------------------------------------------------


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_agent_tool_lists_describes_and_calls(store: ConnectorStore, http: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    from navin.agent.tools.connectors import ConnectorsTool
    _use_store(monkeypatch, store)
    tool = ConnectorsTool(workspace=str(store.root))
    listed = _run(tool.execute(action="list"))
    assert "Connected (0)" in listed and "hubspot | HubSpot | crm" in listed
    described = _run(tool.execute(action="describe", service="Slack", query="message"))
    assert "API base: https://slack.com/api" in described and "never add Authorization" in described
    _connect_hunter(store)
    answer = _run(tool.execute(action="request", service="hunter", path="/account"))
    assert not answer.startswith("Error") and json.loads(answer)["ok"] is True
    missing = _run(tool.execute(action="request", service="stripe", path="/v1/balance"))
    assert missing.startswith("Error") and "Settings > Channels > Connectors" in missing
    unattended = _run(tool.execute(action="connect", service="stripe"))
    assert "nobody can fill a connection card" in unattended
    assert tool.call_read_only({"action": "request", "method": "GET"}) and not tool.call_read_only({"action": "request", "method": "POST"})
    from navin.agent.tools.context import RequestContext

    turn = RequestContext(channel="websocket", chat_id="c1", session_key="websocket:c1", metadata={})
    block = _run(tool._provide_runtime_context(turn))
    assert block is not None and "hunter (Hunter" in block.content


def test_connector_card_round_trip_through_the_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    from navin.agent import credentials as cred
    from navin.agent.credentials import (
        CredentialBroker,
        CredentialRequest,
        bind_credential_broker,
        request_credentials,
    )
    from navin.agent.tools.context import (
        RequestContext,
        bind_request_context,
        reset_request_context,
    )

    published: list[tuple[str, CredentialRequest]] = []
    state = {"connected": False}
    monkeypatch.setattr(cred, "_connector_status", lambda service: (state["connected"], "Aymen"))

    async def scenario() -> Any:
        async def publish(request_id: str, request: CredentialRequest, route: dict[str, Any]) -> None:
            published.append((request_id, request))

        broker = CredentialBroker(publish=publish)
        token = bind_request_context(RequestContext(channel="websocket", chat_id="c1", session_key="websocket:c1", metadata={}))
        bind_credential_broker(broker)
        try:
            async def answer() -> None:
                while not published:
                    await asyncio.sleep(0.001)
                state["connected"] = True
                broker.resolve(published[-1][0], status="connected", login="ignored")

            got, _ = await asyncio.gather(
                request_credentials(CredentialRequest("connector", cred.normalize_host("connector", "HubSpot"), (), "Sync the CRM")),
                answer(),
            )
            return got
        finally:
            reset_request_context(token)

    answer = _run(scenario())
    assert published[0][1].kind == "connector" and published[0][1].host == "hubspot"
    assert answer.status == "connected" and answer.login == "Aymen"
    assert "connectors action=request service=hubspot" in answer.as_text()


def test_naming_a_connected_app_brand_loads_the_tool() -> None:
    from navin.agent.tool_demand import on_demand_tools_for_text

    assert "connectors" in on_demand_tools_for_text("Envoie un message Slack a l equipe")
    assert "connectors" in on_demand_tools_for_text("liste mes deals HubSpot")
    assert "connectors" not in on_demand_tools_for_text("explique la notion de closure en python")


# -- agent calls: every method, every body format -------------------------------------


def _secret_values(item: dict[str, Any], values: dict[str, str]) -> list[str]:
    keys = {field["key"] for field in item["fields"] if field.get("secret")}
    return [value for key, value in values.items() if key in keys and len(value) >= 6 and not value.startswith("{")]


@pytest.mark.parametrize(("service", "kind"), KEY_METHODS)
def test_agent_requests_carry_the_credential_like_the_verification(
    http: Recorder, store: ConnectorStore, service: str, kind: str
) -> None:
    item = catalog.method(service, kind)
    values = _values(service, item)
    manager.connect(store, service, kind, values)
    result = runtime.request(service, "GET", PROBE, store=store)
    assert result["ok"] and result["data"]["probe"], service
    sent = http.requests[-1]
    spec = providers.spec_for(service)
    cred = runtime.load_credential(service, store)
    template = cred.template_values()
    if kind == "service_account" or spec.exchange:
        assert sent.headers["authorization"] == f"Bearer {cred.access_token}"
    else:
        rules = [rule for rule in spec.key_auth if providers.complete(rule[2], template) and providers.complete(rule[1], template)]
        assert rules
        for rule in rules:
            assert _auth_seen(sent, rule, template), f"{service}: {rule} not applied to {sent.url}"
    for name, header_template in spec.headers.items():
        if providers.complete(header_template, template):
            assert sent.headers.get(name) == providers.render(header_template, template)
    # The app echoed the request back: no stored secret may reach the agent.
    text = json.dumps(result)
    for secret in [*_secret_values(item, values), cred.access_token]:
        if secret:
            assert secret not in text, f"{service} leaked a secret"


@pytest.mark.parametrize("service", OAUTH_SERVICES)
def test_agent_requests_carry_the_oauth_token(http: Recorder, store: ConnectorStore, service: str) -> None:
    item = catalog.method(service, "oauth2")
    fields = providers.prepare(service, {
        f["key"]: SAMPLES.get(f["key"], f.get("defaultValue") or "acme") for f in item["fields"] if not f.get("secret")
    })
    metadata = {"instance_url": "https://acme.my.salesforce.com"} if service == "salesforce" else {}
    store.save_connection(service, method="oauth2", fields=fields, secret_values={}, profile={"id": "1", "name": "me"},
                          metadata=metadata, access_token="tok-SECRET-abc")
    result = runtime.request(service, "GET", PROBE, store=store)
    rule = providers.spec_for(service).oauth_auth[0]
    assert http.requests[-1].headers.get(rule[1]) == providers.render(rule[2], {"accessToken": "tok-SECRET-abc"})
    assert result["ok"] and "tok-SECRET-abc" not in json.dumps(result)


def test_describe_shows_the_api_base_of_the_connection(
    http: Recorder, store: ConnectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from navin.agent.tools.connectors import ConnectorsTool
    _use_store(monkeypatch, store)
    tool = ConnectorsTool(workspace=str(store.root))
    before = _run(tool.execute(action="describe", service="mailchimp"))
    assert "API base: https://{dc}.api.mailchimp.com/3.0 ({...} parts come from the connection)" in before
    manager.connect(store, "mailchimp", "api_key", {"apiKey": "0123456789abcdef-us21"})
    after = _run(tool.execute(action="describe", service="mailchimp"))
    assert "API base: https://us21.api.mailchimp.com/3.0\n" in after and "0123456789abcdef-us21" not in after
    manager.connect(store, "datadog", "api_key", {"apiKey": "dd-SECRET-1", "applicationKey": "app-SECRET-2", "site": "eu"})
    assert "API base: https://api.datadoghq.eu\n" in _run(tool.execute(action="describe", service="datadog"))
    store.save_connection("salesforce", method="oauth2", fields={}, secret_values={}, profile={},
                          metadata={"instance_url": "https://acme.my.salesforce.com"}, access_token="tok-SECRET-1")
    assert "API base: https://acme.my.salesforce.com/services/data/v66.0\n" in _run(
        tool.execute(action="describe", service="salesforce"))
    stripe = _run(tool.execute(action="describe", service="stripe"))
    assert "Bodies: form by default (pass nested JSON, Navin form-encodes it)" in stripe and "Notes: " in stripe
    hubspot = _run(tool.execute(action="describe", service="hubspot", query="contact"))
    assert "create_contact [write] POST /crm/v3/objects/contacts" in hubspot


def test_stripe_bodies_are_form_encoded_with_indexed_keys(http: Recorder, store: ConnectorStore) -> None:
    manager.connect(store, "stripe", "api_key", {"apiKey": "sk_test_SECRET123"})
    body = {"email": "a@b.co", "metadata": {"plan": "pro"}, "expand": ["customer"],
            "items": [{"price": "price_1", "quantity": 2}], "livemode": False, "skip": None}
    assert runtime.request("stripe", "POST", "/v1/customers", body=body, store=store)["ok"]
    sent = http.requests[-1]
    assert sent.headers["content-type"] == "application/x-www-form-urlencoded"
    assert parse_qs(sent.content.decode()) == {
        "email": ["a@b.co"], "metadata[plan]": ["pro"], "expand[0]": ["customer"],
        "items[0][price]": ["price_1"], "items[0][quantity]": ["2"], "livemode": ["false"],
    }
    runtime.request("stripe", "POST", "/v1/customers", body={"a": 1}, body_format="json", store=store)
    assert http.requests[-1].headers["content-type"] == "application/json"
    runtime.request("stripe", "GET", "/v1/customers", body={"ignored": True}, params={"limit": 3}, store=store)
    assert http.requests[-1].content == b"" and parse_qs(http.requests[-1].url.query.decode()) == {"limit": ["3"]}


def test_mailgun_form_repeats_lists_and_multipart_attaches_files(
    http: Recorder, store: ConnectorStore, tmp_path: Path
) -> None:
    manager.connect(store, "mailgun", "api_key", {"apiKey": "key-SECRET-123456"})
    runtime.request("mailgun", "POST", "/v3/mg.example.com/messages",
                    body={"from": "a@x.co", "to": ["b@x.co", "c@x.co"], "subject": "Hi", "text": "Hello"}, store=store)
    sent = http.requests[-1]
    assert sent.url.host == "api.mailgun.net"
    assert parse_qs(sent.content.decode())["to"] == ["b@x.co", "c@x.co"]
    report = tmp_path / "report.pdf"
    report.write_bytes(b"%PDF-1.4 navin")
    runtime.request("mailgun", "POST", "/v3/mg.example.com/messages",
                    body={"to": ["b@x.co"], "subject": "Hi"}, files={"attachment": report}, store=store)
    sent = http.requests[-1]
    assert sent.headers["content-type"].startswith("multipart/form-data; boundary=")
    for part in (b'name="attachment"; filename="report.pdf"', b"application/pdf", b"%PDF-1.4 navin", b'name="subject"'):
        assert part in sent.content
    with pytest.raises(ConnectorError, match="needs files"):
        runtime.request("mailgun", "POST", "/v3/x/messages", body={"a": 1}, body_format="multipart", store=store)


def test_raw_uploads_and_streamed_downloads(
    http: Recorder, store: ConnectorStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _connect_hunter(store)
    notes = tmp_path / "notes.txt"
    notes.write_text("hello navin", encoding="utf-8")
    runtime.request("hunter", "PUT", "/upload", body_file=notes, store=store)
    assert http.requests[-1].content == b"hello navin"
    assert http.requests[-1].headers["content-type"].startswith("text/plain")
    runtime.request("hunter", "POST", "/import", body="a,b\n1,2", body_format="raw", content_type="text/csv", store=store)
    assert http.requests[-1].headers["content-type"] == "text/csv" and http.requests[-1].content == b"a,b\n1,2"
    with pytest.raises(ConnectorError, match="Uploads need POST"):
        runtime.request("hunter", "GET", "/x", body_file=notes, store=store)
    with pytest.raises(ConnectorError, match="body_format must be one of"):
        runtime.request("hunter", "POST", "/x", body={}, body_format="xml", store=store)
    with pytest.raises(ConnectorError, match="was not found"):
        runtime.request("hunter", "PUT", "/x", body_file=tmp_path / "missing.txt", store=store)

    payload = b"\x00" * 50_000 + b"END"

    def files(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/missing"):
            return httpx.Response(404, json={"message": "Not found"})
        return httpx.Response(200, content=payload, headers={"content-type": "application/octet-stream"})

    monkeypatch.setattr(runtime, "transport", httpx.MockTransport(files))
    target = tmp_path / "out" / "big.bin"
    saved = runtime.request("hunter", "GET", "/file", save_to=target, store=store)
    assert saved["saved_to"] == str(target) and saved["bytes"] == len(payload)
    assert target.read_bytes() == payload and not target.with_name("big.bin.part").exists()
    missing = tmp_path / "out" / "missing.bin"
    failed = runtime.request("hunter", "GET", "/missing", save_to=missing, store=store)
    assert not failed["ok"] and failed["error"] == "Not found" and not missing.exists()


def test_tool_uploads_stay_inside_the_workspace(
    http: Recorder, store: ConnectorStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from navin.agent.tools.connectors import ConnectorsTool
    _use_store(monkeypatch, store)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "doc.txt").write_text("from the workspace", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    _connect_hunter(store)
    tool = ConnectorsTool(workspace=str(workspace))
    outside = _run(tool.execute(action="request", service="hunter", method="POST", path="/u", body_file="../secret.txt"))
    assert outside.startswith("Error") and "inside the workspace" in outside
    missing = _run(tool.execute(action="request", service="hunter", method="POST", path="/u", files={"file": "nope.txt"}))
    assert missing.startswith("Error") and "is not a file" in missing
    sent = _run(tool.execute(action="request", service="hunter", method="POST", path="/u", body_file="doc.txt"))
    assert json.loads(sent)["ok"] and http.requests[-1].content == b"from the workspace"
    assert not tool.call_read_only({"action": "request", "method": "GET", "files": {"f": "doc.txt"}})


def test_tiktok_refresh_uses_the_refresh_field_names(store: ConnectorStore, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(runtime, "transport", httpx.MockTransport(_token_handler(recorder, {
        "code": 0, "message": "OK", "data": {"access_token": "tt-NEW-SECRET", "refresh_token": "rt-NEW", "expires_in": 86400},
    })))
    oauth.save_app(store, "tiktok_business", client_id="app1", client_secret="sec-SECRET")
    store.save_connection("tiktok_business", method="oauth2", fields={}, secret_values={}, profile={},
                          access_token="old", refresh_token="rt-OLD-SECRET", expires_at=1)
    assert oauth.refresh(store, "tiktok_business") == "tt-NEW-SECRET"
    call = next(r for r in recorder.requests if r.url.path.endswith("/tt_user/oauth2/refresh_token/"))
    assert json.loads(call.content) == {
        "grant_type": "refresh_token", "refresh_token": "rt-OLD-SECRET", "client_id": "app1", "client_secret": "sec-SECRET",
    }


def test_errors_carry_the_status_the_app_answered(http: Recorder) -> None:
    http.status_for["api.hunter.io"] = 403
    with pytest.raises(ConnectorError) as refused:
        runtime.verify_credential("hunter", "api_key", {"apiKey": "bad-key-000000"})
    assert (refused.value.status, refused.value.upstream) == (400, 403)
    http.status_for["api.hunter.io"] = 404
    with pytest.raises(ConnectorError) as wrong:
        runtime.verify_credential("hunter", "api_key", {"apiKey": "bad-key-000000"})
    assert wrong.value.upstream == 404 and "404 Not Found" in wrong.value.message
    http.status_for["api.hunter.io"] = 503
    with pytest.raises(ConnectorError) as down:
        runtime.verify_credential("hunter", "api_key", {"apiKey": "bad-key-000000"})
    assert (down.value.status, down.value.upstream) == (502, 503)


# -- HTTP routes through the gateway handler ------------------------------------------


class _HttpRequest:
    def __init__(self, path: str, body: Any = None, raw: bytes | None = None) -> None:
        self.path = path
        self.headers: dict[str, str] = {}
        payload = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if payload is not None:
            self.headers["x-navin-file-body-0"] = base64.b64encode(payload).decode()


def _gateway_handler(token_ok: bool = True) -> Any:
    from types import SimpleNamespace

    from navin.webui.stall_watch import StallReporter
    from navin.webui.ws_http import GatewayHTTPHandler

    handler = object.__new__(GatewayHTTPHandler)
    handler.check_api_token = lambda request: token_ok
    handler._log = SimpleNamespace(exception=lambda *a, **k: None, warning=lambda *a, **k: None,
                                   debug=lambda *a, **k: None, info=lambda *a, **k: None)
    handler._stalls = StallReporter(handler._log)
    handler.config = SimpleNamespace(token_issue_path="")
    return handler


def test_connectors_http_routes(store: ConnectorStore, http: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_store(monkeypatch, store)
    handler = _gateway_handler()

    def call(path: str, body: Any = None, raw: bytes | None = None, gateway: Any = handler) -> Any:
        return asyncio.run(gateway._dispatch_misc_routes(None, _HttpRequest(path, body, raw), path.split("?")[0]))

    page = call("/api/connectors?action=catalog&page=2&page_size=5")
    assert page.status_code == 200 and json.loads(page.body)["page"] == 2 and len(json.loads(page.body)["items"]) == 5
    connected = call("/api/connectors?action=connect",
                     {"service": "tavily", "method": "api_key", "values": {"apiKey": "tvly-SECRET-9"}})
    assert connected.status_code == 200 and json.loads(connected.body)["status"]["connected"]
    assert b"tvly-SECRET-9" not in connected.body and b"tvly-SECRET-9" not in (store.root / "index.json").read_bytes()
    unknown = call("/api/connectors?action=detail&service=nope")
    assert unknown.status_code == 404 and b"Unknown connector: nope" in unknown.body
    missing = call("/api/connectors?action=connect", {"service": "zendesk", "method": "api_key", "values": {"apiKey": "x"}})
    assert missing.status_code == 400 and b"is required" in missing.body
    assert call("/api/connectors?action=connect", raw=b"[1, 2]").status_code == 400
    assert call("/api/connectors?action=connect", raw=b"{not json").status_code == 400
    assert call("/api/connectors?action=catalog", gateway=_gateway_handler(token_ok=False)).status_code == 401

    callback = asyncio.run(handler._dispatch_resolved(
        None, _HttpRequest("/api/connectors/oauth/callback?state=short"), "/api/connectors/oauth/callback"))
    assert callback.status_code == 400 and callback.headers["Content-Type"].startswith("text/html")
    assert "default-src 'none'" in callback.headers["Content-Security-Policy"]
    assert callback.headers["Cache-Control"] == "no-store" and b"did not finish" in callback.body


# -- error paths that must be handled, not swallowed -----------------------------------


def test_https_url_checks_the_port_and_keeps_ipv6_brackets() -> None:
    assert providers.https_url("http://[::1]:3000/", "URL") == "http://[::1]:3000"
    assert providers.https_url("https://Acme.Grafana.net:8443/x/", "URL") == "https://acme.grafana.net:8443/x"
    for bad in ("https://acme.grafana.net:99999", "https://acme.grafana.net:port"):
        with pytest.raises(ConnectorError, match="must be a URL"):
            providers.https_url(bad, "URL")


def test_service_account_signing_refuses_a_non_rsa_or_unreadable_key() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    with pytest.raises(ConnectorError, match="must be an RSA key"):
        runtime._rs256_jwt({"alg": "RS256"}, {}, pem)
    with pytest.raises(ConnectorError, match="could not be read"):
        runtime._rs256_jwt({"alg": "RS256"}, {}, "not a key")


def test_a_json_labelled_body_that_is_not_json_is_kept_as_text() -> None:
    page = httpx.Response(502, headers={"content-type": "application/json"})
    assert runtime._decode(page, b"<html>Bad gateway</html>") == "<html>Bad gateway</html>"
    binary = httpx.Response(200, headers={"content-type": "application/octet-stream"})
    assert runtime._decode(binary, b"{\x89\x00binary") is None


def test_token_call_network_failures_become_connector_errors(
    store: ConnectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    oauth.save_app(store, "zoom", client_id="cid", client_secret="cs")
    for handler, status, words in ((refused, 502, "could not be reached for the token (ConnectError)"),
                                   (slow, 504, "did not answer the token request in time")):
        monkeypatch.setattr(runtime, "transport", httpx.MockTransport(handler))
        state = parse_qs(urlsplit(oauth.start(store, "zoom", {})["authorization_url"]).query)["state"][0]
        with pytest.raises(ConnectorError) as err:
            oauth.finish(store, {"state": state, "code": "code-1"})
        assert err.value.status == status and words in err.value.message


def test_keyring_failures_fall_back_to_the_file_or_surface(tmp_path: Path) -> None:
    from navin.accounts.store import AccountError

    class LockedVault(FakeVault):
        def get(self, account: str) -> dict[str, Any]:
            raise AccountError("locked", 503)

        def put(self, account: str, tokens: dict[str, Any]) -> None:
            raise AccountError("locked", 503)

        def delete(self, account: str) -> None:
            raise AccountError("locked", 503)

    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    locked = ConnectorStore(tmp_path / "c", vault=LockedVault(ready=True), config_path=config)
    locked.put_secrets("conn:hunter", {"values": {"apiKey": "k-SECRET-1"}})
    assert locked.secrets("conn:hunter") == {"values": {"apiKey": "k-SECRET-1"}}
    assert b"k-SECRET-1" not in (tmp_path / "c" / "secrets.enc").read_bytes()
    with pytest.raises(ConnectorError) as err:
        locked.delete_secrets("conn:hunter")
    assert err.value.status == 503 and "OS keyring" in err.value.message
    # Nothing in the file and an unreadable keyring: the real cause, not an empty secret.
    with pytest.raises(ConnectorError) as unread:
        locked.secrets("conn:never-stored")
    assert unread.value.status == 503 and "could not be read" in unread.value.message


def test_a_secret_the_keyring_refused_is_not_shadowed_by_an_older_one(tmp_path: Path) -> None:
    from navin.accounts.store import AccountError

    class FlakyVault(FakeVault):
        refuse_writes = False

        def put(self, account: str, tokens: dict[str, Any]) -> None:
            if self.refuse_writes:
                raise AccountError("locked", 503)
            super().put(account, tokens)

    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    vault = FlakyVault(ready=True)
    flaky = ConnectorStore(tmp_path / "c", vault=vault, config_path=config)
    flaky.put_secrets("conn:stripe", {"values": {"apiKey": "sk-OLD-1"}})
    vault.refuse_writes = True
    flaky.put_secrets("conn:stripe", {"values": {"apiKey": "sk-NEW-2"}})
    assert flaky.secrets("conn:stripe")["values"]["apiKey"] == "sk-NEW-2"
    vault.refuse_writes = False
    flaky.put_secrets("conn:stripe", {"values": {"apiKey": "sk-NEWEST-3"}})
    assert flaky.secrets("conn:stripe")["values"]["apiKey"] == "sk-NEWEST-3"
    assert "conn:stripe" not in flaky._file_secrets()


def test_store_files_are_private_and_a_damaged_index_is_never_read_as_empty(store: ConnectorStore) -> None:
    import os
    import stat

    from navin.connectors import store as store_module

    store.save_connection("hunter", method="api_key", fields={}, secret_values={"apiKey": "k-SECRET-1"},
                          profile={"id": "1", "name": "me"})
    index = store.root / "index.json"
    if os.name == "posix":
        assert stat.S_IMODE(index.stat().st_mode) == 0o600
        assert stat.S_IMODE((store.root / "secrets.enc").stat().st_mode) == 0o600

    def refuse(src: Any, dst: Any) -> None:
        raise PermissionError("read-only folder")

    # A failed write raises the real error, leaves no temporary file and changes nothing.
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(store_module.os, "replace", refuse)
        with pytest.raises(PermissionError, match="read-only folder"):
            store.set_enabled("hunter", False)
    assert not list(store.root.glob(".navin-conn-*"))
    assert store.status("hunter")["enabled"] is True

    index.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConnectorError) as damaged:
        store.statuses()
    assert damaged.value.status == 503 and "damaged" in damaged.value.message
    with pytest.raises(ConnectorError):
        store.save_connection("tavily", method="api_key", fields={}, secret_values={"apiKey": "t-SECRET-2"}, profile={})
    assert index.read_text(encoding="utf-8") == "{not json"
    assert "conn:tavily" not in store._file_secrets()
    index.write_text('{"connections": []}', encoding="utf-8")
    with pytest.raises(ConnectorError, match="damaged"):
        store.statuses()


def test_marking_or_refreshing_a_removed_connection_writes_nothing(store: ConnectorStore) -> None:
    assert store.mark_error("zoom", "boom") is False
    assert store.update_tokens("zoom", access_token="a-SECRET-1") is False
    assert store.secrets("conn:zoom") == {}
    store.save_connection("zoom", method="oauth2", fields={}, secret_values={}, profile={}, access_token="old-SECRET")
    assert store.mark_error("zoom", "Token refresh failed") is True
    assert store.status("zoom")["error"] == "Token refresh failed"
    assert store.update_tokens("zoom", access_token="new-SECRET", expires_at=99) is True
    assert store.secrets("conn:zoom")["access_token"] == "new-SECRET" and store.status("zoom")["error"] == ""
    store.remove("zoom")
    assert store.update_tokens("zoom", access_token="late-SECRET") is False
    assert store.secrets("conn:zoom") == {}


def test_refresh_of_a_connection_removed_meanwhile_returns_no_token(
    store: ConnectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Recorder()
    monkeypatch.setattr(runtime, "transport", httpx.MockTransport(_token_handler(recorder, {
        "access_token": "fresh-SECRET", "expires_in": 3600,
    })))
    oauth.save_app(store, "zoom", client_id="cid", client_secret="cs")
    store.save_connection("zoom", method="oauth2", fields={}, secret_values={}, profile={},
                          access_token="old", refresh_token="rt-SECRET", expires_at=1)
    real = store.update_tokens

    def removed_meanwhile(service: str, **kwargs: Any) -> bool:
        store.remove(service)
        return real(service, **kwargs)

    monkeypatch.setattr(store, "update_tokens", removed_meanwhile)
    assert oauth.refresh(store, "zoom") == ""
    assert store.secrets("conn:zoom") == {}
