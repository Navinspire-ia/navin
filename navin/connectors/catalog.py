# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The connector catalog: 79 apps in 25 categories, search and pagination.

Read-only data from ``data/catalog.json`` (built by
``scripts/connectors/generate_catalog.py``). Listing never touches the
vault: connection states are passed in by the caller.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).with_name("data") / "catalog.json"
DEFAULT_PAGE_SIZE = 12
MAX_PAGE_SIZE = 100
AUTH_FILTERS = frozenset({"", "oauth", "apikey"})
STATUS_FILTERS = frozenset({"", "connected", "available"})
OPERATIONS = frozenset({"", "read", "write", "destructive"})


class ConnectorError(Exception):
    """A user-facing connector failure with an HTTP status.

    ``upstream`` is the HTTP status the app itself answered (0 when the call
    never reached it), so a refused key can be told from an outage or a
    wrong URL.
    """

    def __init__(self, message: str, status: int = 400, *, upstream: int = 0) -> None:
        super().__init__(message)
        self.message, self.status, self.upstream = message, status, upstream


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def categories() -> list[dict[str, Any]]:
    return list(load()["categories"])


def providers() -> list[dict[str, Any]]:
    return list(load()["providers"])


@lru_cache(maxsize=1)
def _by_service() -> dict[str, dict[str, Any]]:
    return {row["service"]: row for row in load()["providers"]}


def norm(text: Any) -> str:
    """``"Google Calendar"`` -> ``"googlecalendar"`` (accents and punctuation dropped)."""
    folded = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", folded.lower())


_ALIASES = {
    "x": "twitter", "xtwitter": "twitter", "tiktok": "tiktok_business", "teams": "microsoft_teams",
    "msteams": "microsoft_teams", "meet": "googlemeet", "gcal": "googlecalendar", "onedrive": "one_drive",
    "gdrive": "googledrive", "gsheets": "googlesheets", "gforms": "googleforms", "gtasks": "googletasks",
    "mongodb": "mongo_db_atlas_administration", "mongodbatlas": "mongo_db_atlas_administration",
    "atlas": "mongo_db_atlas_administration", "cloudflare": "cloudflare_dns", "dockerhub": "docker_hub",
    "permit": "permit_io", "permitio": "permit_io", "higgsfield": "higgsfield_ai", "cryptoapis": "crypto_apis",
    "anymail": "anymail_finder", "addressvalidation": "google_address_validation", "googlemaps": "google_maps",
    "maps": "google_maps", "ga4": "google_analytics", "analytics": "google_analytics",
    "searchconsole": "google_search_console", "gsc": "google_search_console", "adwords": "googleads",
    "sagehr": "sage_hr", "laravel": "laravel_cloud", "elevenlab": "elevenlabs", "woo": "woocommerce",
    "mongo": "mongo_db_atlas_administration", "slackbot": "slackbot", "bot": "slackbot",
}


@lru_cache(maxsize=1)
def _lookup() -> dict[str, str]:
    table: dict[str, str] = {}
    for row in load()["providers"]:
        table[norm(row["service"])] = row["service"]
        table.setdefault(norm(row["name"]), row["service"])
    for alias, service in _ALIASES.items():
        table.setdefault(alias, service)
    return table


def resolve_service(name: Any) -> str:
    """Catalog id for an id, a display name or a common alias; ``""`` when unknown."""
    return _lookup().get(norm(name), "")


def provider(service: Any) -> dict[str, Any]:
    key = resolve_service(service)
    if not key:
        raise ConnectorError(
            f"Unknown connector: {service}. Use connectors action=list to see the catalog.", 404
        )
    return _by_service()[key]


def method(service: Any, kind: str | None = None) -> dict[str, Any]:
    """The provider's auth method of ``kind`` (its first one when omitted)."""
    row = provider(service)
    if not kind:
        return row["methods"][0]
    for item in row["methods"]:
        if item["type"] == kind:
            return item
    kinds = ", ".join(item["type"] for item in row["methods"])
    raise ConnectorError(f"{row['name']} has no {kind} method; use one of: {kinds}.")


def has_oauth(row: dict[str, Any]) -> bool:
    return any(item["type"] == "oauth2" for item in row["methods"])


def has_key(row: dict[str, Any]) -> bool:
    return any(item["type"] != "oauth2" for item in row["methods"])


def category_label(category_id: str, lang: str = "en") -> str:
    for row in load()["categories"]:
        if row["id"] == category_id:
            return row["label_fr"] if lang == "fr" else row["label"]
    return category_id


