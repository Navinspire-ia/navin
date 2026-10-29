#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Build ``navin/connectors/data/catalog.json`` from an OpenConnector checkout.

Usage::

    python scripts/connectors/generate_catalog.py [--source open-connector]

The provider facts (auth methods and their fields, OAuth endpoints, scopes and
client fields, action names, descriptions and parameters) are read from
OpenConnector's generated catalog (``catalog/apps/<service>.json``, modified
Apache-2.0, Copyright 2026 OOMOL, see ``navin/connectors/NOTICE.md``). The
categories, their order, the Navin-only auth methods and the HubSpot OAuth
endpoints are Navin's. Nothing here runs OpenConnector: the output is plain
data read by :mod:`navin.connectors.catalog`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "navin" / "connectors" / "data" / "catalog.json"
ICONS_DIR = ROOT / "webui" / "src" / "assets" / "connectors"

# (category id, English label, French label, services in display order)
CATEGORIES: list[tuple[str, str, str, list[str]]] = [
    ("communication", "Communication and messaging", "Communication et messagerie",
     ["slack", "slackbot", "microsoft_teams", "discord", "gmail", "outlook"]),
    ("video", "Video conferencing", "Visioconférence", ["googlemeet", "zoom", "agora"]),
    ("calendar", "Calendars and tasks", "Calendriers et tâches",
     ["googlecalendar", "outlook_calendar", "calendly", "googletasks", "motion"]),
    ("documents", "Documents and files", "Documents et fichiers",
     ["notion", "googledrive", "googlesheets", "dropbox", "one_drive", "excel", "airtable"]),
    ("forms", "Forms", "Formulaires", ["googleforms", "typeform"]),
    ("cms", "Content management", "Gestion de contenu", ["wordpress", "readme"]),
    ("crm", "CRM and support", "CRM et support", ["salesforce", "hubspot", "zendesk"]),
    ("prospecting", "Prospecting", "Prospection", ["hunter", "apollo", "anymail_finder", "crunchbase"]),
    ("erp", "ERP", "ERP", ["erpnext"]),
    ("hr", "Human resources", "Ressources humaines", ["sage_hr"]),
    ("ecommerce", "E-commerce", "E-commerce", ["shopify", "woocommerce"]),
    ("payments", "Payments", "Paiements", ["stripe"]),
    ("social", "Social networks", "Réseaux sociaux", ["tiktok_business", "youtube", "twitter"]),
    ("marketing", "Marketing, advertising and SEO", "Marketing, publicité et SEO",
     ["google_analytics", "google_search_console", "googleads", "ahrefs"]),
    ("emailing", "Emailing", "Emailing", ["mailchimp", "brevo", "mailgun", "sendgrid", "emaillistverify"]),
    ("search", "Web search and extraction", "Recherche web et extraction", ["tavily", "serpapi", "apify"]),
    ("geo", "Geolocation", "Géolocalisation", ["google_maps", "google_address_validation", "yelp", "iplocate"]),
    ("data", "Databases and analytics", "Bases de données et analyse",
     ["supabase", "pinecone", "mongo_db_atlas_administration", "databricks"]),
    ("cloud", "Cloud and development", "Cloud et développement",
     ["cloudflare_dns", "vercel", "laravel_cloud", "docker_hub", "circleci", "ngrok"]),
    ("security", "Security and authorization", "Sécurité et autorisations", ["permit_io"]),
    ("monitoring", "Monitoring", "Supervision", ["sentry", "datadog", "grafana", "grafana_cloud"]),
    ("design", "Design", "Design", ["figma", "canva"]),
    ("media", "Image and video generation", "Génération d'images et vidéo", ["heygen", "higgsfield_ai"]),
    ("voice", "Voice and transcription", "Voix et transcription", ["elevenlabs", "deepgram"]),
    ("crypto", "Blockchain and crypto", "Blockchain et cryptomonnaies", ["alchemy", "crypto_apis"]),
]

