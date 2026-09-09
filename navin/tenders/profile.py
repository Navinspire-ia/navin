# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Company profile helpers for the Tenders wizard. No invented facts."""

from __future__ import annotations

from typing import Any

NOT_ON_FILE = "not on file"

_ZONE_ORDER = (
    "europe",
    "africa",
    "gcc",
    "maghreb",
    "americas",
    "international",
    "oceania",
    "asia",
    "mena",
)


def csv_list(value: Any, *, upper: bool = False) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value]
    elif isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
    else:
        items = []
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item:
            continue
        token = item.upper() if upper else item
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
    return out


def _dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(row) for row in value if isinstance(row, dict)]


def default_templates() -> dict[str, Any]:
    return {"word": [], "ppt": [], "reuse_slides": []}


def _file_rows(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict) and (raw.get("file_id") or raw.get("name") or raw.get("excerpt")):
        return [dict(raw)]
    if not isinstance(raw, list):
        return []
    return [
        dict(row)
        for row in raw
        if isinstance(row, dict) and (row.get("file_id") or row.get("name") or row.get("excerpt"))
    ]


def normalize_templates(raw: Any) -> dict[str, Any]:
    base = default_templates()
    if not isinstance(raw, dict):
        return base
    base["word"] = _file_rows(raw.get("word"))
    base["ppt"] = _file_rows(raw.get("ppt"))
    base["reuse_slides"] = _file_rows(raw.get("reuse_slides"))
    return base


