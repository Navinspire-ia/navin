"""Normalize tenders into one record shape."""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any
from urllib.parse import urlparse

STAGES = (
    "discovered",
    "matched",
    "scored",
    "analysed",
    "go",
    "no-go",
    "drafting",
    "validating",
    "submitted",
    "clarification",
    "shortlisted",
    "negotiation",
    "won",
    "lost",
)

SEND_MODES = ("draft", "approval", "autonomous")


def _clean(value: Any, *, limit: int = 4000) -> str:
    text = " ".join(str(value or "").replace("\u2014", "-").replace("\u2013", "-").split())
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "..."
    return text


def tender_id(*parts: str) -> str:
    raw = "|".join(_clean(p, limit=200) for p in parts if _clean(p, limit=200))
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"tn-{digest}"


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


_HOME_MARKERS = (
    "official portal",
    "official supply portal",
    "welcome to",
    "home page",
    "homepage",
    "portail officiel",
    "login to",
    "sign in to",
)

_NOTICE_MARKERS = (
    "tender",
    "appel",
    "marche",
    "marché",
    "rfp",
    "rfq",
    "notice",
    "consultation",
    "accord-cadre",
    "framework",
    "procurement",
    "fourniture",
    "prestation",
    "travaux",
    "acquisition",
    "lot ",
)


def looks_like_notice(row: dict[str, Any]) -> bool:
    """Drop portal homepages and empty scrape hits. Never invent a title."""
    title = _clean(row.get("title") or row.get("name"), limit=240)
    if not title or title.lower() == "untitled notice":
        return False
    low = title.lower()
    if any(marker in low for marker in _HOME_MARKERS):
        return False
    desc = _clean(row.get("description") or row.get("summary"), limit=4000)
    buyer = _clean(row.get("buyer") or row.get("organization"), limit=200)
    deadline = iso_date(row.get("deadline") or row.get("deadline_date"))
    ref = _clean(row.get("reference") or row.get("notice_id"), limit=80)
    blob = f"{low} {desc.lower()}"
    if any(marker in blob for marker in _NOTICE_MARKERS):
        return True
    if deadline or buyer or ref or len(desc) >= 40:
        return True
    return len(title) >= 28


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    "janv": 1, "fev": 2, "fevr": 2, "mars": 3, "avr": 4, "mai": 5, "juin": 6,
    "juil": 7, "aout": 8, "oct.": 10, "dec.": 12,
}


def _month_number(token: str) -> int | None:
    key = token.strip(". ").lower()
    key = key.replace("é", "e").replace("û", "u")
    return _MONTHS.get(key) or _MONTHS.get(key[:3])


def iso_date(value: Any) -> str:
    """YYYY-MM-DD from the date shapes official feeds emit.

    ISO first, then dd/mm/yyyy, yyyy/mm/dd, "01-Sep-2026" (World Bank),
    "Sep 1, 2026" and "1 septembre 2026". Anything else is kept as typed,
    truncated, so a scorer sees an empty deadline rather than a fake one.
    """
    text = _clean(value, limit=40)
    match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if match:
        return match.group(1)
    match = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        return f"{match.group(3)}-{int(match.group(2)):02d}-{int(match.group(1)):02d}"
    match = re.search(r"(\d{1,2})[-\s.]+([A-Za-zéû]{3,9})\.?[-\s.,]+(\d{4})", text)
    if match:
        month = _month_number(match.group(2))
        if month:
            return f"{match.group(3)}-{month:02d}-{int(match.group(1)):02d}"
    match = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", text)
    if match:
        month = _month_number(match.group(1))
        if month:
            return f"{match.group(3)}-{month:02d}-{int(match.group(2)):02d}"
    return text[:10]


def normalize_tender(raw: dict[str, Any], *, source_id: str) -> dict[str, Any]:
    title = _clean(raw.get("title") or raw.get("name"), limit=240)
    url = _clean(raw.get("source_url") or raw.get("url") or raw.get("link"), limit=500)
    ref = _clean(raw.get("reference") or raw.get("id") or raw.get("notice_id"), limit=80)
    tid = tender_id(source_id, ref or url or title)
    now = time.time()
    return {
        "id": tid,
        "source_id": source_id,
        "country": _clean(raw.get("country"), limit=16).upper() or "INTL",
        "buyer": _clean(raw.get("buyer") or raw.get("organization"), limit=200),
        "title": title or "Untitled notice",
        "description": _clean(raw.get("description") or raw.get("summary"), limit=4000),
        "sector": _clean(raw.get("sector"), limit=80),
        "cpv": _clean(raw.get("cpv") or raw.get("unspsc"), limit=80),
        "budget": float(raw["budget"]) if isinstance(raw.get("budget"), (int, float)) else None,
        "currency": _clean(raw.get("currency") or "EUR", limit=8).upper() or "EUR",
        "publication_date": iso_date(raw.get("publication_date") or raw.get("published")),
        "deadline": iso_date(raw.get("deadline") or raw.get("deadline_date")),
        "documents": list(raw.get("documents") or [])[:20],
        "eligibility": _clean(raw.get("eligibility"), limit=800),
        "submission_method": _clean(raw.get("submission_method"), limit=120),
        "source_url": url,
        "reference": ref,
        "status": _clean(raw.get("status") or "open", limit=40) or "open",
        "stage": "discovered",
        "score": None,
        "score_breakdown": {},
        "go": None,
        "go_reason": "",
        "effort_days": None,
        "analysis": {},
        "response": {},
        "mail": [],
        "history": [],
        "fetched_at": now,
        "updated_at": now,
        "favorite": bool(raw.get("favorite")),
        "archived": bool(raw.get("archived")),
        "archived_at": raw.get("archived_at") if raw.get("archived") else None,
    }
