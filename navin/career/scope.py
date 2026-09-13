# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Enforce sourcing geography using result evidence, never the search query."""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from navin.career.sources import MARKETS, countries_mentioned, infer_country_iso


def publication_day(value: Any, *, today: date | None = None) -> str:
    """Normalize publication evidence once, without using the import date."""
    from navin.career.feeds import iso_day

    today = today or datetime.now(timezone.utc).date()
    normalized = iso_day(value)
    if normalized:
        try:
            return date.fromisoformat(normalized).isoformat()
        except ValueError:
            return ""
    text = str(value or "").strip().lower()
    if text in {"today", "aujourd'hui", "just posted", "just now"}:
        return today.isoformat()
    if text in {"yesterday", "hier"}:
        return (today - timedelta(days=1)).isoformat()
    relative = re.fullmatch(r"(?:il y a\s+)?(\d+)\s*(minutes?|mins?|hours?|heures?|jours?|days?|semaines?|weeks?|mois|months?)\s*(?:ago)?", text)
    if relative:
        count, unit = int(relative[1]), relative[2]
        days = count * (30 if unit.startswith(("mois", "month")) else 7 if unit.startswith(("semaine", "week")) else 1 if unit.startswith(("jour", "day")) else 0)
        if days < 36500:
            return (today - timedelta(days=days)).isoformat()
    for pattern in ("%b %d, %Y", "%B %d, %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return ""


def result_countries(row: dict[str, Any]) -> set[str]:
    country = infer_country_iso(str(row.get("country") or ""))
    if country in MARKETS:
        return {country}
    location = str(row.get("location") or row.get("city") or "")
    located = set(countries_mentioned(location))
    if located:
        return located
    # A snippet can document geography when the provider has no structured
    # location. Ambiguous mentions cannot establish eligibility in a market.
    evidence = " ".join(str(row.get(key) or "") for key in ("title", "headline", "description", "snippet"))
    mentioned = set(countries_mentioned(evidence))
    return mentioned if len(mentioned) == 1 else set()


def country_in_scope(row: dict[str, Any], countries: list[str]) -> bool:
    return not countries or bool(result_countries(row) & set(countries))


def candidate_countries(criteria: dict[str, Any], offer: dict[str, Any] | None = None) -> list[str]:
    if criteria.get("profile_countries"):
        return list(criteria["profile_countries"])
    configured = list(criteria.get("countries") or [])
    country = infer_country_iso(str((offer or {}).get("country") or ""), str((offer or {}).get("location") or ""))
    if country in MARKETS and (not configured or country in configured):
        return [country]
    return configured


def offer_rejection(row: dict[str, Any], criteria: dict[str, Any]) -> str:
    """Return the failed business constraint, or an empty string for a match."""
    from navin.career.matching import job_is_relevant
    from navin.career.normalize import convert_money, enrich_facts, normalize_remote

    facts = enrich_facts(dict(row))
    if not country_in_scope(facts, criteria.get("countries", [])):
        return "country"
    if criteria.get("roles") and not job_is_relevant(facts, {"titles": criteria["roles"], "countries_primary": criteria.get("countries", [])}):
        return "role"
    if criteria.get("track", "both") != "both" and facts.get("track") != criteria["track"]:
        return "track"
    wanted_mode = str(criteria.get("work_mode") or "any")
    if wanted_mode != "any":
        actual = normalize_remote(facts.get("remote"), facts.get("title"), facts.get("location"), facts.get("description"))
        if actual != wanted_mode:
            return "work_mode" if actual else "work_mode_unknown"
    max_age = int(criteria.get("max_age_days") or 0)
    if max_age:
        posted = publication_day(facts.get("posted_at") or facts.get("published_at"))
        if not posted:
            return "date_unknown"
        age = (datetime.now(timezone.utc).date() - date.fromisoformat(posted)).days
        if age < 0 or age > min(30, max_age):
            return "date"
    minimum = max(float(criteria.get("min_rate") or 0), float(criteria.get("sale_rate") or 0))
    if minimum > 0 and facts.get("track") != "jobs":
        # Every advertised bound must meet the floor. A range of 600-700
        # does not guarantee a minimum of 650. Missing pay is unverified.
        values = [facts[key] for key in ("daily_rate_min", "daily_rate_max") if facts.get(key) is not None]
        if not values:
            return "rate_unknown"
        currency = str(facts.get("currency") or "").upper()
        target = str(criteria.get("currency") or "EUR").upper()
        if not currency:
            return "rate_unknown"
        try:
            amount = min(float(value) for value in values)
            converted = amount if currency == target else convert_money(amount, currency, target)
        except (TypeError, ValueError):
            return "rate_unknown"
        if converted is None or not math.isfinite(converted) or converted <= 0:
            return "rate_unknown"
        if converted < minimum:
            return "rate"
    return ""
