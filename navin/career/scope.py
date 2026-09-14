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


def offer_work_mode(row: dict[str, Any]) -> str:
    from navin.career.normalize import normalize_remote

    return normalize_remote(row.get("remote"), row.get("remote_evidence"), row.get("title"), row.get("location"), row.get("description"))


def selling_floor(criteria: dict[str, Any], work_mode: str = "") -> float:
    """Explicit mode floors replace the legacy floor, including an explicit zero."""
    legacy = max(float(criteria.get("min_rate") or 0), float(criteria.get("sale_rate") or 0))
    remote = criteria.get("sale_rate_remote")
    onsite = criteria.get("sale_rate_onsite")
    remote = legacy if remote is None else float(remote)
    onsite = legacy if onsite is None else float(onsite)
    return remote if work_mode == "remote" else onsite if work_mode in {"hybrid", "onsite"} else max(remote, onsite)


def offer_rejection(row: dict[str, Any], criteria: dict[str, Any]) -> str:
    """Return the failed business constraint, or an empty string for a match."""
    from navin.career.matching import is_off_role, requires_restricted_eligibility
    from navin.career.needs import is_project_need
    from navin.career.normalize import convert_money, enrich_facts
    from navin.career.search_plan import role_scope
    from navin.career.vocabulary import has_term, role_present, skill_present

    facts = enrich_facts(dict(row))
    if not country_in_scope(facts, criteria.get("countries", [])):
        return "country"
    if criteria.get("city") and not has_term(str(facts.get("location") or facts.get("city") or ""), criteria["city"]):
        return "city"
    roles = [role for role in criteria.get("roles", []) if role_present(str(facts.get("title") or ""), role)]
    if criteria.get("roles"):
        profile = {"titles": roles, "countries_primary": criteria.get("countries", [])}
        if not roles or is_off_role(str(facts.get("title") or ""), profile) or requires_restricted_eligibility(facts, profile):
            return "role"
    if criteria.get("track") == "freelance":
        contract_text = " ".join(str(facts.get(key) or "") for key in ("title", "employment_type", "description"))
        if re.search(r"\b(?:CDI|permanent (?:position|role|employment|contract)|contrat [àa] dur[ée]e ind[ée]termin[ée]e)\b", contract_text, re.I):
            return "track"
    deadline = publication_day(facts.get("deadline"))
    if deadline and date.fromisoformat(deadline) < datetime.now(timezone.utc).date():
        return "expired"
    if criteria.get("track", "both") != "both" and facts.get("track") != criteria["track"]:
        return "track"
    wanted_mode = str(criteria.get("work_mode") or "any")
    actual = offer_work_mode(facts)
    if wanted_mode != "any":
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
    project = is_project_need(facts)
    if not project and not actual and facts.get("track") != "jobs" and selling_floor(criteria, "remote") != selling_floor(criteria, "onsite"):
        return "work_mode_unknown"
    minimum = float(criteria.get("min_project_budget") or 0) if project else selling_floor(criteria, actual)
    if minimum > 0 and facts.get("track") != "jobs":
        # Every advertised bound must meet the floor. A range of 600-700
        # does not guarantee a minimum of 650. Missing pay is unverified.
        keys = ("budget_min", "budget_max") if project else ("daily_rate_min", "daily_rate_max")
        values = [facts[key] for key in keys if facts.get(key) is not None]
        if project and not values and facts.get("budget") is not None:
            values = [facts["budget"]]
        unknown = "budget_unknown" if project else "rate_unknown"
        if not values:
            return unknown
        currency = str(facts.get("currency") or "").upper()
        target = str(criteria.get("currency") or "EUR").upper()
        if not currency:
            return unknown
        try:
            amount = min(float(value) for value in values)
            converted = amount if currency == target else convert_money(amount, currency, target)
        except (TypeError, ValueError):
            return unknown
        if converted is None or not math.isfinite(converted) or converted <= 0:
            return unknown
        if converted < minimum:
            return "budget" if project else "rate"
    if criteria.get("skills"):
        evidence = " ".join(str(facts.get(key) or "") for key in ("title", "description", "snippet"))
        evidence += " " + " ".join(str(skill) for skill in facts.get("stack", []) or [])
        # Multiple roles are alternatives. Only the matching role's defaults
        # and shared manual skills are required, never another role's stack.
        scopes = [role_scope(criteria, role) for role in roles] or [criteria]
        if not any(all(skill_present(evidence, skill) for skill in scope.get("skills", [])) for scope in scopes):
            return "skills"
    return ""
