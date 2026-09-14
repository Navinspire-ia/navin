# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Read the same anonymous Softr listing endpoint as the public MCI page."""

from __future__ import annotations

import copy
import json
import re
import threading
import time
import urllib.request
from typing import Any

from navin.career.errors import CareerError
from navin.career.http_pacing import pace_public_request
from navin.career.normalize import duration_from_text, normalize_experience, normalize_remote
from navin.career.scope import publication_day
from navin.career.sources import clean_job_text, html_to_text, infer_country_iso, stable_job_id

SOURCE = "mon-consultant-independant"
BASE = "https://www.mon-consultant-independant.com"
LISTING = BASE + "/missions-consultants-freelance"
DETAIL = BASE + "/detail-annonce-deconnecte-consultant-freelance"
_LOCK = threading.Lock()
_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])


def _fetch(url: str, payload: dict[str, Any] | None = None) -> str:
    pace_public_request(SOURCE, 1.0)
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode() if payload is not None else None,
        headers={"User-Agent": "NavinCareer/1.0", "Accept": "application/json, text/html",
                 "Content-Type": "application/json", "Referer": LISTING},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read(4_000_001)
        if len(body) > 4_000_000:
            raise CareerError("MCI public listing exceeds the size limit.")
        return body.decode("utf-8", errors="replace")
    except OSError:
        raise CareerError("MCI public listing is unavailable. Other sources will continue.") from None


def listing_endpoint(body: str) -> tuple[str, str]:
    """Discover public block identifiers without evaluating JavaScript or mockData."""
    try:
        marker = re.search(r"\bvar\s+softrBlocks\s*=\s*", body)
        if not marker:
            raise ValueError("No public blocks")
        blocks, _ = json.JSONDecoder().raw_decode(body[marker.end():])
        app = re.search(r'data-appid=["\']([a-f0-9-]+)["\']', body, re.I)[1]
        page = re.search(r'data-pageid=["\']([a-f0-9-]+)["\']', body, re.I)[1]
        for block in blocks:
            collection = block.get("collection") or {}
            source = collection.get("dataSource") or {}
            if (source.get("airtable") or {}).get("tableName") != "Annonces":
                continue
            ids = [app, page, block["id"], source["id"]]
            if not all(re.fullmatch(r"[a-f0-9-]{36}", value) for value in ids):
                raise ValueError("Invalid public block identifiers")
            sort = collection["sortOptions"][0]["field"]
            return BASE + "/v1/datasource/airtable/" + "/".join(ids) + "/data", sort
    except (ValueError, TypeError, KeyError, IndexError):
        pass
    raise CareerError("MCI public listing format changed. No missions were imported.")


def parse_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise CareerError("Unexpected MCI public listing response.")
    rows = []
    for record in payload["records"]:
        if not isinstance(record, dict) or not isinstance(record.get("fields"), dict):
            continue
        fields = record["fields"]
        def text(key: str) -> str:
            return clean_job_text(html_to_text(str(fields.get(key) or ""))).strip()

        rid = str(record.get("id") or "")
        title = text("Titre")
        if not re.fullmatch(r"rec[a-zA-Z0-9]+", rid) or not title or text("Etat") != "Ouverte aux candidatures":
            continue
        url = DETAIL + "?recordId=" + rid
        location = " ".join(filter(None, [text("Lieu"), text("Précision lieu mission")]))
        published = re.search(r"\d{2}/\d{2}/\d{4}", text("Affichage date de publication"))
        start = re.search(r"\d{2}/\d{2}/\d{4}", text("Début mission affichage Softr"))
        # The raw 'TJM ' field differs from the public consultant rate.
        # A displayed ceiling is never promoted to a guaranteed minimum.
        pay = text("TJM affichage Softr")
        ceiling = re.search(r"max\s+([\d\s]+(?:[.,]\d+)?)\s*€", pay, re.I)
        rate = float(re.sub(r"\s", "", ceiling[1]).replace(",", ".")) if ceiling else None
        mode = text("Télétravail")
        remote_days = re.search(r"\b([1-5])\s*j\b", mode)
        remote = ("remote" if remote_days[1] == "5" else "hybrid") if remote_days else normalize_remote(mode)
        if "discuter" in mode.lower() or "aménageable" in mode.lower():
            remote = ""
        months, duration = duration_from_text(text("Durée"))
        level, years = normalize_experience(text("Expérience min") + " ans")
        rows.append({
            "id": stable_job_id(SOURCE, url, title), "source": SOURCE, "url": url,
            "title": title, "description": text("Description")[:12000], "company": "",
            "country": infer_country_iso("", location), "location": location,
            "track": "freelance", "contracts": ["contractor"], "opportunity_kind": "freelance",
            "need_type": "consulting", "stage": "discovered", "status": "open",
            "stack": list(dict.fromkeys(filter(None, [text(key) for key in (
                "Compétence principale attendue", "Critere_1", "Critere_2", "Critere_3")]))),
            "posted_at": publication_day(published[0] if published else record.get("createdTime")),
            "start_date": publication_day(start[0]) if start else "", "deadline": "",
            "daily_rate_min": None, "daily_rate_max": rate, "compensation": rate,
            "currency": "EUR" if rate is not None else "", "pay_period": "day" if rate is not None else "",
            "pay_text": pay, "duration": duration or text("Durée"), "duration_months": months,
            "remote": remote, "remote_evidence": mode, "seniority": level, "experience_years_min": years,
            "attribution": "Mon Consultant Indépendant - annonces publiques", "ingest": "public_listing",
        })
    return rows


def search_missions(*, titles: list[str], countries: list[str], track: str = "freelance") -> list[dict[str, Any]]:
    if track == "jobs":
        return []
    global _cache
    # Parallel roles/countries share one bounded listing read, cached for ten minutes.
    with _LOCK:
        if time.monotonic() - _cache[0] > 600 or not _cache[0]:
            endpoint, sort = listing_endpoint(_fetch(LISTING))
            rows: dict[str, dict[str, Any]] = {}
            offset = None
            seen_offsets = set()
            for _ in range(3):
                payload = json.loads(_fetch(endpoint, {
                    "options": {"cellFormat": "string", "timeZone": "UTC", "userLocale": "en-US"},
                    "pageContext": None, "filterCriteria": {},
                    "pagingOption": {"offset": offset, "count": 100},
                    "sortingOption": {"sortingField": sort, "sortType": "DESC"},
                }))
                rows.update({row["id"]: row for row in parse_records(payload)})
                offset = payload.get("offset")
                if not isinstance(offset, str) or not offset or offset in seen_offsets:
                    break
                seen_offsets.add(offset)
            _cache = (time.monotonic(), list(rows.values()))
        rows = copy.deepcopy(_cache[1])
    from navin.career.matching import job_is_relevant
    return [row for row in rows if (not countries or row["country"] in countries)
            and (not any(titles) or job_is_relevant(row, {"titles": titles}))]