# What the requested table says about each app: OAuth, API key, or both.
BOTH = {"calendly", "notion", "airtable", "typeform", "youtube", "supabase", "cloudflare_dns", "figma"}
APIKEY = {
    "agora", "motion", "wordpress", "readme", "zendesk", "hunter", "apollo", "anymail_finder",
    "crunchbase", "erpnext", "sage_hr", "woocommerce", "stripe", "ahrefs", "mailchimp", "brevo",
    "mailgun", "sendgrid", "emaillistverify", "tavily", "serpapi", "apify", "google_maps",
    "google_address_validation", "yelp", "iplocate", "pinecone", "mongo_db_atlas_administration",
    "databricks", "vercel", "laravel_cloud", "docker_hub", "circleci", "ngrok", "permit_io",
    "datadog", "grafana", "grafana_cloud", "heygen", "higgsfield_ai", "elevenlabs", "deepgram",
    "alchemy", "crypto_apis",
}

# Methods Navin adds on top of OpenConnector's, each one a documented way to
# authenticate the same public API.
EXTRA_METHODS: dict[str, list[dict[str, Any]]] = {
    "youtube": [{
        "type": "api_key", "label": "API Key", "placeholder": "AIza...",
        "description": "YouTube Data API v3 key for public data (search, videos, channels, playlists). "
        "Create it in Google Cloud Console > APIs & Services > Credentials.",
    }],
    "sentry": [{
        "type": "api_key", "label": "Auth Token", "placeholder": "sntryu_...",
        "description": "Sentry personal or organization auth token sent as a Bearer token. "
        "Create it in Sentry > Settings > Personal Tokens.",
    }],
    "slackbot": [{
        "type": "api_key", "label": "Bot token", "placeholder": "xoxb-...",
        "description": "Bot User OAuth Token of your Slack app (OAuth & Permissions page), sent as a Bearer token.",
    }],
    "hubspot": [{
        "type": "api_key", "label": "Private app access token", "placeholder": "pat-na1-...",
        "description": "HubSpot private app access token sent as a Bearer token. "
        "Create it in HubSpot > Settings > Integrations > Private Apps.",
    }],
}

# OpenConnector reaches HubSpot through its hosted MCP server; Navin talks to
# the public REST API, so it uses HubSpot's standard OAuth app endpoints.
OAUTH_OVERRIDES: dict[str, dict[str, Any]] = {
    "hubspot": {
        "type": "oauth2",
        "authorizationUrl": "https://app.hubspot.com/oauth/authorize",
        "tokenUrl": "https://api.hubapi.com/oauth/v1/token",
        "scopes": [
            "oauth", "crm.objects.contacts.read", "crm.objects.contacts.write",
            "crm.objects.companies.read", "crm.objects.companies.write",
            "crm.objects.deals.read", "crm.objects.deals.write",
        ],
        "tokenEndpointAuthMethod": "client_secret_post",
    },
}

# Where each OAuth app is created (Navin data, shown next to the client fields).
OAUTH_APP_URLS: dict[str, str] = {
    "google": "https://console.cloud.google.com/apis/credentials",
    "microsoft": "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
    "slack": "https://api.slack.com/apps",
    "discord": "https://discord.com/developers/applications",
    "zoom": "https://marketplace.zoom.us/develop/create",
    "calendly": "https://developer.calendly.com/",
    "notion": "https://www.notion.so/profile/integrations",
    "dropbox": "https://www.dropbox.com/developers/apps",
    "airtable": "https://airtable.com/create/oauth",
    "typeform": "https://admin.typeform.com/account#/section/apps",
    "salesforce": "https://help.salesforce.com/s/articleView?id=sf.connected_app_create.htm",
    "hubspot": "https://developers.hubspot.com/",
    "shopify": "https://partners.shopify.com/",
    "tiktok_business": "https://business-api.tiktok.com/portal/apps",
    "twitter": "https://developer.x.com/en/portal/dashboard",
    "supabase": "https://supabase.com/docs/guides/integrations/build-a-supabase-integration",
    "cloudflare_dns": "https://developers.cloudflare.com/fundamentals/api/",
    "sentry": "https://sentry.io/settings/account/api/applications/",
    "figma": "https://www.figma.com/developers/apps",
    "canva": "https://www.canva.com/developers/integrations/connect-api",
}
GOOGLE = {
    "gmail", "googlemeet", "googlecalendar", "googletasks", "googledrive", "googlesheets",
    "googleforms", "youtube", "google_analytics", "google_search_console", "googleads",
}
MICROSOFT = {"microsoft_teams", "outlook", "outlook_calendar", "one_drive", "excel"}
# Local notes that OpenConnector shows but never sends to the provider.
_DROPPED_FIELDS = {"permissionScopeGuide"}


