# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Single social pack: four networks, one still and one clip each.

Produce, briefs, calendar and Studio all read this file. Nothing is published.
"""

from __future__ import annotations

from typing import Any

SOCIAL_CHANNELS = ("linkedin", "facebook", "instagram", "tiktok")

# channel, kind, placement, aspect
STILLS: tuple[tuple[str, str, str, str], ...] = (
    ("linkedin", "image", "linkedin post", "1:1"),
    ("facebook", "image", "facebook post", "1:1"),
    ("instagram", "image", "instagram post", "4:5"),
    ("tiktok", "image", "tiktok cover", "9:16"),
)
CLIPS: tuple[tuple[str, str, str, str], ...] = (
    ("linkedin", "video", "linkedin clip", "16:9"),
    ("facebook", "video", "facebook clip", "1:1"),
    ("instagram", "video", "instagram reel", "9:16"),
    ("tiktok", "video", "tiktok clip", "9:16"),
)
BRAND: tuple[tuple[str, str, str], ...] = (
    ("image", "brand lockup", "1:1"),
    ("image", "og / blog header", "16:9"),
)
AUDIO: tuple[tuple[str, str, str], ...] = (("audio", "15s voice over", ""),)

SITE_PLACEMENTS = frozenset({"site logo", "site og", "site image"})


def _kind_rows(rows: tuple[tuple[str, str, str, str], ...]) -> tuple[tuple[str, str, str], ...]:
    return tuple((kind, placement, aspect) for _channel, kind, placement, aspect in rows)


POST_PACK = _kind_rows(STILLS)
VIDEO_PACK = _kind_rows(CLIPS)
IMAGE_PACK = BRAND + POST_PACK
SOCIAL_PACK = POST_PACK + VIDEO_PACK

PACKS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "brand": BRAND,
    "posts": POST_PACK,
    "social": SOCIAL_PACK,
    "image": IMAGE_PACK,
    "video": VIDEO_PACK,
    "clips": VIDEO_PACK,
    "audio": AUDIO,
}

PACK_PLACEMENTS = frozenset(placement for _kind, placement, _aspect in SOCIAL_PACK + BRAND + AUDIO)


def still_placement(channel: str) -> str:
    for item_channel, _kind, placement, _aspect in STILLS:
        if item_channel == channel:
            return placement
    return ""


def clip_placement(channel: str) -> str:
    for item_channel, _kind, placement, _aspect in CLIPS:
        if item_channel == channel:
            return placement
    return ""


def keep_creative(row: dict[str, Any]) -> bool:
    """True for pack assets, harvested site stills, or anything already produced."""
    placement = str(row.get("placement") or "")
    if placement in PACK_PLACEMENTS or placement in SITE_PLACEMENTS:
        return True
    if row.get("source") == "site":
        return True
    if row.get("preview") and row.get("status") in {"produced", "harvested", "ready"}:
        return True
    return False


def creative_prompt(store: Any, kind: str, placement: str) -> str:
    from navin.marketing.content import product_facts

    facts = product_facts(store)
    brand = store.load_brand()
    harvest = store.load_harvest()
    colors = ", ".join(brand.get("colors") or harvest.get("colors") or ["neutral"])
    headings = " / ".join(
        str(item).strip() for item in (harvest.get("headings") or [])[:3] if str(item).strip()
    )
    return (
        f"{facts['name']} {placement} for {kind}, ready to post. "
        f"Brand colors {colors}. Tone {facts['tone']}. "
        f"Audience {facts['audience'] or 'the buyers of this exact product'}. "
        f"Site {facts['site'] or 'none'}. "
        f"{facts['one_liner']} "
        f"Headlines: {headings or facts['heading'] or facts['name']}. "
        f"CTA: {facts['cta'] or 'none'}. "
        f"Show this exact product and its UI or packaging, not stock office photography. "
        f"No fake brand name. No generic SaaS collage."
    )
