# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin Match Score. Presentation only - never invent experience."""

from __future__ import annotations

import re
from typing import Any

from navin.career.normalize import convert_money
from navin.career.sources import MARKETS, html_to_text, infer_country_iso, is_listing_hit


def _tokens(text: str) -> set[str]:
    return {part for part in re.findall(r"[a-zA-Z0-9+#.]{2,}", (text or "").lower()) if part}


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


_TITLE_STOP = {
    "senior",
    "sr",
    "junior",
    "jr",
    "lead",
    "staff",
    "principal",
    "freelance",
    "contract",
    "contractor",
    "remote",
    "intern",
    "internship",
    "job",
    "jobs",
    "hiring",
    "independent",
    "the",
    "and",
    "for",
    "with",
    "from",
}
_GENERIC_ROLE = {
    "engineer",
    "developer",
    "consultant",
    "specialist",
    "manager",
    "analyst",
    "technician",
    "officer",
    "associate",
    "architect",
}
_SYNONYMS: dict[str, set[str]] = {
    "ai": {"ai", "ia"},
    "ml": {"ml"},
    "nlp": {"nlp"},
}
_PHRASES: dict[str, tuple[str, ...]] = {
    "ai": ("artificial intelligence", "intelligence artificielle"),
    "ml": ("machine learning", "apprentissage automatique"),
}
_RELATED: dict[str, set[str]] = {
    "ai": {"ml", "llm", "genai", "nlp", "machine", "learning"},
}
_OFF_ROLE = re.compile(
    r"\b(sales jedi|support jedi|inside sales|office assistant|account executive|"
    r"customer support|sales representative|sales contractor|receptionist|"
    r"talent acquisition|recruiter|copywriter|jedi)\b|"
    r"\bsales\b",
    re.I,
)
_CITIZEN = re.compile(
    r"citizenship is required|must be (an? )?(us|u\.s\.|united states) citizen|"
    r"us citizen(ship)? required|security clearance required",
    re.I,
)


def _role_tokens(text: str) -> set[str]:
    return {item for item in _tokens(text) if item not in _TITLE_STOP}


def _title_has_token(title_low: str, title_tokens: set[str], token: str) -> bool:
    aliases = {token, *(_SYNONYMS.get(token) or set())}
    if aliases & title_tokens:
        return True
    return any(phrase in title_low for phrase in _PHRASES.get(token, ()))


def title_match_pct(job_title: str, profile: dict[str, Any]) -> float:
    wanted_titles = [_role_tokens(item) for item in _list(profile.get("titles"))]
    wanted_titles = [item for item in wanted_titles if item]
    if not wanted_titles:
        return 70.0
    title_low = (job_title or "").lower()
    title_tokens = _role_tokens(job_title)
    best = 0.0
    for wanted in wanted_titles:
        distinctive = {item for item in wanted if item not in _GENERIC_ROLE}
        hits = {item for item in wanted if _title_has_token(title_low, title_tokens, item)}
        if distinctive and not any(_title_has_token(title_low, title_tokens, item) for item in distinctive):
            related: set[str] = set()
            for item in distinctive:
                related |= _RELATED.get(item, set())
            best = max(best, 70.0 if related & title_tokens else 25.0)
            continue
        ratio = len(hits) / len(wanted)
        if ratio >= 0.5:
            best = max(best, 90.0)
        elif hits:
            best = max(best, 55.0)
        else:
            best = max(best, 25.0)
    return best


def is_off_role(title: str, profile: dict[str, Any]) -> bool:
    if not _OFF_ROLE.search(title or ""):
        return False
    wanted = " ".join(_list(profile.get("titles"))).lower()
    if re.search(r"\b(sales|recruiter|assistant|jedi)\b", wanted):
        return False
    return True


def requires_restricted_eligibility(row: dict[str, Any], profile: dict[str, Any]) -> bool:
    hay = f"{row.get('title') or ''} {row.get('description') or ''}"
    if not _CITIZEN.search(hay):
        return False
    visa = str(profile.get("visa") or "none").strip().lower()
    if visa not in {"", "none", "open"}:
        return False
    primary = {item.upper() for item in _list(profile.get("countries_primary"))}
    return "US" not in primary


def job_is_relevant(row: dict[str, Any], profile: dict[str, Any]) -> bool:
    title = str(row.get("title") or "")
    if is_listing_hit(title, str(row.get("url") or "")):
        return False
    if is_off_role(title, profile):
        return False
    if requires_restricted_eligibility(row, profile):
        return False
    return title_match_pct(title, profile) >= 50


