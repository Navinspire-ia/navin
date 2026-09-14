# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Curated client mission sources, separate from candidate directories and jobs."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

# Public discovery is not a private marketplace API. Account-only sources stay
# visible but never consume search capacity or claim an automated connection.
_SOURCES = (
    ("mon-consultant-independant", "Mon Consultant Indépendant", "FR BE CH LU", "public_listing", "https://www.mon-consultant-independant.com/missions-consultants-freelance", r"/detail-annonce-deconnecte-consultant-freelance", True),
    ("lehibou", "LeHibou", "FR", "public_search", "https://www.lehibou.com/freelance/", r"/(?:annonce|mission)s?/[^/]+", True),
    ("freelanceinformatique", "Freelance Informatique", "FR", "public_search", "https://www.freelance-informatique.fr/mission-freelance", r"/mission(?:-freelance)?(?:/|-)[^/]*\d[A-Za-z0-9]*", True),
    ("freelancermap_jobs", "freelancermap", "International", "public_search", "https://www.freelancermap.com/", r"/(?:project|projekt)/[^/]+", True),
    ("connecting_expertise", "Connecting-Expertise", "Europe BE", "account", "https://www.connecting-expertise.com/freelancers", "", True),
    ("prounity", "ProUnity", "BE NL LU", "public_listing", "https://www.pro-unity.com/freelance-missions/", r"/job/[a-f0-9-]{36}/?", True),
    ("freelancers_lu", "Freelancers.lu", "LU", "public_listing", "https://freelancers.lu/fr/missions", r"/(?:fr|en|de|lb)/missions/[a-f0-9-]{36}", True),
    ("peopleperhour_jobs", "PeoplePerHour", "International", "public_search", "https://www.peopleperhour.com/freelance-jobs", r"/freelance-jobs/(?:[^/]+/){2}[^/]+-\d+", True),
    ("upwork_jobs", "Upwork", "International", "public_search", "https://www.upwork.com/nx/search/jobs/", r"/(?:jobs/[^/]*~[a-zA-Z0-9]+|freelance-jobs/apply/[^/]+)", True),
    ("freelancer_jobs", "Freelancer.com", "International", "public_search", "https://www.freelancer.com/jobs/", r"/projects/[^/]+/[^/]+", True),
    ("freelancer_au", "Freelancer Australia", "AU", "public_search", "https://www.freelancer.com.au/jobs/", r"/projects/[^/]+/[^/]+", True),
    ("expert360", "Expert360", "AU NZ", "account", "https://expert360.com/", "", True),
    ("malt_missions", "Malt", "Europe", "account", "https://www.malt.fr/", "", True),
    ("comet", "Comet", "FR", "account", "https://www.comet.co/", "", False),
    ("freelance_com", "Freelance.com", "International", "account", "https://www.freelance.com/", "", False),
    ("gulp", "GULP", "DE CH AT", "public_search", "https://www.gulp.de/", r"/projekt/[^/]+", False),
    ("yunojuno", "YunoJuno", "GB", "account", "https://www.yunojuno.com/", "", True),
    ("toptal", "Toptal", "International", "account", "https://www.toptal.com/", "", False),
    ("workhoppers_jobs", "Workhoppers", "CA", "public_search", "https://www.workhoppers.com/", r"/(?:en|fr)/job/\d+", False),
)


def mission_platform_catalog() -> list[dict[str, Any]]:
    return [{"id": sid, "name": name, "markets": markets, "mode": mode,
             "provider": "account" if mode == "account" else "", "url": url,
             "domain": (urlsplit(url).hostname or "").removeprefix("www."),
             "detail_pattern": pattern, "priority": 1 if primary else 2,
             "opportunity_kind": "freelance"}
            for sid, name, markets, mode, url, pattern, primary in _SOURCES]


def platform_by_id(source: str) -> dict[str, Any] | None:
    return next((row for row in mission_platform_catalog() if row["id"] == source), None)


def is_mission_url(url: str, source: dict[str, Any]) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").removeprefix("www.")
        return (parsed.scheme == "https" and not parsed.username and not parsed.password
                and (host == source["domain"] or host.endswith("." + source["domain"]))
                and bool(source["detail_pattern"])
                and bool(re.fullmatch(source["detail_pattern"] + r"/?", parsed.path)))
    except ValueError:
        return False


def indexed_missions(source: dict[str, Any], hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep individual client briefs, with provenance and no inferred salary/date."""
    from navin.career.normalize import enrich_facts
    from navin.career.scope import publication_day
    from navin.career.sources import clean_job_text, stable_job_id

    rows = []
    seen = set()
    for hit in hits:
        url = str(hit.get("url") or "")
        title = clean_job_text(str(hit.get("title") or "")).strip()
        description = clean_job_text(str(hit.get("snippet") or "")).strip()
        if not title or url in seen or not is_mission_url(url, source):
            continue
        # A mission path alone cannot override an explicitly salaried contract.
        if re.search(r"\b(?:CDI|permanent|internship|apprenticeship|stage|alternance)\b", f"{title} {description}", re.I):
            continue
        seen.add(url)
        row = {"id": stable_job_id(source["id"], url, title), "source": source["id"], "url": url,
               "title": title, "description": description, "company": "", "country": "", "location": "",
               "track": "freelance", "contracts": ["contractor"], "opportunity_kind": "freelance",
               "need_type": "client_project", "stage": "discovered", "stack": [], "deadline": "",
               "posted_at": publication_day(hit.get("posted_at") or hit.get("date")),
               "attribution": source["name"] + " - annonces publiques indexées", "ingest": "search_snippet"}
        rows.append(enrich_facts(row))
    return rows
