# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""7 / 30 / 90 day campaign plans from a goal and the current positioning."""

from __future__ import annotations

from typing import Any

from navin.marketing.errors import MarketingError
from navin.marketing.store import CHANNELS, MarketingStore

DEFAULT_MIX = {
    "linkedin": 20,
    "x": 40,
    "blog": 8,
    "youtube": 4,
    "tiktok": 15,
    "email": 3,
    "seo": 20,
}


def _horizon_counts(days: int) -> dict[str, int]:
    scale = max(7, min(days, 90)) / 30
    return {key: max(1, int(round(value * scale))) for key, value in DEFAULT_MIX.items()}


def _objectives(goal: str, days: int, signups: int) -> list[str]:
    return [
        "J7: message house et 3 preuves produit pretes",
        f"J{min(days, 30)}: {max(1, signups // 3)} inscriptions et 1 angle gagnant",
        f"J{days}: {signups} inscriptions ({goal})",
    ]


def build_campaign(
    store: MarketingStore,
    *,
    goal: str = "",
    days: int = 30,
    signups: int = 1000,
    channels: list[str] | None = None,
    label: str = "",
) -> dict[str, Any]:
    product = store.load_product()
    positioning = store.load_positioning()
    name = str(product.get("name") or store.load_brand().get("product") or "product")
    try:
        horizon = max(7, min(int(days), 90))
    except (TypeError, ValueError) as exc:
        raise MarketingError("days must be a number between 7 and 90") from exc
    try:
        target = max(1, int(signups))
    except (TypeError, ValueError) as exc:
        raise MarketingError("signups must be a positive number") from exc
    picked = [item.lower() for item in (channels or []) if str(item).lower() in CHANNELS]
    if not picked:
        picked = ["linkedin", "facebook", "instagram", "tiktok", "x"]
    mix = _horizon_counts(horizon)
    wanted_goal = goal or f"{target} inscriptions"
    existing = next(
        (
            row
            for row in store.load_campaigns()
            if row.get("status") in {"draft", "planned"}
            and int(row.get("days") or 0) == horizon
            and str(row.get("goal") or "") == wanted_goal
        ),
        None,
    )
    campaign = store.upsert_campaign(
        {
            **(existing or {}),
            "label": label or (existing or {}).get("label") or f"{horizon}-day {name}",
            "goal": wanted_goal,
            "days": horizon,
            "signups": target,
            "channels": picked,
            "mix": mix,
            "objectives": _objectives(wanted_goal, horizon, target),
            "icp": positioning.get("icp"),
            "status": "planned",
        }
    )
    store.append_journal({"kind": "campaign", "text": f"campaign planned: {campaign['label']}"})
    return campaign


def approve_campaign(store: MarketingStore, campaign_id: str) -> dict[str, Any]:
    row = store.get_campaign(campaign_id)
    updated = store.upsert_campaign({**row, "id": campaign_id, "status": "approved"})
    store.append_journal({"kind": "campaign", "text": f"campaign approved: {updated.get('label')}"})
    return updated
