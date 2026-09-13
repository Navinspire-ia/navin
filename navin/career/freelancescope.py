# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Read public FreelanceScope mission cards; never ingest homepage examples."""

from __future__ import annotations

import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from bs4 import BeautifulSoup

from navin.career.errors import CareerError
from navin.career.jsonld import jobposting_rows
from navin.career.sources import stable_job_id

BASE = "https://www.freelancescope.fr"


def parse_missions(body: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(body, "html.parser")
    rows = []
    seen = set()
    for anchor in soup.select("h3 a[href]"):
        path = str(anchor.get("href") or "")
        if not path.startswith("/missions/") or path.startswith("/missions/categorie/") or path in seen:
            continue
        seen.add(path)
        card = anchor.find_parent("article")
        if card is None:
            continue
        title = anchor.get_text(" ", strip=True)
        description = card.get_text(" ", strip=True)
        company_line = anchor.parent.parent.find("p")
        company = company_line.get_text(" ", strip=True).split("·")[0].strip() if company_line else ""
        location = card.select_one("ul li")
        skills = [s.get_text(" ", strip=True) for s in card.select('[data-slot="badge"]')]
        published = card.find("time")
        url = BASE + path
        rows.append({"id": stable_job_id("freelancescope", url, title), "source": "freelancescope",
                     "title": title, "description": description, "company": company,
                     "location": location.get_text(" ", strip=True).split("·")[0].strip() if location else "",
                     "stack": skills, "published_at": published.get("datetime", "") if published else "",
                     "url": url, "country": "FR", "track": "freelance", "stage": "discovered",
                     "attribution": "FreelanceScope - catalogue public", "ingest": "public_card"})
    return rows


def _fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Navin-Career/1.0", "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read(3_000_001)
    if len(body) > 3_000_000:
        raise CareerError("FreelanceScope catalog exceeds the size limit.")
    return body.decode("utf-8", errors="replace")


def enrich_mission(row: dict[str, Any], body: str) -> dict[str, Any]:
    details = jobposting_rows(body, page_url=row["url"], source="freelancescope", track="freelance")
    if not details:
        return row
    detail = details[0]
    links = [{"url": row["url"], "source": "freelancescope", "id": row["id"]}]
    soup = BeautifulSoup(body, "html.parser")
    for anchor in soup.find_all("a", href=True):
        url = str(anchor["href"])
        if url.startswith("https://candidat.francetravail.fr/offres/recherche/detail/"):
            links.append({"url": url, "source": "francetravail", "id": ""})
    return {**row, **detail, "id": row["id"], "location": row["location"], "source_links": links}


def search_missions(query: str) -> list[dict[str, Any]]:
    body = _fetch(BASE + "/missions?" + urllib.parse.urlencode({"q": query[:250]}))
    rows = parse_missions(body)
    if not rows and "Catalogue de missions" not in body and "aucune mission" not in body.lower():
        raise CareerError("FreelanceScope catalog is unavailable or its format changed.")
    # Bound detail requests. The remaining cards are still usable search results.
    def detail(row):
        try:
            return enrich_mission(row, _fetch(row["url"]))
        except (OSError, ValueError, CareerError):
            return row
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows[:4] = list(pool.map(detail, rows[:4]))
    return rows
