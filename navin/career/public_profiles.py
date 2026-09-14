# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Public Freelancers.lu profiles, with observed rates and residence evidence."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup

from navin.career.errors import CareerError
from navin.career.mission_platforms import platform_by_id
from navin.career.prospecting_catalog import is_profile_url, platform_catalog
from navin.career.public_missions import _fetch
from navin.career.sources import clean_job_text, infer_country_iso
from navin.career.vocabulary import role_present


def parse_lu_profiles(body: str) -> list[dict[str, Any]]:
    platform = next(row for row in platform_catalog() if row["id"] == "freelancers_lu")
    soup = BeautifulSoup(body, "html.parser")
    rows = []
    for card in soup.select("a[href]"):
        url = urljoin("https://freelancers.lu", str(card["href"]))
        heading = card.find("h3")
        if not heading or not is_profile_url(url, platform):
            continue
        name = clean_job_text(str(card.get("aria-label") or "")).strip()
        headline = clean_job_text(heading.get_text(" ", strip=True))
        city = card.select_one("span[class*='max-w-']")
        location = city.get_text(" ", strip=True) if city else ""
        skills = [s.get_text(" ", strip=True) for s in card.select(".li-pill") if not s.get_text(strip=True).startswith("+")]
        rows.append({"url": url, "name": name, "headline": headline, "title": name + " - " + headline,
                     "location": location, "country": infer_country_iso("", location), "skills": skills,
                     "snippet": clean_job_text(card.get_text(" ", strip=True))[:2000]})
    if not rows and not soup.select_one("main"):
        raise CareerError("Freelancers.lu public profile directory is unavailable.")
    return rows


def enrich_lu_profile(row: dict[str, Any], body: str) -> dict[str, Any]:
    soup = BeautifulSoup(body, "html.parser")
    heading, main = soup.find("h1"), soup.find("main")
    if not heading or not main:
        return row
    paragraphs = heading.parent.find_all("p", recursive=False)
    location_node = paragraphs[1].find("span") if len(paragraphs) > 1 else None
    location = location_node.get_text(" ", strip=True) if location_node else row["location"]
    skills_heading = next((h for h in main.find_all("h2") if h.get_text(strip=True) == "Compétences"), None)
    skills = [s.get_text(" ", strip=True) for s in skills_heading.parent.select(".li-pill")] if skills_heading else []
    return {**row, "location": location, "country": infer_country_iso("", location), "skills": skills or row["skills"],
            "snippet": clean_job_text(main.get_text(" ", strip=True))[:4000] + " " + row["snippet"]}


def search_lu_profiles(criteria: dict[str, Any]) -> list[dict[str, Any]]:
    source = platform_by_id("freelancers_lu")
    query = " ".join(criteria.get("roles") or criteria.get("skills", [])[:3])
    url = "https://freelancers.lu/fr/freelance?" + urlencode({"search": query[:200]})
    rows = parse_lu_profiles(_fetch(url, source))
    if criteria.get("roles"):
        rows = [row for row in rows if any(role_present(row["headline"], role) for role in criteria["roles"])]
    for index, row in enumerate(rows[:4]):
        try:
            rows[index] = enrich_lu_profile(row, _fetch(row["url"], source))
        except CareerError:
            pass
    return rows[:20]
