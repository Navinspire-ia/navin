# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Marketplace consultations stay in Career with their own project budgets."""

from __future__ import annotations

import re
from typing import Any

PROJECT_NEED_TYPES = {"rfp", "rfq", "rfi", "eoi", "sow"}
_PREFIXES = (
    ("rfp", r"rfp\b|request for proposals?\b|appel d['’]offres\b"),
    ("rfq", r"rfq\b|request for quotations?\b|demande de devis\b"),
    ("rfi", r"rfi\b|request for information\b"),
    ("eoi", r"eoi\b|expression of interest\b|appel [àa] manifestation d['’]int[ée]r[êe]t\b"),
    ("sow", r"sow\b|statement of work\b"),
)


def enrich_need(row: dict[str, Any]) -> None:
    """Use explicit source types or title prefixes, never incidental CV jargon."""
    from navin.career.normalize import detect_period, parse_pay_range

    title = str(row.get("title") or "").strip().lstrip("[")
    declared = str(row.get("need_type") or "").lower()
    if declared in PROJECT_NEED_TYPES:
        row["need_type"] = declared
    elif row.get("track") == "freelance":
        for kind, pattern in _PREFIXES:
            if re.match(r"(?:" + pattern + r")", title, re.I):
                row["need_type"] = kind
                break
    if row.get("track") != "freelance" or row.get("price_model") in {"daily", "hourly"}:
        return
    if row.get("price_model") != "fixed" and any(row.get(key) is not None for key in ("daily_rate_min", "daily_rate_max")):
        return
    text = str(row.get("description") or "")
    budget = re.search(
        r"(?:fixed[ -]price|forfait|budget(?:\s+(?:total|fixe|du projet))?)\s*:?\s*"
        r"((?:[A-Z]{3}|[$€£])?\s*\d[\d,. ]*(?:\s*[kK])?"
        r"(?:\s*-\s*\d[\d,. ]*(?:\s*[kK])?)?\s*(?:[A-Z]{3}|[$€£])?)", text, re.I)
    if not budget or detect_period(text[budget.start():budget.end() + 15]) or re.search(r"\bTJM\b", budget[0], re.I):
        return
    pay = parse_pay_range(budget[1])
    if pay["max"] is not None:
        row["price_model"] = "fixed"
        for key, value in (("budget_min", pay["min"]), ("budget_max", pay["max"]), ("budget", pay["max"])):
            if row.get(key) is None:
                row[key] = value
        if not row.get("currency"):
            row["currency"] = pay["currency"]


def is_project_need(row: dict[str, Any]) -> bool:
    """A daily-rate RFP still uses day-rate floors; a total budget never does."""
    if row.get("track") == "jobs":
        return False
    if row.get("price_model") == "fixed":
        return True
    return (row.get("need_type") in PROJECT_NEED_TYPES
            and row.get("price_model") not in {"daily", "hourly"}
            and all(row.get(key) is None for key in ("daily_rate_min", "daily_rate_max", "compensation")))
