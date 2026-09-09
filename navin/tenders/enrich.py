# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Enrich a thin notice from its official source page. Never invent text."""

from __future__ import annotations

import html
import re
from typing import Any, Callable

from loguru import logger

import urllib.request

THIN_CHARS = 240
FETCH_LIMIT = 400_000
DESC_LIMIT = 4000
CDC_LIMIT = 12_000
_TIMEOUT_S = 8
_UA = "NavinTenders/1.0 (+https://navin.ai)"

_TAG = re.compile(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>", re.I)
_SPACE = re.compile(r"\s+")
_NOISE = re.compile(
    r"javascript is disabled|enable javascript|you.?re not a robot|"
    r"cloudflare|attention required|just a moment|captcha|"
    r"access denied|verify you are human|"
    r"boamp\.fr|\{\{\s*bo_host|menu boamp|espace acheteur|espace entreprise|"
    r"bulletin officiel des annonces|detail d.un avis|"
    r"ted\.europa\.eu/en/notice|please enable cookies",
    re.I,
)

FetchFn = Callable[[str], bytes]


def _fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml,*/*"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:  # noqa: S310
        return resp.read(FETCH_LIMIT)


def notice_is_thin(tender: dict[str, Any]) -> bool:
    desc = str(tender.get("description") or "").strip()
    cdc = str(tender.get("cdc_text") or "").strip()
    if fetch_text_is_noise(desc) and (not cdc or fetch_text_is_noise(cdc)):
        return True
    return len(desc) + len(cdc) < THIN_CHARS


def html_to_text(raw: str) -> str:
    body = _TAG.sub(" ", html.unescape(raw or ""))
    return _SPACE.sub(" ", body).strip()


def fetch_text_is_noise(text: str) -> bool:
    body = str(text or "").strip()
    if not body:
        return True
    return bool(_NOISE.search(body[:1200]))


def fetch_source_text(url: str, fetch: FetchFn | None = None) -> str:
    href = str(url or "").strip()
    if not href.startswith(("http://", "https://")):
        return ""
    try:
        raw = (fetch or _fetch_bytes)(href)
    except Exception as exc:
        logger.warning("Tenders enrich skipped for {}: {}", href, exc)
        return ""
    if not raw:
        return ""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    if "<" in text[:400].lower():
        text = html_to_text(text)
    return text.strip()


def enrich_notice(tender: dict[str, Any], *, fetch: FetchFn | None = None) -> dict[str, Any]:
    """Fill description / cdc_text from source_url when the notice is title-thin."""
    row = dict(tender)
    if fetch_text_is_noise(str(row.get("description") or "")):
        row["description"] = str(row.get("title") or "").strip()
        row["enriched"] = False
    if fetch_text_is_noise(str(row.get("cdc_text") or "")):
        row["cdc_text"] = ""
    if not notice_is_thin(row):
        return row
    url = str(row.get("source_url") or "").strip()
    if not url:
        return row
    text = fetch_source_text(url, fetch=fetch)
    if len(text) < 40 or fetch_text_is_noise(text):
        return row
    current = str(row.get("description") or "").strip()
    if len(text) > len(current):
        row["description"] = text[:DESC_LIMIT]
        row["cdc_text"] = text[:CDC_LIMIT]
        row["enriched"] = True
    return row
