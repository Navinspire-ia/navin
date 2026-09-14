# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Bounded public card and detail readers for ProUnity and Freelancers.lu."""

from __future__ import annotations

import re
import threading
import time
import urllib.request
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from navin.career.errors import CareerError
from navin.career.http_pacing import pace_public_request
from navin.career.jsonld import extract_jobpostings, jobposting_rows
from navin.career.mission_platforms import is_mission_url
from navin.career.normalize import enrich_facts, normalize_remote
from navin.career.scope import publication_day
from navin.career.sources import clean_job_text, infer_country_iso, stable_job_id

_cache: dict[str, tuple[float, str]] = {}
_locks = {"prounity": threading.Lock(), "freelancers_lu": threading.Lock()}


def _fetch(url: str, source: dict[str, Any]) -> str:
    with _locks[source["id"]]:
        now = time.monotonic()
        cached = _cache.get(url)
        if cached and now - cached[0] < 600:
            return cached[1]
        pace_public_request(source["id"], 1.0)

        class SameHost(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                parsed = urlsplit(newurl)
                if parsed.scheme != "https" or (parsed.hostname or "").removeprefix("www.") != source["domain"]:
                    raise CareerError("Public mission page redirected outside its source.")
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        request = urllib.request.Request(url, headers={"User-Agent": "NavinCareer/1.0", "Accept": "text/html"})
        try:
            with urllib.request.build_opener(SameHost).open(request, timeout=15) as response:
                body = response.read(4_000_001)
            if len(body) > 4_000_000:
                raise CareerError("Public mission page exceeds the size limit.")
        except OSError:
            raise CareerError(source["name"] + " public listings are temporarily unavailable.") from None
        html = body.decode("utf-8", errors="replace")
        # Keep the in-process cache bounded across long-running daily searches.
        if len(_cache) >= 128:
            _cache.pop(next(iter(_cache)))
        _cache[url] = (now, html)
        return html


def parse_cards(body: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    soup = BeautifulSoup(body, "html.parser")
    rows = []
    seen = set()
    for anchor in soup.select("a[href]"):
        url = urljoin(source["url"], str(anchor["href"]))
        if url in seen or not is_mission_url(url, source):
            continue
        card = anchor.find_parent(class_="job") if source["id"] == "prounity" else None
        title_node = card.select_one(".job__title") if card else anchor
        title = clean_job_text(title_node.get_text(" ", strip=True)) if title_node else ""
        if not title or title.lower() in {"read more", "voir les détails"}:
            continue
        seen.add(url)
        row = {"id": stable_job_id(source["id"], url, title), "source": source["id"], "url": url,
               "title": title, "description": "", "company": "", "country": "", "location": "",
               "track": "freelance", "contracts": ["contractor"], "opportunity_kind": "freelance",
               "need_type": "consulting", "stage": "discovered", "stack": [], "posted_at": "", "deadline": "",
               "attribution": source["name"] + " - annonces publiques", "ingest": "public_listing"}
        if card:
            company = card.select_one("[data-filter-company]")
            date = card.select_one(".job__pubdate")
            row.update(company=clean_job_text(company.get_text(" ", strip=True)) if company else "",
                       posted_at=publication_day(date.get_text(strip=True)) if date else "",
                       description=clean_job_text(card.get_text(" ", strip=True)))
        rows.append(enrich_facts(row))
    if not rows:
        raise CareerError(source["name"] + " listing is empty or its format changed; no verified missions.")
    return rows


def enrich_detail(row: dict[str, Any], body: str) -> dict[str, Any]:
    soup = BeautifulSoup(body, "html.parser")
    if row["source"] == "freelancers_lu":
        details = jobposting_rows(body, page_url=row["url"], source=row["source"], track="freelance")
        if not details:
            raise CareerError("Freelancers.lu mission details are unavailable.")
        raw = extract_jobpostings(body)[0]
        row = {**row, **details[0], "id": row["id"], "url": row["url"],
               "deadline": publication_day(raw.get("validThrough"))}
        main = soup.select_one("main")
        if main:
            row["remote"] = normalize_remote(main.get_text(" ", strip=True)[:500])
    else:
        article = soup.select_one("article.pji_job")
        if not article:
            raise CareerError("ProUnity mission details are unavailable.")
        info = article.select_one(".job__infobar")
        info_text = clean_job_text(info.get_text(" ", strip=True)) if info else ""
        # The date range here is the assignment period, not the application deadline.
        dates = re.findall(r"\d{2}/\d{2}/\d{4}", info_text)
        location = info_text.split(dates[-1], 1)[-1].strip() if dates else ""
        row.update(description=clean_job_text(article.get_text(" ", strip=True))[:12000],
                   location=location, country=infer_country_iso("", location),
                   start_date=publication_day(dates[0]) if dates else "",
                   end_date=publication_day(dates[1]) if len(dates) > 1 else "")
    return enrich_facts(row)


def search_missions(source: dict[str, Any], *, titles: list[str], countries: list[str]) -> list[dict[str, Any]]:
    from navin.career.matching import job_is_relevant

    cards = parse_cards(_fetch(source["url"], source), source)
    relevant = [row for row in cards if not any(titles) or job_is_relevant(row, {"titles": titles})]
    rows = []
    errors = []
    for card in relevant[:6]:
        try:
            row = enrich_detail(card, _fetch(card["url"], source))
        except CareerError as exc:
            errors.append(exc)
            continue
        if row.get("track") == "jobs":
            continue
        if not countries or row.get("country") in countries:
            rows.append(row)
    if errors and not rows:
        raise errors[0]
    return rows