def paginate(items: list[Any], page: Any = 1, page_size: Any = DEFAULT_PAGE_SIZE) -> dict[str, Any]:
    try:
        size = int(page_size or DEFAULT_PAGE_SIZE)
    except (TypeError, ValueError):
        size = DEFAULT_PAGE_SIZE
    size = max(1, min(MAX_PAGE_SIZE, size))
    total = len(items)
    pages = max(1, -(-total // size))
    try:
        current = int(page or 1)
    except (TypeError, ValueError):
        current = 1
    current = max(1, min(pages, current))
    start = (current - 1) * size
    return {"items": items[start:start + size], "total": total, "page": current, "page_size": size, "pages": pages}


def summary(row: dict[str, Any], status: dict[str, Any] | None = None) -> dict[str, Any]:
    """Card data for one app: no fields, no actions, no secrets."""
    return {
        "service": row["service"],
        "name": row["name"],
        "category": row["category"],
        "auth": row["auth"],
        "auth_types": [item["type"] for item in row["methods"]],
        "homepage": row.get("homepage") or "",
        "description": row.get("description") or "",
        "icon": row.get("icon") or "",
        "actions": len(row.get("actions") or []),
        "status": status or {"connected": False},
    }


def _words(query: Any) -> list[str]:
    return [word for word in (norm(part) for part in str(query or "").split()) if word]


def _haystack(row: dict[str, Any]) -> str:
    labels = [category_label(row["category"]), category_label(row["category"], "fr")]
    # The OAuth family makes "google" find Gmail and YouTube, "microsoft" find Outlook.
    families = [item.get("family") or "" for item in row["methods"] if item["type"] == "oauth2"]
    return " ".join(norm(part) for part in (row["name"], row["service"], *labels, *families, row.get("description")))


def search(
    *,
    query: Any = "",
    category: Any = "",
    auth: Any = "",
    status: Any = "",
    page: Any = 1,
    page_size: Any = DEFAULT_PAGE_SIZE,
    statuses: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One page of app cards plus per-category counts for the same filters."""
    statuses = statuses or {}
    category = str(category or "").strip()
    auth = str(auth or "").strip().lower()
    status = str(status or "").strip().lower()
    if category and category not in {row["id"] for row in load()["categories"]}:
        raise ConnectorError(f"Unknown connector category: {category}.")
    if auth not in AUTH_FILTERS:
        raise ConnectorError("auth must be oauth or apikey.")
    if status not in STATUS_FILTERS:
        raise ConnectorError("status must be connected or available.")
    words = _words(query)

    def connected(row: dict[str, Any]) -> bool:
        return bool((statuses.get(row["service"]) or {}).get("connected"))

    base = []
    for row in load()["providers"]:
        if words and not all(word in _haystack(row) for word in words):
            continue
        if auth == "oauth" and not has_oauth(row):
            continue
        if auth == "apikey" and not has_key(row):
            continue
        if status == "connected" and not connected(row):
            continue
        if status == "available" and connected(row):
            continue
        base.append(row)
    counts = Counter(row["category"] for row in base)
    linked = Counter(row["category"] for row in base if connected(row))
    shown = [row for row in base if not category or row["category"] == category]
    data = paginate([summary(row, statuses.get(row["service"])) for row in shown], page, page_size)
    data["categories"] = [
        {**row, "count": counts.get(row["id"], 0), "connected": linked.get(row["id"], 0)}
        for row in load()["categories"]
    ]
    data["matching"] = len(base)
    data["catalog_total"] = len(load()["providers"])
    data["connected_total"] = sum(1 for row in load()["providers"] if connected(row))
    return data


def actions(
    service: Any, *, query: Any = "", op: Any = "", page: Any = 1, page_size: Any = 20
) -> dict[str, Any]:
    """One page of an app's actions (name, operation, description, parameters)."""
    row = provider(service)
    op = str(op or "").strip().lower()
    if op not in OPERATIONS:
        raise ConnectorError("op must be read, write or destructive.")
    words = _words(query)
    rows = [
        item for item in row["actions"]
        if (not op or item["op"] == op)
        and all(word in norm(item["name"] + " " + item["description"] + " " + item.get("endpoint", "")) for word in words)
    ]
    data = paginate(rows, page, page_size)
    data["service"] = row["service"]
    return data


def action(service: Any, name: str) -> dict[str, Any]:
    row = provider(service)
    wanted = str(name or "").strip().split(".")[-1]
    for item in row["actions"]:
        if item["name"] == wanted:
            return item
    raise ConnectorError(f"{row['name']} has no action {name}.", 404)