def normalize_sites(raw: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _dicts(raw):
        kind = str(row.get("kind") or "branch").strip().lower()
        if kind not in {"hq", "branch"}:
            kind = "branch"
        try:
            headcount = int(row.get("headcount") or 0)
        except (TypeError, ValueError):
            headcount = 0
        rows.append(
            {
                "id": str(row.get("id") or "").strip(),
                "kind": kind,
                "name": str(row.get("name") or "").strip(),
                "country": str(row.get("country") or "").strip().upper(),
                "address": str(row.get("address") or "").strip(),
                "city": str(row.get("city") or "").strip(),
                "headcount": max(0, headcount),
            }
        )
    return rows


def normalize_partners(raw: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _dicts(raw):
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        rows.append(
            {
                "name": name,
                "country": str(row.get("country") or "").strip().upper(),
                "role": str(row.get("role") or "").strip(),
            }
        )
    return rows


def normalize_references(raw: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            title = item.strip()
            if title:
                rows.append({"title": title})
            continue
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        file_id = str(item.get("file_id") or "").strip()
        if not title and not file_id:
            continue
        if not title:
            title = str(item.get("name") or "reference").strip() or "reference"
        amount = item.get("amount")
        try:
            amount_n = float(amount) if amount not in (None, "") else None
        except (TypeError, ValueError):
            amount_n = None
        year = str(item.get("year") or "").strip()
        rows.append(
            {
                "title": title,
                "client": str(item.get("client") or "").strip(),
                "year": year,
                "country": str(item.get("country") or "").strip().upper(),
                "amount": amount_n,
                "file_id": str(item.get("file_id") or "").strip(),
                "name": str(item.get("name") or "").strip(),
                "path": str(item.get("path") or "").strip(),
                "extract": str(item.get("extract") or "").strip(),
                "chars": int(item["chars"]) if isinstance(item.get("chars"), int) else len(str(item.get("excerpt") or "")),
                "excerpt": str(item.get("excerpt") or "").strip(),
            }
        )
    return rows


def normalize_custom_sources(raw: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _dicts(raw):
        url = str(row.get("url") or "").strip()
        name = str(row.get("name") or "").strip()
        if not url or not name:
            continue
        ingest = str(row.get("ingest") or "html").strip().lower()
        if ingest not in {"api", "html", "search"}:
            ingest = "html"
        sid = str(row.get("id") or "").strip() or _slug(name)
        rows.append(
            {
                "id": sid,
                "name": name,
                "url": url,
                "api": str(row.get("api") or "").strip(),
                "country": str(row.get("country") or "").strip().upper(),
                "zone": str(row.get("zone") or "international").strip().lower(),
                "ingest": ingest,
            }
        )
    return rows


def _slug(value: str) -> str:
    chars = [ch.lower() if ch.isalnum() else "-" for ch in value.strip()]
    text = "".join(chars).strip("-")
    while "--" in text:
        text = text.replace("--", "-")
    return (text or "source")[:40]


def wizard_ready(profile: dict[str, Any]) -> bool:
    name = str(profile.get("name") or "").strip()
    country = str(profile.get("country") or "").strip()
    currency = str(profile.get("currency") or "").strip()
    specialty = str(profile.get("specialty") or "").strip()
    countries = csv_list(profile.get("countries"), upper=True)
    crafts = csv_list(profile.get("crafts"))
    source_ids = csv_list(profile.get("source_ids"))
    custom = normalize_custom_sources(profile.get("custom_sources"))
    finished = bool(profile.get("wizard_complete"))
    return bool(
        finished
        and name
        and country
        and currency
        and specialty
        and countries
        and crafts
        and (source_ids or custom)
    )


def stated(value: Any, fallback: str = NOT_ON_FILE) -> str:
    text = str(value or "").strip()
    return text if text else fallback


def _doc_row(item: dict[str, Any], bucket: str, label: str) -> dict[str, Any]:
    return {
        "bucket": bucket,
        "label": label,
        "file_id": str(item.get("file_id") or "").strip(),
        "name": str(item.get("name") or item.get("title") or "").strip(),
        "title": str(item.get("title") or item.get("name") or "").strip(),
        "excerpt": str(item.get("excerpt") or "").strip(),
        "extract": str(item.get("extract") or "").strip(),
        "path": str(item.get("path") or "").strip(),
        "client": str(item.get("client") or "").strip(),
        "year": str(item.get("year") or "").strip(),
        "chars": int(item["chars"]) if isinstance(item.get("chars"), int) else len(str(item.get("excerpt") or "")),
    }


def filed_documents(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Every model and reference on file. Write must reuse these if present."""
    templates = normalize_templates(profile.get("templates"))
    rows: list[dict[str, Any]] = []
    for key, label in (
        ("word", "Word model"),
        ("ppt", "PowerPoint model"),
        ("reuse_slides", "Reuse slide"),
    ):
        for item in templates.get(key) or []:
            if isinstance(item, dict):
                rows.append(_doc_row(item, key, label))
    for item in normalize_references(profile.get("references")):
        rows.append(_doc_row(item, "reference", "Reference"))
    return rows


def template_excerpts(profile: dict[str, Any]) -> list[str]:
    return [row["excerpt"] for row in filed_documents(profile) if row.get("excerpt")]


def public_profile(profile: dict[str, Any], *, has_sam_key: bool, custom_keys: set[str]) -> dict[str, Any]:
    out = dict(profile)
    custom = []
    for row in normalize_custom_sources(out.get("custom_sources")):
        item = dict(row)
        item.pop("api_key", None)
        item.pop("key", None)
        item["has_key"] = row["id"] in custom_keys
        custom.append(item)
    out["custom_sources"] = custom
    out["has_sam_key"] = bool(has_sam_key)
    return out


def group_catalog_by_zone(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {zone: [] for zone in _ZONE_ORDER}
    extra: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        zone = str(row.get("zone") or "international").strip().lower()
        if zone not in buckets:
            extra.setdefault(zone, []).append(row)
        else:
            buckets[zone].append(row)
    grouped: list[dict[str, Any]] = []
    for zone in _ZONE_ORDER:
        if buckets[zone]:
            grouped.append({"zone": zone, "sources": buckets[zone]})
    for zone, items in extra.items():
        grouped.append({"zone": zone, "sources": items})
    return grouped
