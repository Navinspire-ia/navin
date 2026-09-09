# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""SEO book from harvest + research. No invented rankings."""

from __future__ import annotations

from typing import Any

from navin.marketing.store import MarketingStore


def build_seo(store: MarketingStore) -> dict[str, Any]:
    product = store.load_product()
    harvest = store.load_harvest()
    research = store.load_research()
    positioning = store.load_positioning()
    keywords = list(
        dict.fromkeys(
            [
                *[str(item).strip() for item in (harvest.get("keywords") or []) if str(item).strip()],
                *[str(item).strip() for item in (research.get("keywords") or []) if str(item).strip()],
                str(product.get("name") or "").strip(),
                str(product.get("category") or "").strip(),
                str(positioning.get("pain") or product.get("pain") or "").strip(),
            ]
        )
    )
    keywords = [item for item in keywords if item][:24]
    pages: list[dict[str, Any]] = []
    site = str(product.get("site") or harvest.get("site") or "").strip()
    if site:
        pages.append(
            {
                "url": site,
                "title": harvest.get("title") or product.get("name") or "Home",
                "description": harvest.get("one_liner") or product.get("one_liner") or "",
                "kind": "home",
            }
        )
    for page in harvest.get("pages") or []:
        if not isinstance(page, dict) or not page.get("url"):
            continue
        pages.append(
            {
                "url": page.get("url"),
                "title": page.get("title") or page.get("path") or page.get("url"),
                "description": page.get("description") or "",
                "kind": page.get("kind") or "inner",
            }
        )
    current = store.load_seo()
    saved = store.save_seo({"keywords": keywords, "pages": pages, "rankings": current.get("rankings") or []})
    store.append_journal({"kind": "seo", "text": f"seo book: {len(pages)} pages, {len(keywords)} keywords"})
    return saved


def ingest_ranking(store: MarketingStore, row: dict[str, Any]) -> dict[str, Any]:
    """Record a measured ranking. Never invents position or traffic."""
    keyword = str(row.get("keyword") or row.get("query") or "").strip()
    url = str(row.get("url") or "").strip()
    if not keyword or not url:
        from navin.marketing.errors import MarketingError

        raise MarketingError("keyword and url are required", status=400)
    try:
        position = int(row.get("position") or row.get("rank") or 0)
    except (TypeError, ValueError):
        from navin.marketing.errors import MarketingError

        raise MarketingError("position must be an integer", status=400)
    if position < 1:
        from navin.marketing.errors import MarketingError

        raise MarketingError("position must be 1 or more", status=400)
    current = store.load_seo()
    rankings = [item for item in (current.get("rankings") or []) if isinstance(item, dict)]
    key = (keyword.lower(), url.lower())
    rankings = [
        item
        for item in rankings
        if (str(item.get("keyword") or "").lower(), str(item.get("url") or "").lower()) != key
    ]
    rankings.insert(0, {"keyword": keyword, "url": url, "position": position, "source": "ingested"})
    saved = store.save_seo({"rankings": rankings[:40]})
    store.append_journal({"kind": "seo", "text": f"ranking ingested: {keyword} #{position}"})
    return saved
