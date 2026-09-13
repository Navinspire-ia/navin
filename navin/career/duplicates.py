# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Conservative cross-board identity, with every original URL retained."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any


def _text(value: Any) -> str:
    value = unicodedata.normalize("NFKD", str(value or "").casefold())
    value = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(re.findall(r"\w+", value))


def same_mission(a: dict[str, Any], b: dict[str, Any]) -> bool:
    # A shared title alone is insufficient: two clients may recruit the same role.
    if a.get("source") == b.get("source"):
        return False
    if a.get("country") and b.get("country") and a["country"] != b["country"]:
        return False
    if a.get("track") in {"jobs", "freelance"} and b.get("track") in {"jobs", "freelance"} and a["track"] != b["track"]:
        return False
    title_a, title_b = _text(a.get("title")), _text(b.get("title"))
    if not title_a or SequenceMatcher(None, title_a, title_b).ratio() < .9:
        return False
    company_a, company_b = _text(a.get("company")), _text(b.get("company"))
    location_a, location_b = _text(a.get("location")), _text(b.get("location"))
    if location_a and location_b and location_a != location_b:
        return False
    desc_a, desc_b = _text(a.get("description")), _text(b.get("description"))
    if min(len(desc_a), len(desc_b)) < 160:
        return False
    # Different intermediaries can syndicate a client's text. In that case require
    # almost identical long descriptions, not merely a common list of skills.
    ratio = SequenceMatcher(None, desc_a, desc_b, autojunk=False).ratio()
    if company_a and company_a == company_b and (location_a or a.get("country")):
        return ratio >= .92
    return min(len(desc_a), len(desc_b)) >= 400 and ratio >= .98


def source_links(*rows: dict[str, Any]) -> list[dict[str, str]]:
    links = {}
    for row in rows:
        for source in [*row.get("source_links", []), row]:
            if source.get("url"):
                links[source["url"]] = {k: str(source.get(k) or "") for k in ("url", "source", "id")}
    return list(links.values())
