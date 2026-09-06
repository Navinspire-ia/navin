"""Proposed ad sets from brand + creatives. Never spend."""

from __future__ import annotations

from typing import Any

from navin.marketing.store import MarketingStore


def propose_ads(store: MarketingStore) -> dict[str, Any]:
    brand = store.load_brand()
    product = store.load_product()
    positioning = store.load_positioning()
    creatives = store.load_creatives()
    name = str(product.get("name") or brand.get("product") or "the product")
    audience = str(brand.get("audience") or positioning.get("icp") or "builders")
    countries = list(brand.get("countries") or [])
    languages = list(brand.get("languages") or ["fr", "en"])
    stills = [row for row in creatives if row.get("kind") in {"image", "banner"} and (row.get("preview") or row.get("prompt"))]
    videos = [row for row in creatives if row.get("kind") == "video"]
    campaigns = [
        {
            "id": "ads-prospect",
            "name": f"{name} - prospecting",
            "objective": "traffic",
            "audience": audience,
            "countries": countries or ["World"],
            "languages": languages,
            "placements": ["instagram", "facebook", "linkedin"],
            "creative_ids": [str(row.get("id") or "") for row in stills[:3]],
            "copy": f"{name}: {product.get('one_liner') or positioning.get('value_prop') or 'start here'}.",
            "status": "proposed",
            "spend": 0,
        },
        {
            "id": "ads-retarget",
            "name": f"{name} - retarget",
            "objective": "leads",
            "audience": audience,
            "countries": countries or ["World"],
            "languages": languages,
            "placements": ["instagram", "youtube"],
            "creative_ids": [str(row.get("id") or "") for row in (videos[:1] + stills[:2])],
            "copy": f"Still dealing with {product.get('pain') or 'manual work'}? {name} finishes it.",
            "status": "proposed",
            "spend": 0,
        },
    ]
    saved = store.save_ads({"campaigns": campaigns, "spend": 0.0})
    store.append_journal({"kind": "ads", "text": f"proposed {len(campaigns)} ad sets - spend stays 0"})
    return saved