def family_of(service: str) -> str:
    if service in GOOGLE:
        return "google"
    if service in MICROSOFT:
        return "microsoft"
    if service in {"slack", "slackbot"}:
        return "slack"
    return service


_OAUTH_KEYS = (
    "authorizationUrl", "tokenUrl", "refreshTokenUrl", "revocationUrl", "scopes", "optionalScopes",
    "scopeSeparator", "tokenEndpointAuthMethod", "tokenRequestFormat", "pkce", "authorizationParams",
    "tokenRequestFields", "tokenResponseEnvelope", "authorizationRequestFields",
    "tokenRequestCallbackParameters", "clientSetup",
)


def _clean(text: Any, limit: int) -> str:
    value = " ".join(str(text or "").split())
    # Navin copy never carries long dashes.
    value = value.replace("\u2014", "-").replace("\u2013", "-")
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def _field(raw: dict[str, Any]) -> dict[str, Any]:
    field = {
        "key": str(raw.get("key") or ""),
        "label": _clean(raw.get("label") or raw.get("key"), 80),
        "inputType": str(raw.get("inputType") or ("password" if raw.get("secret") else "text")),
        "required": bool(raw.get("required")),
        "secret": bool(raw.get("secret")),
    }
    for key in ("placeholder", "defaultValue", "location"):
        if raw.get(key) not in (None, ""):
            field[key] = _clean(raw[key], 240)
    if raw.get("description"):
        field["description"] = _clean(raw["description"], 400)
    return field


def _method(raw: dict[str, Any]) -> dict[str, Any] | None:
    kind = raw.get("type")
    if kind == "api_key":
        label = _clean(raw.get("label") or "API Key", 80)
        fields = [{
            "key": "apiKey", "label": label, "inputType": "password", "required": True, "secret": True,
            "placeholder": _clean(raw.get("placeholder"), 120),
        }]
        if raw.get("description"):
            fields[0]["description"] = _clean(raw["description"], 400)
        fields += [_field(item) for item in raw.get("extraFields") or []]
        return {"type": "api_key", "label": label, "fields": fields}
    if kind == "custom_credential":
        fields = [_field(item) for item in raw.get("fields") or []]
        service_account = any(item["key"] == "serviceAccountJson" for item in fields)
        out: dict[str, Any] = {
            "type": "service_account" if service_account else "custom",
            "label": _clean(raw.get("label") or ("Service Account" if service_account else "Credentials"), 80),
            "fields": fields,
        }
        if raw.get("description"):
            out["description"] = _clean(raw["description"], 400)
        return out
    if kind == "oauth2":
        out = {"type": "oauth2", "label": "OAuth"}
        for key in _OAUTH_KEYS:
            if raw.get(key) not in (None, [], {}):
                out[key] = raw[key]
        out["fields"] = [_field(item) for item in raw.get("clientConfigFields") or []]
        return out
    return None


def _params(schema: dict[str, Any]) -> list[list[Any]]:
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return []
    required = set(schema.get("required") or [])
    rows: list[list[Any]] = []
    for name, prop in list(props.items())[:25]:
        prop = prop if isinstance(prop, dict) else {}
        kind = prop.get("type") or ("enum" if prop.get("enum") else "any")
        if isinstance(kind, list):
            kind = "|".join(str(item) for item in kind)
        if prop.get("enum"):
            kind = f"{kind}({','.join(str(item) for item in prop['enum'][:8])})"
        rows.append([name, str(kind), 1 if name in required else 0, _clean(prop.get("description"), 140)])
    return rows


# Names as the requested table spells them.
NAME_OVERRIDES: dict[str, str] = {"tiktok_business": "TikTok"}

# Refresh calls whose field names differ from the code exchange. TikTok's code
# exchange takes app_id / secret, its refresh endpoint client_id / client_secret
# (checked live: the refresh endpoint answers "client_id: Missing data").
REFRESH_FIELDS: dict[str, dict[str, Any]] = {
    "tiktok_business": {
        "clientId": "client_id", "clientSecret": "client_secret", "grantType": "grant_type",
        "refreshToken": "refresh_token",
    },
}

