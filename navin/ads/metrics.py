"""Deterministic aggregation of normalized rows into per-entity KPIs."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Iterable

from navin.ads.models import LEVEL_KEYS, AdRow, EntityMetrics, Level, Metrics, Platform


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def compute_metrics(
    *,
    impressions: float = 0,
    clicks: float = 0,
    cost: float = 0,
    conversions: float = 0,
    conversion_value: float = 0,
    total_cost: float | None = None,
) -> Metrics:
    return Metrics(
        impressions=round(impressions, 2),
        clicks=round(clicks, 2),
        cost=round(cost, 2),
        conversions=round(conversions, 2),
        conversion_value=round(conversion_value, 2),
        ctr=_ratio(clicks, impressions),
        cpc=_ratio(cost, clicks),
        cpm=None if impressions <= 0 else round(cost / impressions * 1000, 6),
        cpa=_ratio(cost, conversions),
        cvr=_ratio(conversions, clicks),
        roas=_ratio(conversion_value, cost),
        spend_share=None if not total_cost else _ratio(cost, total_cost),
    )


def sum_metrics(rows: Iterable[AdRow], *, total_cost: float | None = None) -> Metrics:
    impressions = clicks = cost = conversions = value = 0.0
    for row in rows:
        impressions += row.impressions
        clicks += row.clicks
        cost += row.cost
        conversions += row.conversions
        value += row.conversion_value
    return compute_metrics(
        impressions=impressions, clicks=clicks, cost=cost, conversions=conversions,
        conversion_value=value, total_cost=total_cost,
    )


def primary_rows(rows: list[AdRow]) -> list[AdRow]:
    """Rows of the most aggregated level present (avoid double counting).

    A campaign report plus a search-terms report describe the same spend; the
    account totals come from the campaign rows only.
    """
    if not rows:
        return []
    return source_rows_for(rows, Level.ACCOUNT) or rows


def period_of(rows: Iterable[AdRow]) -> dict[str, object]:
    dates = sorted({row.date for row in rows if row.date})
    if not dates:
        return {"start": None, "end": None, "days": None, "dated": False}
    start, end = dates[0], dates[-1]
    days = len(dates)
    try:
        span = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        days = max(days, 1) if span < days else span
    except ValueError:
        pass
    return {"start": start, "end": end, "days": days, "dated": True}


_LEVEL_ORDER = [Level.ACCOUNT, Level.CAMPAIGN, Level.AD_GROUP, Level.AD, Level.KEYWORD, Level.SEARCH_TERM]


def source_rows_for(rows: list[AdRow], level: Level) -> list[AdRow]:
    """Rows feeding one level: exact-level rows, else the shallowest deeper level.

    A campaign report plus a search-terms report describe the same spend, so a
    campaign entity never mixes both (no double counting); with only a
    search-terms report the campaigns are rolled up from the terms.
    """
    by_level: dict[Level, list[AdRow]] = defaultdict(list)
    for row in rows:
        by_level[row.level()].append(row)
    start = _LEVEL_ORDER.index(level)
    for candidate in _LEVEL_ORDER[start:]:
        if by_level.get(candidate):
            return by_level[candidate]
    return []


def aggregate(rows: list[AdRow], level: Level, *, total_cost: float | None = None) -> list[EntityMetrics]:
    """Group the level's source rows by the level's key columns."""
    keys = LEVEL_KEYS[level]
    last_key = keys[-1]
    groups: dict[tuple[str, ...], list[AdRow]] = defaultdict(list)
    for row in source_rows_for(rows, level):
        if last_key != "account" and not getattr(row, last_key):
            continue
        group_key = (row.platform.value, *[getattr(row, key) for key in keys])
        groups[group_key].append(row)
    out: list[EntityMetrics] = []
    for group_key, members in groups.items():
        first = members[0]
        extras = _merge_extras(members)
        entity = EntityMetrics(
            level=level,
            key=" > ".join(part for part in group_key[1:] if part) or group_key[0],
            platform=Platform(group_key[0]),
            account=first.account,
            campaign=first.campaign if "campaign" in keys else "",
            ad_group=first.ad_group if "ad_group" in keys else "",
            ad=first.ad if "ad" in keys else "",
            keyword=first.keyword if "keyword" in keys else "",
            match_type=first.match_type if "keyword" in keys else "",
            search_term=first.search_term if "search_term" in keys else "",
            status=_dominant_status(members),
            rows=len(members),
            days=len({row.date for row in members if row.date}),
            metrics=sum_metrics(members, total_cost=total_cost),
            extras=extras,
        )
        out.append(entity)
    out.sort(key=lambda item: (-item.metrics.cost, item.key))
    return out


def _dominant_status(rows: list[AdRow]) -> str:
    statuses = [row.status for row in rows if row.status]
    if not statuses:
        return ""
    return max(set(statuses), key=statuses.count)


def _merge_extras(rows: list[AdRow]) -> dict[str, float]:
    """Impression-weighted averages for rates / scores, max for reach-like extras."""
    weighted: dict[str, tuple[float, float]] = defaultdict(lambda: (0.0, 0.0))
    maxima: dict[str, float] = {}
    for row in rows:
        weight = max(row.impressions, 1.0)
        for name, value in row.extras.items():
            if name in {"quality_score", "impression_share", "frequency"}:
                total, weights = weighted[name]
                weighted[name] = (total + value * weight, weights + weight)
            elif name in {"reach", "video_views"}:
                maxima[name] = maxima.get(name, 0.0) + value
            else:
                maxima[name] = max(maxima.get(name, 0.0), value)
    out = {name: round(total / weights, 4) for name, (total, weights) in weighted.items() if weights}
    out.update({name: round(value, 4) for name, value in maxima.items()})
    return dict(sorted(out.items()))


def available_levels(rows: list[AdRow]) -> list[Level]:
    present = {row.level() for row in rows}
    return [level for level in Level if level in present]
