"""Social calendar: one still and one clip per network. Drafts only, never published."""

from __future__ import annotations

from typing import Any

from navin.marketing.pack import SOCIAL_CHANNELS, clip_placement, still_placement
from navin.marketing.store import MarketingStore


def _rank(item: dict[str, Any]) -> int:
    return 0 if item.get("status") == "produced" else 1


def _pick(
    creatives: list[dict[str, Any]],
    placement: str,
    used: set[str],
    *,
    kind: str = "",
    fallback: bool = False,
) -> dict[str, Any] | None:
    ranked = sorted(creatives, key=_rank)
    if placement:
        for item in ranked:
            cid = str(item.get("id") or "")
            if not cid or cid in used:
                continue
            if str(item.get("placement") or "") != placement:
                continue
            return item
    if not fallback:
        return None
    for item in ranked:
        cid = str(item.get("id") or "")
        if not cid or cid in used:
            continue
        if kind and item.get("kind") != kind:
            continue
        if not item.get("preview"):
            continue
        return item
    return None


def _harvest_preview(harvest: dict[str, Any], index: int) -> str:
    images = [item for item in (harvest.get("images") or []) if isinstance(item, dict)]
    if not images:
        return ""
    item = images[index % len(images)]
    return str(item.get("preview") or item.get("url") or "")


def _ordered_content(store: MarketingStore) -> list[dict[str, Any]]:
    rows = [row for row in store.load_content() if not row.get("parent_id")]
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    by_channel: dict[str, dict[str, Any]] = {}
    for row in rows:
        channel = str(row.get("channel") or "")
        if channel in SOCIAL_CHANNELS and channel not in by_channel:
            by_channel[channel] = row
    for channel in SOCIAL_CHANNELS:
        row = by_channel.get(channel)
        if row and str(row.get("id") or "") not in seen:
            picked.append(row)
            seen.add(str(row.get("id") or ""))
    for row in rows:
        rid = str(row.get("id") or "")
        if rid and rid not in seen:
            picked.append(row)
            seen.add(rid)
    return picked[:16]


def build_social_calendar(store: MarketingStore) -> dict[str, Any]:
    content = _ordered_content(store)
    creatives = store.load_creatives()
    harvest = store.load_harvest()
    posts: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, row in enumerate(content):
        channel = str(row.get("channel") or "linkedin")
        still = _pick(creatives, still_placement(channel), used, kind="image", fallback=True)
        if still and still.get("id"):
            used.add(str(still["id"]))
        clip = _pick(creatives, clip_placement(channel), used, kind="video")
        if clip and clip.get("id"):
            used.add(str(clip["id"]))
        preview = str((still or {}).get("preview") or "") or _harvest_preview(harvest, index)
        clip_preview = str((clip or {}).get("preview") or "")
        kind = "video" if clip_preview else "image" if preview else "text"
        posts.append(
            {
                "id": f"soc-{row.get('id') or index}",
                "channel": channel,
                "day": (index % 14) + 1,
                "hook": row.get("hook") or row.get("title") or "",
                "body": row.get("body") or "",
                "cta": row.get("cta") or "",
                "hashtags": row.get("hashtags") or [],
                "creative_id": (still or {}).get("id") or "",
                "clip_id": (clip or {}).get("id") or "",
                "kind": kind,
                "preview": preview,
                "clip": clip_preview,
                "status": "draft",
            }
        )
    saved = store.save_social({"posts": posts})
    store.append_journal({"kind": "social", "text": f"social calendar: {len(posts)} drafts - not published"})
    return saved