# OpenConnector's texts talk about its own service; Navin's say what Navin does.
TEXT_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("shown by this runtime", "shown by Navin"),
    ("in this runtime", "in Navin"),
    ("so the runtime can", "so Navin can"),
)
_SLACK_UPLOAD = (
    "Upload a file in three calls: POST files.getUploadURLExternal (body_format=form with filename and length), "
    "POST the bytes to the returned upload_url with body_file, then POST files.completeUploadExternal with "
    "files=[{id, title}] and channel_id."
)
ACTION_TEXT: dict[tuple[str, str], str] = {
    ("slack", "upload_file"): _SLACK_UPLOAD,
    ("slackbot", "upload_file"): _SLACK_UPLOAD,
}


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        for old, new in TEXT_REPLACEMENTS:
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()}
    return value

_OBJ = ("objectType", "string", True, "CRM object type: contacts, companies or deals.")
_PROPS = ("properties", "object", True, "Property values, e.g. {email, firstname} or {name, domain} or {dealname, amount}.")
_LIST = [
    ("limit", "integer", False, "Page size, up to 100."),
    ("after", "string", False, "Cursor from paging.next.after of the previous page."),
    ("properties", "string", False, "Comma-separated properties to return."),
]


def _hubspot_object(singular: str, plural: str) -> list[tuple]:
    key = f"{singular}Id"
    base = f"/crm/v3/objects/{plural}"
    return [
        (f"list_{plural}", "read", f"GET {base}", f"List HubSpot {plural} page by page.", _LIST),
        (f"get_{singular}", "read", f"GET {base}/{{{key}}}", f"Get one HubSpot {singular} by ID.",
         [(key, "string", True, f"HubSpot {singular} ID."), _LIST[2]]),
        (f"create_{singular}", "write", f"POST {base}", f"Create a HubSpot {singular}.",
         [_PROPS, ("associations", "array", False, "Records to associate on creation.")]),
        (f"update_{singular}", "write", f"PATCH {base}/{{{key}}}", f"Update properties of a HubSpot {singular}.",
         [(key, "string", True, f"HubSpot {singular} ID."), _PROPS]),
        (f"archive_{singular}", "destructive", f"DELETE {base}/{{{key}}}", f"Archive a HubSpot {singular}.",
         [(key, "string", True, f"HubSpot {singular} ID.")]),
    ]


# OpenConnector's HubSpot actions are the tools of HubSpot's MCP server. Navin
# calls the public REST API, so its HubSpot actions name the REST endpoints,
# within the scopes of the HubSpot OAuth app above.
ACTION_OVERRIDES: dict[str, list[tuple]] = {
    "hubspot": [
        *_hubspot_object("contact", "contacts"),
        *_hubspot_object("company", "companies"),
        *_hubspot_object("deal", "deals"),
        ("search_crm_objects", "read", "POST /crm/v3/objects/{objectType}/search",
         "Search contacts, companies or deals with filters, a text query and sorting.",
         [_OBJ, ("filterGroups", "array", False, "Filters such as [{filters: [{propertyName, operator, value}]}]."),
          ("query", "string", False, "Full-text query."), ("properties", "array", False, "Properties to return."),
          ("sorts", "array", False, "Sort rules."), ("limit", "integer", False, "Page size, up to 200."),
          ("after", "string", False, "Paging cursor.")]),
        ("batch_read_crm_objects", "read", "POST /crm/v3/objects/{objectType}/batch/read",
         "Read several contacts, companies or deals by ID in one call.",
         [_OBJ, ("inputs", "array", True, "[{id}] of the records to read."),
          ("properties", "array", False, "Properties to return.")]),
        ("list_associations", "read", "GET /crm/v4/objects/{fromObjectType}/{fromObjectId}/associations/{toObjectType}",
         "List the records associated with a record, such as the deals of a contact.",
         [("fromObjectType", "string", True, "contacts, companies or deals."),
          ("fromObjectId", "string", True, "Record ID."), ("toObjectType", "string", True, "contacts, companies or deals.")]),
        ("associate_records", "write",
         "PUT /crm/v4/objects/{fromObjectType}/{fromObjectId}/associations/default/{toObjectType}/{toObjectId}",
         "Associate two records with the default association type.",
         [("fromObjectType", "string", True, "contacts, companies or deals."), ("fromObjectId", "string", True, "Record ID."),
          ("toObjectType", "string", True, "contacts, companies or deals."), ("toObjectId", "string", True, "Record ID.")]),
        ("get_account_details", "read", "GET /account-info/v3/details",
         "Get the HubSpot account (portal ID, time zone, currency, data hosting).", []),
    ],
}


