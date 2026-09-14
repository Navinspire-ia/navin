"""Evaluate captured page evidence against Career criteria without saving records."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from navin.browser_import import clean_record
from navin.career.errors import CareerError
from navin.career.normalize import (
    contracts_track,
    enrich_facts,
    normalize_contracts,
    pay_from_description,
)
from navin.career.prospecting import _state, score_candidate
from navin.career.scope import candidate_countries, offer_rejection, result_countries
from navin.career.search_plan import role_scope, role_shares
from navin.career.store import CareerStore
from navin.career.vocabulary import has_term, role_present, skill_present

# Only matching preferences are exposed, never the profile, company, mail or stored candidates.
CRITERIA_FIELDS = (
    "mode", "roles", "skills", "domain", "countries", "city", "track", "work_mode",
    "profile_roles", "profile_skills", "profile_domain", "profile_countries", "profile_city",
    "sale_rate", "sale_rate_remote", "sale_rate_onsite", "min_rate", "min_project_budget",
    "buy_rate_max", "salary_max", "currency", "min_score", "max_age_days", "signal_only",
    "role_skills", "role_priorities",
)


def _context(criteria: dict[str, Any]) -> dict[str, Any]:
    safe = {key: criteria[key] for key in CRITERIA_FIELDS if key in criteria}
    explicit = criteria.get("autofill", {}).get("version") == 1
    profile = {**safe, "roles": criteria.get("profile_roles", []),
               "skills": criteria.get("profile_skills", []) if explicit else criteria.get("profile_skills") or criteria.get("skills", []),
               "domain": criteria.get("profile_domain", "") if explicit else criteria.get("profile_domain") or criteria.get("domain", ""),
               "countries": candidate_countries(criteria),
               "city": criteria.get("profile_city") or criteria.get("city", "")}
    revision = hashlib.sha256(json.dumps([safe, profile], sort_keys=True).encode()).hexdigest()
    return {"criteria": safe, "profiles": profile, "revision": revision,
            "configured": bool(any(safe.get(key) or profile.get(key) for key in ("roles", "skills", "domain")))}


def _reason(key: str, status: str, detail: str) -> dict[str, str]:
    return {"key": key, "status": status, "detail": detail}


def _candidate(row: dict[str, Any], criteria: dict[str, Any]) -> tuple[str, int, list[dict[str, str]]]:
    text = " ".join([row["headline"], row["description"], " ".join(row["skills"])])
    roles = criteria.get("roles", [])
    matched_roles = [role for role in roles if role_present(text, role)]
    # Roles are alternatives: apply the skills belonging to the matching role.
    scopes = [role_scope(criteria, role) for role in matched_roles] or [criteria]
    criteria = min(scopes, key=lambda scope: sum(not skill_present(text, skill) for skill in scope.get("skills", [])))
    candidate = {"headline": row["headline"], "snippet": row["description"], "skills": row["skills"],
                 "country": row["country"], "city": row["location"]}
    scored = score_candidate(candidate, {}, criteria)
    reasons = []
    if roles:
        reasons.append(_reason("role", "match" if matched_roles else "unknown" if not row["headline"] else "mismatch",
                               ", ".join(matched_roles or roles)))
    if criteria.get("skills"):
        missing = [skill for skill in criteria["skills"] if not skill_present(text, skill)]
        reasons.append(_reason("skills", "unknown" if missing else "match", ", ".join(missing or criteria["skills"])))
    if criteria.get("domain"):
        reasons.append(_reason("domain", "match" if has_term(text, criteria["domain"]) else "unknown", criteria["domain"]))
    countries = result_countries(row)
    if criteria.get("countries"):
        status = "match" if countries & set(criteria["countries"]) else "mismatch" if countries else "unknown"
        reasons.append(_reason("country", status, ", ".join(sorted(countries) if countries else criteria["countries"])))
    if criteria.get("city"):
        reasons.append(_reason("city", "match" if has_term(row["location"], criteria["city"]) else "unknown", criteria["city"]))
    ceiling = float(criteria.get("buy_rate_max") or 0)
    if ceiling:
        rates = [row[key] for key in ("daily_rate_min", "daily_rate_max") if row.get(key) is not None]
        known = bool(rates) and row["currency"] == criteria.get("currency", "EUR")
        status = "match" if known and max(rates) <= ceiling else "mismatch" if known else "unknown"
        reasons.append(_reason("buy_rate", status, f"Maximum {ceiling:g} {criteria.get('currency', 'EUR')}/jour"))
    salary_ceiling = float(criteria.get("salary_max") or 0)
    if salary_ceiling:
        salaries = [row[key] for key in ("salary_min", "salary_max") if row.get(key) is not None]
        known = bool(salaries) and row["currency"] == criteria.get("currency", "EUR")
        status = "match" if known and max(salaries) <= salary_ceiling else "mismatch" if known else "unknown"
        reasons.append(_reason("salary", status, f"Maximum {salary_ceiling:g} {criteria.get('currency', 'EUR')}/an"))
    if criteria.get("signal_only"):
        # An availability mention is an observation, never a confirmed status.
        note = row.get("availability", "")
        reasons.append(_reason("availability", "unknown", note or "Disponibilité non renseignée, à confirmer"))
    blockers = [reason for reason in reasons if reason["status"] == "mismatch"]
    unknown = [reason for reason in reasons if reason["status"] == "unknown" and reason["key"] != "availability"]
    status = "excluded" if blockers else "review" if unknown or scored["score"] < float(criteria.get("min_score") or 0) else "recommended"
    return status, scored["score"], reasons


def _record(raw: dict[str, Any], context: dict[str, Any], candidates: set[str], offers: set[str], seen: set[tuple[str, str]]) -> dict[str, Any]:
    identity = str(raw.get("request_id") or "")
    row = clean_record(raw, require_review=False)
    kind = row["kind"]
    if row["capture_mode"] in {"page", "selection"} and kind in {"mission", "job"}:
        contracts = normalize_contracts(row["title"], row["description"])
        if contracts:
            kind = "job" if contracts_track(contracts, context["criteria"].get("track", "")) == "jobs" else "mission"
            row["kind"] = kind
    criteria = context["profiles"] if kind == "candidate" else context["criteria"]
    configured_roles = criteria.get("roles", [])
    active_roles = [role for role, weight in role_shares(configured_roles, criteria.get("role_priorities", {})).items() if weight > 0]
    criteria = {**criteria, "roles": active_roles}
    text = " ".join([row["headline"], row["title"], row["description"]])
    if kind in {"candidate", "mission", "job"}:
        facts = enrich_facts({**row, "title": row["headline"] if kind == "candidate" else row["title"],
                              "track": "jobs" if kind == "job" else "freelance"})
        # Country defaults are not evidence of the advertised currency.
        observed_pay = pay_from_description(row["description"], track="jobs" if kind == "job" else "freelance") or {}
        facts["currency"] = row["currency"] or observed_pay.get("currency", "")
        for key in ("daily_rate_min", "daily_rate_max", "salary_min", "salary_max", "currency", "remote", "need_type"):
            if not row.get(key) and facts.get(key):
                row[key] = facts[key]
        countries = result_countries(row)
        if not row["country"] and len(countries) == 1:
            row["country"] = next(iter(countries))
        row["skills"] = list(dict.fromkeys([*row["skills"], *[s for s in criteria.get("skills", []) if skill_present(text, s)]]))
    reasons: list[dict[str, str]] = []
    score = None
    if kind == "candidate":
        status, score, reasons = _candidate(row, criteria)
    elif kind in {"mission", "job"}:
        failure = offer_rejection({**row, "stack": row["skills"], "track": "jobs" if kind == "job" else "freelance"}, criteria)
        unknown = failure == "country" and not result_countries(row) or failure == "city" and not row["location"] or failure == "skills" or failure.endswith("_unknown")
        status = "review" if unknown else "excluded" if failure else "recommended"
        reasons = [_reason(failure or "criteria", "unknown" if status == "review" else "mismatch" if failure else "match",
                           "Critères Navin respectés" if not failure else failure)]
    else:
        status = "review"
        reasons = [_reason("manual", "unknown", "Les critères Carrière ne s'appliquent pas à cette destination")]
    mode = context["criteria"].get("mode", "both")
    if (kind == "candidate" and mode == "missions") or (kind in {"job", "mission"} and mode == "profiles"):
        status = "excluded"
        reasons.append(_reason("mode", "mismatch", "Type de fiche hors de la recherche active dans Navin"))
    if not context["configured"] and kind in {"candidate", "mission", "job"}:
        status = "review"
        reasons.append(_reason("configuration", "unknown", "Définissez vos métiers ou compétences dans Carrière"))
    if configured_roles and not active_roles and kind in {"candidate", "mission", "job"}:
        status = "excluded"
        reasons.append(_reason("configuration", "mismatch", "Tous les métiers de cette recherche sont en pause dans Navin"))
    duplicate = row["url"] in (candidates if kind == "candidate" else offers if kind in {"mission", "job"} else set()) or (kind, row["url"]) in seen
    seen.add((kind, row["url"]))
    if duplicate:
        status = "duplicate"
        reasons.append(_reason("duplicate", "match", "Cette fiche est déjà dans Navin ou dans la sélection"))
    return {"request_id": identity, "record": {**row, "request_id": identity}, "status": status,
            "score": score, "reasons": reasons, "destination": "candidates" if kind == "candidate" else "career" if kind in {"mission", "job"} else kind}


def analyze_records(records: list[dict[str, Any]] | None = None, *, roots: dict[str, Path] | None = None) -> dict[str, Any]:
    store = CareerStore((roots or {}).get("career"))
    try:
        with FileLock(str(store.root / "lifecycle.lock"), timeout=0):
            state = _state(store)
            context = _context(state["criteria"])
            if records is None:
                return context
            candidates = {row.get("url", "") for row in state["candidates"]}
            offers = {row.get("url", "") for row in store.load_opportunities()}
            seen: set[tuple[str, str]] = set()
            results = []
            for raw in records:
                try:
                    results.append(_record(raw, context, candidates, offers, seen))
                except CareerError as exc:
                    results.append({"request_id": str(raw.get("request_id") or ""), "status": "invalid", "error": exc.message})
            return {**context, "results": results}
    except Timeout:
        raise CareerError("Carrière est occupé. Réessayez l'analyse dans un instant.", status=409) from None
