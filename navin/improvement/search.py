# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Search adapters keep business constraints and scoring outside learned policy."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Callable

from loguru import logger

from navin.improvement.engine import ImprovementEngine, Observation


def search_criteria(criteria: dict[str, Any], policy: dict[str, str]) -> dict[str, Any]:
    scoped = dict(criteria)
    generated = {skill.casefold() for skills in criteria.get("role_skills", {}).values() for skill in skills}
    manual = [skill for skill in scoped["skills"] if skill.casefold() not in generated]
    if policy["query"] == "role_only" and scoped.get("roles"):
        scoped["skills"] = manual
    elif policy["query"] == "focused_skills" and scoped.get("roles"):
        scoped["skills"] = list(dict.fromkeys([*manual, *scoped["skills"][:3]]))
    return scoped


def career_quality(rows, criteria, side):
    from navin.career.matching import score_opportunity
    from navin.career.prospecting import score_candidate
    from navin.career.scope import offer_rejection
    from navin.career.sources import normalize_job_url

    scores, seen = [], set()
    for row in rows[:100]:
        url = normalize_job_url(str(row.get("url") or ""))
        identity = url or str(row.get("id") or "")
        if not identity or identity in seen:
            continue
        seen.add(identity)
        if criteria.get("countries") and row.get("country") and row["country"] not in criteria["countries"]:
            continue
        if side == "missions":
            if offer_rejection(row, criteria):
                continue
            if criteria["track"] != "both" and row.get("track") not in {criteria["track"], "", None, "both"}:
                continue
            score = score_opportunity(row, {
                "titles": criteria["roles"], "stack": criteria["skills"], "countries_primary": criteria["countries"],
                "track": criteria["track"], "min_rate": criteria.get("sale_rate", 0), "currency": criteria.get("currency", "EUR"),
            })["match_score"] / 100
        else:
            if row.get("currency") == criteria.get("currency") and any(
                criteria.get(limit) and float(row.get(field) or 0) > float(criteria[limit])
                for field, limit in (("daily_rate", "buy_rate_max"), ("salary", "salary_max"))
            ):
                continue
            score = score_candidate(row, {"title": " ".join(criteria["roles"]), "stack": criteria["skills"]}, criteria)["score"] / 100
        if score >= max(.7, float(criteria.get("min_score") or 70) / 100):
            scores.append(score)
    return min(1, sum(scores) / 5), bool(scores)


def career_search(store, side: str, source: str, scoped: dict[str, Any], configured: dict[str, Any], run: Callable):
    engine = ImprovementEngine(store.root / "improvement", "career")
    trial = None
    context_fields = ("domain", "roles", "skills", "role_skills", "countries", "city", "track", "mode", "role_priorities", "sources", "platforms",
                      "profile_domain", "profile_roles", "profile_skills", "profile_countries", "profile_city", "signal_only", "min_score",
                      "buy_rate_max", "salary_max", "sale_rate", "min_rate", "currency", "work_mode", "max_age_days")
    try:
        trial = engine.choose({"version": 1, **{key: configured.get(key) for key in context_fields}},
                              stratum=f"{side}:{source}:{'|'.join(scoped['roles'])}")
    except Exception as exc:  # noqa: BLE001 - keep the original search available
        logger.debug("Career improvement unavailable: {}", type(exc).__name__)
    started = time.monotonic()
    try:
        rows = run(store, source, search_criteria(scoped, trial.policy) if trial else scoped)
    except Exception:
        if trial:
            try:
                engine.observe(trial, Observation(0, False, (time.monotonic() - started) * 1000, 1))
            except Exception:  # noqa: BLE001 - preserve the original provider error
                pass
        raise
    if trial:
        try:
            # Grade using the original criteria, never the candidate's broader query.
            score, success = career_quality(rows, scoped, side)
            engine.observe(trial, Observation(score, success, (time.monotonic() - started) * 1000, 1))
        except Exception as exc:  # noqa: BLE001 - preserve successful results
            logger.debug("Career improvement observation unavailable: {}", type(exc).__name__)
    return rows


def tender_brief(brief, policy):
    if policy["terms"] == "specific_first":
        return replace(brief, terms=tuple(sorted(brief.terms, key=lambda value: -len(value.split()))))
    if policy["terms"] == "bilingual_first":
        preferred = {term.casefold(): index for index, term in enumerate(brief.search_terms)}
        terms = tuple(sorted(brief.terms, key=lambda term: preferred.get(term.casefold(), len(preferred))))
        return replace(brief, terms=terms)
    return brief