def _override_actions(rows: list[tuple]) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": description,
            "op": op,
            "endpoint": endpoint,
            "params": [[key, kind, 1 if required else 0, text] for key, kind, required, text in params],
        }
        for name, op, endpoint, description, params in rows
    ]


def _actions(service: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
    if service in ACTION_OVERRIDES:
        return _override_actions(ACTION_OVERRIDES[service])
    return [
        {
            "name": action["name"],
            "description": ACTION_TEXT.get((service, action["name"])) or _clean(action.get("description"), 280),
            "op": action.get("operationType") or "read",
            "params": _params(action.get("inputSchema") or {}),
        }
        for action in raw.get("actions") or []
    ]


def _icon(service: str) -> str:
    for ext in ("svg", "png", "webp", "jpg"):
        if (ICONS_DIR / f"{service}.{ext}").is_file():
            return f"{service}.{ext}"
    return ""


def build(source: Path) -> dict[str, Any]:
    providers: list[dict[str, Any]] = []
    for category, _en, _fr, services in CATEGORIES:
        for service in services:
            raw = json.loads((source / "catalog" / "apps" / f"{service}.json").read_text(encoding="utf-8"))
            methods = [m for m in (_method(item) for item in raw.get("auth") or []) if m]
            if service in OAUTH_OVERRIDES:
                methods = [m for m in methods if m["type"] != "oauth2"]
                methods.insert(0, _method(OAUTH_OVERRIDES[service]))
            for m in methods:
                if m["type"] == "oauth2" and service in REFRESH_FIELDS:
                    fields = dict(m.get("tokenRequestFields") or {})
                    fields["refresh"] = {**(fields.get("refresh") or {}), **REFRESH_FIELDS[service]}
                    m["tokenRequestFields"] = fields
            for extra in EXTRA_METHODS.get(service, []):
                methods.append(_method(extra))
            auth = "both" if service in BOTH else "apikey" if service in APIKEY else "oauth"
            if auth == "apikey":
                # The requested table lists these apps with an API key only.
                methods = [m for m in methods if m["type"] != "oauth2"]
            for m in methods:
                m["fields"] = [f for f in m.get("fields", []) if f["key"] not in _DROPPED_FIELDS]
                if m["type"] == "oauth2":
                    m["family"] = family_of(service)
                    m["appUrl"] = OAUTH_APP_URLS.get(family_of(service), raw.get("homepageUrl") or "")
            # The first method is the one the setup form opens on.
            if auth == "apikey":
                methods.sort(key=lambda m: m["type"] == "oauth2")
            else:
                methods.sort(key=lambda m: m["type"] != "oauth2")
            providers.append(_scrub({
                "service": service,
                "name": NAME_OVERRIDES.get(service) or _clean(raw.get("displayName") or service, 80),
                "category": category,
                "auth": auth,
                "homepage": raw.get("homepageUrl") or "",
                "description": _clean(raw.get("description"), 300),
                "icon": _icon(service),
                "methods": methods,
                "actions": _actions(service, raw),
            }))
    return {
        "version": 1,
        "source": {
            "name": "OpenConnector",
            "url": "https://github.com/oomol-lab/open-connector",
            "license": "Modified Apache-2.0 (see navin/connectors/NOTICE.md)",
            "copyright": "Copyright 2026 OOMOL",
        },
        "categories": [{"id": cid, "label": en, "label_fr": fr} for cid, en, fr, _ in CATEGORIES],
        "providers": providers,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default=str(ROOT / "open-connector"), help="OpenConnector checkout")
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()
    data = build(Path(args.source))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    actions = sum(len(p["actions"]) for p in data["providers"])
    print(f"{len(data['providers'])} connectors, {actions} actions, {out.stat().st_size} bytes -> {out}")


if __name__ == "__main__":
    main()
