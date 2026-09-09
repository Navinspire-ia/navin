# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Creative briefs and the image -> vision -> fix loop."""

from __future__ import annotations

from typing import Any

from navin.marketing.errors import MarketingError
from navin.marketing.pack import AUDIO, CLIPS, STILLS, creative_prompt
from navin.marketing.store import CREATIVE_KINDS, MarketingStore

_PLACEMENTS = {
    "image": [placement for _channel, _kind, placement, _aspect in STILLS],
    "video": [placement for _channel, _kind, placement, _aspect in CLIPS],
    "audio": [placement for _kind, placement, _aspect in AUDIO],
    "banner": ["linkedin banner", "og image", "ad banner"],
}


def brief_creatives(
    store: MarketingStore,
    *,
    kinds: list[str] | None = None,
    campaign_id: str = "",
) -> list[dict[str, Any]]:
    picked = [item.lower() for item in (kinds or CREATIVE_KINDS) if item.lower() in CREATIVE_KINDS]
    if not picked:
        picked = ["image", "video"]
    created: list[dict[str, Any]] = []
    for kind in picked:
        for placement in _PLACEMENTS.get(kind) or []:
            row = store.upsert_creative(
                {
                    "kind": kind,
                    "placement": placement,
                    "campaign_id": campaign_id,
                    "prompt": creative_prompt(store, kind, placement),
                    "status": "brief",
                    "vision": None,
                }
            )
            created.append(row)
    store.append_journal({"kind": "creative", "text": f"briefed {len(created)} creatives"})
    return created


def apply_vision(
    store: MarketingStore,
    creative_id: str,
    *,
    verdict: str,
    score: float | None = None,
    notes: str = "",
) -> dict[str, Any]:
    rows = store.load_creatives()
    current = next((item for item in rows if item.get("id") == creative_id), None)
    if current is None:
        raise MarketingError(f"creative {creative_id} not found", status=404)
    clean = str(verdict or "").strip().upper()
    if clean not in {"PASS", "WARN", "BLOCK"}:
        raise MarketingError("verdict must be PASS, WARN or BLOCK")
    status = "approved" if clean == "PASS" else "revise" if clean == "BLOCK" else "review"
    vision = {
        "verdict": clean,
        "score": score,
        "notes": notes,
    }
    updated = store.upsert_creative({**current, "id": creative_id, "status": status, "vision": vision})
    store.append_journal({"kind": "vision", "text": f"{creative_id} {clean} -> {status}"})
    return updated