def score_opportunity(row: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    title = str(row.get("title") or "")
    company = str(row.get("company") or "")
    description = html_to_text(str(row.get("description") or ""))
    hay = _tokens(f"{title} {company} {description} {' '.join(_list(row.get('stack')))}")
    stack = [item.lower() for item in _list(profile.get("stack"))]
    stack_hits = [item for item in stack if item in hay]
    stack_missing = [item for item in stack if item not in hay]
    stack_pct = (100.0 * len(stack_hits) / len(stack)) if stack else 70.0

    country = infer_country_iso(str(row.get("country") or ""), str(row.get("location") or ""))
    weights = profile.get("country_weights") if isinstance(profile.get("country_weights"), dict) else {}
    primary = {c.upper() for c in _list(profile.get("countries_primary"))}
    secondary = {c.upper() for c in _list(profile.get("countries_secondary"))}
    excluded = {c.upper() for c in _list(profile.get("countries_excluded"))}
    if country in excluded:
        country_pct = 0.0
    elif country in weights:
        try:
            country_pct = float(weights[country])
        except (TypeError, ValueError):
            country_pct = 70.0
    elif country in primary:
        country_pct = 100.0
    elif country in secondary:
        country_pct = 70.0
    elif country in {"REMOTE", "WW", ""}:
        country_pct = 80.0
    else:
        country_pct = 45.0

    work_mode = str(profile.get("work_mode") or "any").lower()
    remote = str(row.get("remote") or "").lower()
    if work_mode in {"", "any"}:
        mode_pct = 80.0
    elif work_mode == "remote" and remote in {"remote", "yes", "true"}:
        mode_pct = 100.0
    elif work_mode == remote:
        mode_pct = 95.0
    else:
        mode_pct = 40.0

    min_rate = float(profile.get("min_rate") or 0)
    max_rate = float(profile.get("max_rate") or 0)
    min_salary = float(profile.get("min_salary") or 0)
    max_salary = float(profile.get("max_salary") or 0)
    comp = row.get("compensation")
    try:
        comp_val = float(comp) if comp not in (None, "") else 0.0
    except (TypeError, ValueError):
        comp_val = 0.0
    # The offer is posted in its market's currency; the floor is in the profile's.
    # Compare in one currency (indicative rates), never show the converted figure.
    row_currency = str(row.get("currency") or "").strip().upper()
    goal_currency = str(profile.get("currency") or "EUR").strip().upper() or "EUR"
    if comp_val > 0 and row_currency and row_currency != goal_currency:
        converted = convert_money(comp_val, row_currency, goal_currency)
        comp_val = converted if converted is not None else 0.0
    track = str(row.get("track") or profile.get("track") or "jobs")
    floor = min_rate if track == "freelance" else min_salary
    ceiling = max_rate if track == "freelance" else max_salary
    if floor <= 0 or comp_val <= 0:
        pay_pct = 70.0
    elif comp_val >= floor:
        pay_pct = 100.0
    else:
        pay_pct = max(20.0, 100.0 * (comp_val / floor))
    if ceiling > 0 and comp_val > ceiling * 1.35:
        pay_pct = min(pay_pct, 65.0)

    langs = [item.lower()[:2] for item in _list(profile.get("languages"))]
    lang_hay = _tokens(description) | {str(item).lower()[:2] for item in _list(row.get("languages"))}
    lang_hits = [item for item in langs if item in lang_hay or item in hay]
    lang_pct = (100.0 * len(lang_hits) / len(langs)) if langs else 75.0

    title_pct = title_match_pct(title, profile)
    score = round(
        0.22 * title_pct
        + 0.22 * stack_pct
        + 0.18 * country_pct
        + 0.14 * pay_pct
        + 0.12 * mode_pct
        + 0.12 * lang_pct
    )
    strengths = [item.lower() for item in _list(profile.get("strengths"))]
    strength_hits = [item for item in strengths if _tokens(item) & hay]
    if strength_hits:
        score = min(100, score + min(8, 2 * len(strength_hits)))
    profile_track = str(profile.get("track") or "").strip().lower()
    if profile_track and profile_track != "both" and track and profile_track != track:
        score = max(20, score - 8)
    if country in excluded:
        score = min(score, 24)
    if is_off_role(title, profile) or requires_restricted_eligibility(row, profile):
        score = min(score, 35)
    if title_pct < 50:
        score = min(score, 48)

    reasons: list[dict[str, Any]] = []
    for item in stack_hits:
        reasons.append({"label": item, "ok": True})
    for item in stack_missing[:4]:
        reasons.append({"label": item, "ok": False})
    for item in langs:
        reasons.append({"label": item, "ok": item in lang_hits})
    for item in strength_hits[:4]:
        reasons.append({"label": item, "ok": True})
    market = MARKETS.get(country) or {}
    if country:
        reasons.append({"label": str(market.get("label") or country), "ok": country_pct >= 70})

    if score >= 85:
        bucket = "perfect"
    elif score >= 65:
        bucket = "good"
    else:
        bucket = "skip"

    out = dict(row)
    if country:
        out["country"] = country
    if description:
        out["description"] = description
    out["match_score"] = score
    out["match_reasons"] = reasons
    out["bucket"] = bucket
    if out.get("stage") in (None, "", "discovered") and bucket != "skip":
        out["stage"] = "matched"
    return out
