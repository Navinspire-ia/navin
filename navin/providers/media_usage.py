# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Debit managed media spend (image / video / audio) to the usage ledger.

Media tools bill the same managed key as chat, so a generation the ledger
never sees lets an account reach its provider cap while the dashboard still
reports budget left.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

_MICRO_USD = 1_000_000


def provider_cost_micro_usd(raw: Any) -> int:
    """Provider-reported spend for one media job, in microdollars."""
    if not isinstance(raw, dict):
        return 0
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        return 0
    try:
        cost = float(usage.get("cost"))
    except (TypeError, ValueError):
        return 0
    if cost <= 0:
        return 0
    return max(1, round(cost * _MICRO_USD))


def catalog_cost_micro_usd(config: Any, model: str, *, units: float = 1.0) -> int:
    """Catalog unit price per job, for providers that report no cost.

    ``units`` scales the catalog price when a job deviates from the unit the
    price is quoted for (image count, or clip duration against the 8s baseline).
    """
    presets = getattr(config, "model_presets", None) or {}
    for preset in presets.values():
        if (getattr(preset, "model", "") or "").strip() != model:
            continue
        # Prefer estimated_generation_cost (profil métier, ex. 720p/8s),
        # puis unit_price_usd (même sémantique pour le débit budget).
        raw_price = getattr(preset, "estimated_generation_cost", None)
        if raw_price is None:
            raw_price = getattr(preset, "unit_price_usd", None)
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            continue
        if price > 0:
            return max(1, round(price * max(units, 0.0) * _MICRO_USD))
    return 0


async def report_media_usage(model: str, raw: Any, *, units: float = 1.0) -> None:
    """Best-effort debit for completed media generation. Never raises."""
    import asyncio

    model = (model or "").strip()
    if not model:
        return
    try:
        from navin.config.loader import load_config
        from navin.license_client import report_usage, uses_managed_key

        config = load_config()
        if not uses_managed_key(config):
            return
        cost = provider_cost_micro_usd(raw)
        if cost <= 0:
            cost = catalog_cost_micro_usd(config, model, units=units)
        if cost <= 0:
            return
        await asyncio.to_thread(
            report_usage, config, model=model, cost_micro_usd=cost
        )
    except Exception:
        logger.debug("media usage report skipped", exc_info=True)
