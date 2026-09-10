# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""schema.org JobPosting reader: connector 2 of the Career engine.

Any public page that embeds ``<script type="application/ld+json">`` with a
JobPosting (employer career sites, ATS-hosted pages, most job boards) yields a
structured offer without a site-specific parser: title, hiring organisation,
place, remote flag, employment type, base salary (min / max / currency / unit),
posting and start dates, experience requirements and the full description.

This module never fetches on its own from a closed host: callers hand it HTML
they were allowed to read (employer careers pages, an open ATS page, a page the
user pasted). ``jobposting_rows`` folds the postings onto the shared
vocabulary so the desk filters them like any other source.
"""

from __future__ import annotations

import json
import re
from typing import Any

from navin.career.normalize import (
    contracts_track,
    duration_from_text,
    normalize_contracts,
    normalize_experience,
    normalize_remote,
    pay_from_numbers,
)
from navin.career.sources import html_to_text, infer_country_iso, is_closed_job_url, stable_job_id

_JSONLD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
_UNIT_TEXT = {
    "HOUR": "hour",
    "HOURLY": "hour",
    "DAY": "day",
    "DAILY": "day",
    "WEEK": "week",
    "WEEKLY": "week",
    "MONTH": "month",
    "MONTHLY": "month",
    "YEAR": "year",
    "YEARLY": "year",
    "ANNUAL": "year",
}


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("@value") or value.get("value") or "")
    if isinstance(value, list):
        return ", ".join(part for part in (_text(item) for item in value) if part)
    return str(value or "").strip()


def _types(item: dict[str, Any]) -> list[str]:
    kind = item.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return [str(k or "").rsplit("/", 1)[-1] for k in kinds]


def extract_jobpostings(html_text: str) -> list[dict[str, Any]]:
    """Every JobPosting object in the page, @graph and arrays included."""
    out: list[dict[str, Any]] = []
    for blob in _JSONLD_RE.findall(html_text or ""):
        cleaned = blob.strip()
        # Some sites wrap the payload in an HTML comment or CDATA block.
        cleaned = re.sub(r"^<!--|-->$|^//<!\[CDATA\[|//\]\]>$", "", cleaned).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            continue
        queue: list[Any] = data if isinstance(data, list) else [data]
        while queue:
            item = queue.pop(0)
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                queue.extend(node for node in graph if isinstance(node, dict))
            main = item.get("mainEntity")
            if isinstance(main, dict):
                queue.append(main)
            if "JobPosting" in _types(item):
                out.append(item)
    return out


def _salary(item: dict[str, Any]) -> tuple[Any, Any, str, str]:
    """(min, max, currency, unit) out of baseSalary / estimatedSalary."""
    base = item.get("baseSalary") or item.get("estimatedSalary")
    if isinstance(base, list):
        base = base[0] if base else None
    if not isinstance(base, dict):
        return None, None, "", ""
    currency = _text(base.get("currency"))
    value = base.get("value")
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        low = value.get("minValue", value.get("value"))
        high = value.get("maxValue", value.get("value"))
        unit = _UNIT_TEXT.get(_text(value.get("unitText")).upper(), _text(value.get("unitText")).lower())
        return low, high, currency, unit
    if isinstance(value, (int, float, str)) and str(value).strip():
        unit = _UNIT_TEXT.get(_text(base.get("unitText")).upper(), "")
        return value, value, currency, unit
    return base.get("minValue"), base.get("maxValue"), currency, _UNIT_TEXT.get(_text(base.get("unitText")).upper(), "")


def _place(item: dict[str, Any]) -> tuple[str, str]:
    """(location label, ISO country) from jobLocation / applicantLocationRequirements."""
    loc = item.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    address = loc.get("address") if isinstance(loc, dict) else {}
    if isinstance(address, str):
        return address[:120], infer_country_iso("", address)
    if not isinstance(address, dict):
        address = {}
    parts = [_text(address.get(key)) for key in ("addressLocality", "addressRegion", "addressCountry")]
    label = ", ".join(part for part in parts if part)
    country_raw = _text(address.get("addressCountry"))
    iso = country_raw.upper() if len(country_raw) == 2 else infer_country_iso("", country_raw or label)
    if not label:
        req = item.get("applicantLocationRequirements")
        label = _text(req)
        if not iso:
            iso = infer_country_iso("", label)
    return label[:120], iso


def _experience(item: dict[str, Any]) -> list[Any]:
    out: list[Any] = []
    req = item.get("experienceRequirements")
    items = req if isinstance(req, list) else [req]
    for entry in items:
        if isinstance(entry, dict):
            months = entry.get("monthsOfExperience")
            try:
                if months is not None:
                    out.append(int(float(months) / 12))
            except (TypeError, ValueError):
                pass
            out.append(_text(entry.get("description") or entry.get("name")))
        elif entry:
            out.append(_text(entry))
    return out


def jobposting_facts(item: dict[str, Any], *, country: str = "", track: str = "") -> dict[str, Any]:
    """Normalized fields (pay, contracts, remote, experience, dates) for one posting."""
    title = _text(item.get("title") or item.get("name"))
    description = html_to_text(_text(item.get("description")))
    location, iso = _place(item)
    iso = iso or country.upper()
    kinds = normalize_contracts(item.get("employmentType"), title)
    low, high, currency, unit = _salary(item)
    telecommute = _text(item.get("jobLocationType")).upper() == "TELECOMMUTE"
    remote = normalize_remote(True if telecommute else None, title, location, description[:400])
    level, years = normalize_experience(_experience(item), title)
    months, duration = duration_from_text(f"{title} {description[:800]}")
    facts: dict[str, Any] = {
        "title": title[:180],
        "company": _text(item.get("hiringOrganization"))[:80],
        "location": location,
        "country": iso,
        "description": description[:4000],
        "posted_at": _text(item.get("datePosted"))[:10],
        "valid_through": _text(item.get("validThrough"))[:10],
        "start_date": _text(item.get("jobStartDate"))[:40],
        "contracts": kinds,
        "employment_type": ", ".join(kinds),
        "track": contracts_track(kinds, track),
        "remote": remote,
        "experience_level": level,
        "experience_years_min": years,
        "seniority": level,
        "duration": duration,
        "duration_months": months,
        "stack": [part for part in (_text(item.get("skills")) or "").split(",") if part.strip()][:12],
    }
    facts.update(pay_from_numbers(low, high, period=unit, currency=currency, country=iso, track=facts["track"]))
    return facts


def jobposting_rows(
    html_text: str,
    *,
    page_url: str,
    source: str = "jsonld",
    attribution: str = "",
    ingest: str = "jsonld",
    country: str = "",
    track: str = "",
) -> list[dict[str, Any]]:
    """Desk rows for every JobPosting in the page. Closed-host URLs are dropped."""
    rows: list[dict[str, Any]] = []
    for item in extract_jobpostings(html_text):
        facts = jobposting_facts(item, country=country, track=track)
        url = _text(item.get("url")) or page_url
        if not facts["title"] or not url or is_closed_job_url(url):
            continue
        row = {
            "id": stable_job_id(source, url, facts["title"]),
            "source": source,
            "url": url,
            "stage": "discovered",
            "contact": "",
            "ingest": ingest,
            "attribution": attribution or f"{facts['company'] or source} (JobPosting)",
            **facts,
        }
        rows.append(row)
    return rows
