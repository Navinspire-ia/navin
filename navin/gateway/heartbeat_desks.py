# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Heartbeat desk ticks that must not depend on the LLM turn."""

from __future__ import annotations

from typing import Any

from loguru import logger


def tick_heartbeat_desks(
    *,
    career_store: Any = None,
    tenders_store: Any = None,
    leads_store: Any = None,
    marketing_store: Any = None,
    trading_store: Any = None,
) -> str:
    """Run Tenders follow, Career watch, Leads watch, then Marketing watch.

    Each desk is isolated: one failure never skips the others.
    """
    notes: list[str] = []
    try:
        from navin.tenders.heartbeat import heartbeat_prompt_note, tick_watch

        note = heartbeat_prompt_note(tick_watch(tenders_store))
        if note:
            notes.append(note)
    except Exception:
        logger.exception("Heartbeat: tenders watch failed")
    try:
        from navin.career.heartbeat import heartbeat_prompt_note, tick_watch as career_tick

        note = heartbeat_prompt_note(career_tick(career_store))
        if note:
            notes.append(note)
    except Exception:
        logger.exception("Heartbeat: career watch failed")
    try:
        from navin.leads.heartbeat import heartbeat_prompt_note as leads_note
        from navin.leads.heartbeat import tick_watch as leads_tick

        note = leads_note(leads_tick(leads_store))
        if note:
            notes.append(note)
    except Exception:
        logger.exception("Heartbeat: leads watch failed")
    try:
        from navin.marketing.heartbeat import heartbeat_prompt_note as marketing_note
        from navin.marketing.heartbeat import tick_watch as marketing_tick

        note = marketing_note(marketing_tick(marketing_store))
        if note:
            notes.append(note)
    except Exception:
        logger.exception("Heartbeat: marketing watch failed")
    try:
        from navin.trading.heartbeat import heartbeat_prompt_note as trading_note
        from navin.trading.heartbeat import tick_watch as trading_tick

        note = trading_note(trading_tick(trading_store))
        if note:
            notes.append(note)
    except Exception:
        logger.exception("Heartbeat: trading watch failed")
    return "".join(notes)
