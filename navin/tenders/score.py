"""Deterministic 0-100 scoring: technical, country, budget, refs, cash, deadline, win, effort."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from navin.tenders.needs import aliases_for_need, need_family_ids

# Kept for tests and callers that imported the map. Catalog aliases are source of truth.
_CRAFT_ALIASES: dict[str, tuple[str, ...]] = {
    "ai": aliases_for_need("ai"),
    "data": aliases_for_need("data"),
    "cloud": aliases_for_need("cloud"),
    "digital": aliases_for_need("digital"),
}


def _matches(term: str, hay: str) -> bool:
    """Whole-word match. Substring matching turns 'AI' into a hit on 'taille'."""
    needle = term.strip().lower()
    if not needle:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", hay) is not None


def _haystack(tender: dict[str, Any]) -> str:
    parts = [
        tender.get("title"),
        tender.get("description"),
        tender.get("sector"),
        tender.get("cpv"),
        tender.get("eligibility"),
    ]
    return " ".join(str(p or "") for p in parts).lower()


def _days_to_deadline(deadline: str) -> int | None:
    text = (deadline or "").strip()[:10]
    try:
        when = dt.date.fromisoformat(text)
    except ValueError:
        return None
    return (when - dt.date.today()).days


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def score_tender(tender: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    hay = _haystack(tender)
    crafts = [str(c).strip().lower() for c in (profile.get("crafts") or []) if str(c).strip()]
    countries = {str(c).strip().upper() for c in (profile.get("countries") or []) if str(c).strip()}
    min_budget = float(profile.get("min_budget") or 0)
    max_budget = float(profile.get("max_budget") or profile.get("project_size_max") or 0)
    min_deadline = int(profile.get("min_deadline_days") or 0)

    tech = 40.0
    if crafts:
        hits = 0
        for craft in crafts:
            aliases = aliases_for_need(craft)
            if any(_matches(alias, hay) for alias in aliases) or _matches(craft, hay):
                hits += 1
        tech = 35.0 + 65.0 * (hits / max(1, len(crafts)))
        families = need_family_ids(profile.get("crafts"))
        if "modernisation" in hay and families & {"data", "digital", "ai"}:
            tech = max(tech, 78.0)

    projects = [str(item).strip() for item in (profile.get("project_types") or []) if str(item).strip()]
    project_hits = 0
    for item in projects:
        aliases = aliases_for_need(item)
        if any(_matches(alias, hay) for alias in aliases) or _matches(item, hay):
            project_hits += 1
    if project_hits:
        tech = min(100.0, tech + min(10.0, 5.0 * project_hits))

    types = [str(item).strip() for item in (profile.get("tender_types") or []) if str(item).strip()]
    type_score = 70.0
    type_hits = 0
    if types:
        for item in types:
            aliases = aliases_for_need(item)
            if any(_matches(alias, hay) for alias in aliases) or _matches(item, hay):
                type_hits += 1
        type_score = 40.0 + 60.0 * (type_hits / max(1, len(types)))

    country_code = str(tender.get("country") or "").upper()
    if not countries:
        country_score = 70.0
    elif country_code in countries or country_code in {"INTL", "EU"}:
        country_score = 95.0
    else:
        country_score = 25.0

    budget = tender.get("budget")
    if not isinstance(budget, (int, float)) or budget <= 0:
        budget_score = 60.0
    elif budget < min_budget:
        budget_score = 20.0
    elif max_budget > 0 and budget > max_budget:
        budget_score = 15.0
    elif budget < min_budget * 1.5:
        budget_score = 70.0
    else:
        budget_score = 90.0

    refs_needed = "reference" in hay or "references" in hay or "référence" in hay
    refs_have = len(profile.get("references") or [])
    refs_score = 80.0 if refs_have >= 3 else 55.0 if refs_have else 35.0
    if refs_needed and refs_have < 2:
        refs_score = min(refs_score, 40.0)

    ca = float(profile.get("turnover") or 0)
    if ca <= 0:
        cash_score = 55.0
    elif isinstance(budget, (int, float)) and budget > ca * 0.5:
        cash_score = 35.0
    else:
        cash_score = 85.0

    days = _days_to_deadline(str(tender.get("deadline") or ""))
    if days is None:
        deadline_score = 55.0
    elif days < 0:
        deadline_score = 0.0
    elif days < min_deadline:
        deadline_score = 25.0
    elif days < 7:
        deadline_score = 40.0
    else:
        deadline_score = 88.0

    win = _clamp((tech * 0.45 + country_score * 0.2 + refs_score * 0.2 + cash_score * 0.15))
    effort = 100.0 - min(90.0, (len(hay) / 80.0) + (25.0 if re.search(r"consortium|joint venture", hay) else 0))

    total = (
        tech * 0.28
        + country_score * 0.16
        + budget_score * 0.12
        + refs_score * 0.12
        + cash_score * 0.08
        + deadline_score * 0.12
        + win * 0.08
        + effort * 0.04
    )
    # Country, budget or deadline must never carry an off-craft notice over the bar.
    if crafts:
        total = min(total, tech + 15.0)
    if types:
        if type_hits:
            total = min(100.0, total + min(6.0, 3.0 * type_hits))
        else:
            total = max(0.0, total - 5.0)
    total = round(total, 1)
    breakdown = {
        "technical": round(tech, 1),
        "country": round(country_score, 1),
        "budget": round(budget_score, 1),
        "references": round(refs_score, 1),
        "financial": round(cash_score, 1),
        "deadline": round(deadline_score, 1),
        "win_probability": round(win, 1),
        "effort": round(effort, 1),
        "type": round(type_score, 1),
        "days_left": days,
    }
    return {"score": _clamp(total), "breakdown": breakdown, "days_left": days}
