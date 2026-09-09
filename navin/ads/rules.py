# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Deterministic paid-media rules: rows -> findings + approval-gated changes.

Every finding carries the exported numbers it was computed from. Thresholds
are explicit and configurable; nothing here asks a model for a verdict.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pydantic import Field

from navin.ads.metrics import aggregate, available_levels, period_of, primary_rows, sum_metrics
from navin.ads.models import (
    PLATFORM_LABELS,
    AdRow,
    AdsAnalysis,
    AdsFinding,
    Confidence,
    EntityMetrics,
    Evidence,
    Level,
    Platform,
    ProposedChange,
    Severity,
)
from navin.config_base import Base

DAYS_PER_MONTH = 30.4
_OFF_STATUSES = ("paused", "removed", "ended", "deleted", "inactive", "disable", "off", "archived", "suspendu", "supprime")


class AdsThresholds(Base):
    """Rule thresholds. Money values are in the account currency."""

    # An entity with this many clicks and no conversion is considered proven waste.
    min_clicks_no_conversion: int = Field(default=30, ge=1)
    min_cost_no_conversion: float = Field(default=50.0, ge=0)
    # Share of total spend under which an entity is too small to act on.
    min_spend_share: float = Field(default=0.03, ge=0, le=1)
    high_cpa_ratio: float = Field(default=1.8, ge=1)
    min_conversions_for_cpa: int = Field(default=3, ge=1)
    low_ctr_ratio: float = Field(default=0.5, ge=0, le=1)
    min_impressions_for_ctr: int = Field(default=1000, ge=1)
    search_term_min_cost: float = Field(default=20.0, ge=0)
    search_term_min_clicks: int = Field(default=10, ge=1)
    winner_cpa_ratio: float = Field(default=0.7, ge=0, le=1)
    winner_min_conversions: int = Field(default=5, ge=1)
    winner_max_spend_share: float = Field(default=0.35, ge=0, le=1)
    low_quality_score: float = Field(default=4.0, ge=1, le=10)
    fatigue_frequency: float = Field(default=4.0, ge=1)
    pacing_tolerance: float = Field(default=0.15, ge=0, le=1)
    low_impression_share: float = Field(default=0.3, ge=0, le=1)
    concentration_share: float = Field(default=0.7, ge=0, le=1)
    max_changes_per_rule: int = Field(default=40, ge=1, le=500)


def analyze_rows(
    rows: list[AdRow],
    *,
    thresholds: AdsThresholds | None = None,
    monthly_budget: float | None = None,
    currency: str = "",
    sources: list[str] | None = None,
    columns: dict[str, dict[str, str]] | None = None,
    data_gaps: list[dict[str, str]] | None = None,
) -> AdsAnalysis:
    cfg = thresholds or AdsThresholds()
    rows = list(rows)
    period = period_of(rows)
    base_rows = primary_rows(rows)
    totals = sum_metrics(base_rows)
    total_cost = totals.cost
    levels = available_levels(rows)
    entities: dict[str, list[EntityMetrics]] = {}
    for level in (Level.CAMPAIGN, Level.AD_GROUP, Level.AD, Level.KEYWORD, Level.SEARCH_TERM):
        if any(_level_index(candidate) >= _level_index(level) for candidate in levels):
            grouped = aggregate(rows, level, total_cost=total_cost)
            if grouped:
                entities[level.value] = grouped
    platforms = sorted({row.platform for row in rows}, key=lambda item: item.value)
    source_label = ", ".join(sources or []) or "rows"
    conversions_tracked = _conversions_tracked(columns, rows)
    context = _Context(
        cfg=cfg, totals=totals, total_cost=total_cost, days=period.get("days"),
        source=source_label, currency=currency, conversions_tracked=conversions_tracked,
        entities=entities, rows=rows,
    )
    findings: list[AdsFinding] = []
    changes: list[ProposedChange] = []
    for rule in (
        _rule_tracking,
        _rule_zero_conversion_spend,
        _rule_high_cpa,
        _rule_search_term_waste,
        _rule_low_ctr,
        _rule_low_quality_score,
        _rule_creative_fatigue,
        _rule_winners,
        _rule_impression_share,
        _rule_concentration,
    ):
        rule(context, findings, changes)
    if monthly_budget:
        _rule_pacing(context, findings, changes, monthly_budget=monthly_budget)
    changes = _dedupe_changes(changes)
    _link_changes(findings, changes)
    findings.sort(key=lambda item: (_SEVERITY_ORDER[item.severity], -item.impact.get("cost", 0.0), item.code, item.entity.get("campaign", "")))
    gaps = list(data_gaps or [])
    if not period.get("dated"):
        gaps.append({
            "service": "period",
            "reason": "Export has no date column: monthly projections use the export as one period",
        })
    return AdsAnalysis(
        platforms=platforms,
        currency=currency,
        period=period,
        row_count=len(rows),
        totals=totals,
        entities=entities,
        findings=findings,
        changes=changes,
        data_gaps=gaps,
        columns=columns or {},
        sources=list(sources or []),
        metadata={
            "thresholds": cfg.model_dump(mode="json"),
            "levels": [level.value for level in levels],
            "monthly_budget": monthly_budget,
            "wasted_cost": round(sum(item.impact.get("wasted_cost", 0.0) for item in findings if item.code in _WASTE_CODES), 2),
        },
    )


_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3, Severity.INFO: 4}
_WASTE_CODES = frozenset({"zero_conversion_spend", "search_term_waste"})


def _level_index(level: Level) -> int:
    return [Level.ACCOUNT, Level.CAMPAIGN, Level.AD_GROUP, Level.AD, Level.KEYWORD, Level.SEARCH_TERM].index(level)


class _Context:
    def __init__(
        self,
        *,
        cfg: AdsThresholds,
        totals: Any,
        total_cost: float,
        days: Any,
        source: str,
        currency: str,
        conversions_tracked: bool,
        entities: dict[str, list[EntityMetrics]],
        rows: list[AdRow],
    ) -> None:
        self.cfg = cfg
        self.totals = totals
        self.total_cost = total_cost
        self.days = int(days) if isinstance(days, int) and days > 0 else None
        self.source = source
        self.currency = currency
        self.conversions_tracked = conversions_tracked
        self.entities = entities
        self.rows = rows

    def monthly(self, cost: float) -> float:
        if self.days:
            return round(cost / self.days * DAYS_PER_MONTH, 2)
        return round(cost, 2)

    def level(self, level: Level) -> list[EntityMetrics]:
        return self.entities.get(level.value, [])

    def money(self, value: float) -> str:
        return f"{value:,.2f} {self.currency}".strip()

    def evidence(self, entity: EntityMetrics, **extra: Any) -> list[Evidence]:
        payload: dict[str, Any] = {
            "impressions": entity.metrics.impressions,
            "clicks": entity.metrics.clicks,
            "cost": entity.metrics.cost,
            "conversions": entity.metrics.conversions,
            "conversion_value": entity.metrics.conversion_value,
            "ctr": entity.metrics.ctr,
            "cpc": entity.metrics.cpc,
            "cpa": entity.metrics.cpa,
            "roas": entity.metrics.roas,
            "spend_share": entity.metrics.spend_share,
            "rows": entity.rows,
        }
        payload.update({key: value for key, value in entity.extras.items()})
        payload.update(extra)
        return [
            Evidence(kind="metrics", value=payload, source=self.source),
            Evidence(kind="account_benchmark", value={
                "cost": self.totals.cost, "ctr": self.totals.ctr, "cpa": self.totals.cpa,
                "cpc": self.totals.cpc, "roas": self.totals.roas, "days": self.days,
            }, source=self.source),
        ]


def _conversions_tracked(columns: dict[str, dict[str, str]] | None, rows: list[AdRow]) -> bool:
    if columns:
        return any("conversions" in mapping for mapping in columns.values())
    return any(row.conversions for row in rows)


def _is_off(entity: EntityMetrics) -> bool:
    status = entity.status.lower()
    return any(token in status for token in _OFF_STATUSES)


def _label(entity: EntityMetrics) -> str:
    return f"{PLATFORM_LABELS.get(entity.platform, 'Ads')} {entity.level.value.replace('_', ' ')} \"{entity.key}\""


def _actionable_levels(ctx: _Context) -> list[Level]:
    """Levels where pause / budget decisions are made (skip search terms)."""
    return [level for level in (Level.CAMPAIGN, Level.AD_GROUP, Level.KEYWORD, Level.AD) if ctx.level(level)]


# --- rules ------------------------------------------------------------------


def _rule_tracking(ctx: _Context, findings: list[AdsFinding], changes: list[ProposedChange]) -> None:
    if not ctx.conversions_tracked:
        return
    if ctx.totals.conversions > 0:
        return
    if ctx.total_cost < ctx.cfg.min_cost_no_conversion * 2:
        return
    platform = ctx.rows[0].platform if ctx.rows else Platform.UNKNOWN
    change = ProposedChange(
        platform=platform, action="review_tracking", level=Level.ACCOUNT, target={"account": "all"},
        params={"cost_without_conversions": ctx.total_cost},
        rationale="Spend is recorded with zero conversions across the whole export; conversion tracking is probably broken or not imported.",
        finding_code="tracking_missing",
    )
    changes.append(change)
    findings.append(AdsFinding(
        code="tracking_missing", category="tracking", severity=Severity.HIGH,
        title="No conversions recorded on the whole account",
        message=f"{ctx.money(ctx.total_cost)} spent with 0 conversions over the export. Efficiency rules are muted until tracking is fixed.",
        platform=platform, level=Level.ACCOUNT, entity={"account": "all"},
        evidence=[Evidence(kind="account_totals", value=ctx.totals.model_dump(mode="json"), source=ctx.source)],
        impact={"cost": ctx.total_cost},
        recommendation="Verify the conversion tag / pixel / CAPI and the conversion columns of the export before judging CPA or ROAS.",
        change_ids=[change.id],
    ))


def _rule_zero_conversion_spend(ctx: _Context, findings: list[AdsFinding], changes: list[ProposedChange]) -> None:
    if not ctx.conversions_tracked or ctx.totals.conversions <= 0:
        return
    cfg = ctx.cfg
    count = 0
    # A campaign flagged whole is not re-flagged through its ad groups / keywords.
    covered: set[tuple[str, str]] = set()
    for level in _actionable_levels(ctx):
        if level == Level.AD:
            continue
        for entity in ctx.level(level):
            m = entity.metrics
            if m.conversions > 0 or _is_off(entity):
                continue
            if _covered(entity, covered):
                continue
            share = m.spend_share or 0.0
            if m.clicks < cfg.min_clicks_no_conversion or m.cost < cfg.min_cost_no_conversion:
                continue
            if share < cfg.min_spend_share and m.cost < cfg.min_cost_no_conversion * 4:
                continue
            if count >= cfg.max_changes_per_rule:
                break
            count += 1
            covered.add(_cover_key(entity))
            monthly = ctx.monthly(m.cost)
            severity = Severity.HIGH if share >= 0.10 else Severity.MEDIUM
            if level == Level.CAMPAIGN:
                change = ProposedChange(
                    platform=entity.platform, action="reduce_budget", level=level, target=entity.target(),
                    params={"budget_change_pct": -50, "then": "pause if still no conversion after 7 days"},
                    rationale=f"{m.clicks:.0f} clicks and {ctx.money(m.cost)} without a conversion.",
                    finding_code="zero_conversion_spend", estimated_monthly_savings=round(monthly * 0.5, 2),
                )
            else:
                change = ProposedChange(
                    platform=entity.platform, action="pause", level=level, target=entity.target(),
                    params={},
                    rationale=f"{m.clicks:.0f} clicks and {ctx.money(m.cost)} without a conversion.",
                    finding_code="zero_conversion_spend", estimated_monthly_savings=monthly,
                )
            changes.append(change)
            findings.append(AdsFinding(
                code="zero_conversion_spend", category="efficiency", severity=severity,
                title=f"Spend without conversions: {entity.key}",
                message=(
                    f"{_label(entity)} spent {ctx.money(m.cost)} ({share:.1%} of spend) for "
                    f"{m.clicks:.0f} clicks and 0 conversions."
                ),
                platform=entity.platform, level=level, entity=entity.target(),
                evidence=ctx.evidence(entity),
                impact={"cost": m.cost, "wasted_cost": m.cost, "monthly_cost": monthly},
                recommendation=(
                    "Cut the budget by half now and pause if it still does not convert in 7 days."
                    if level == Level.CAMPAIGN else "Pause it and move the budget to converting entities."
                ),
                change_ids=[change.id],
            ))


def _rule_high_cpa(ctx: _Context, findings: list[AdsFinding], changes: list[ProposedChange]) -> None:
    account_cpa = ctx.totals.cpa
    if not account_cpa:
        return
    cfg = ctx.cfg
    count = 0
    covered: set[tuple[str, str]] = set()
    for level in _actionable_levels(ctx):
        if level == Level.AD:
            continue
        for entity in ctx.level(level):
            m = entity.metrics
            if not m.cpa or m.conversions < cfg.min_conversions_for_cpa or _is_off(entity):
                continue
            if _covered(entity, covered):
                continue
            ratio = m.cpa / account_cpa
            if ratio < cfg.high_cpa_ratio or (m.spend_share or 0.0) < cfg.min_spend_share:
                continue
            if count >= cfg.max_changes_per_rule:
                break
            count += 1
            covered.add(_cover_key(entity))
            excess = max(0.0, m.cost - m.conversions * account_cpa)
            severity = Severity.HIGH if ratio >= 2.5 else Severity.MEDIUM
            if level == Level.CAMPAIGN:
                change = ProposedChange(
                    platform=entity.platform, action="reduce_budget", level=level, target=entity.target(),
                    params={"budget_change_pct": -30},
                    rationale=f"CPA {m.cpa:.2f} is {ratio:.1f}x the account CPA {account_cpa:.2f}.",
                    finding_code="high_cpa", estimated_monthly_savings=round(ctx.monthly(excess) * 0.5, 2),
                )
            else:
                change = ProposedChange(
                    platform=entity.platform, action="lower_bid", level=level, target=entity.target(),
                    params={"bid_change_pct": -20},
                    rationale=f"CPA {m.cpa:.2f} is {ratio:.1f}x the account CPA {account_cpa:.2f}.",
                    finding_code="high_cpa", estimated_monthly_savings=round(ctx.monthly(excess) * 0.3, 2),
                )
            changes.append(change)
            findings.append(AdsFinding(
                code="high_cpa", category="efficiency", severity=severity,
                title=f"CPA {ratio:.1f}x the account average: {entity.key}",
                message=(
                    f"{_label(entity)} converts at {m.cpa:.2f} vs {account_cpa:.2f} for the account "
                    f"({m.conversions:.0f} conversions, {ctx.money(m.cost)})."
                ),
                platform=entity.platform, level=level, entity=entity.target(),
                evidence=ctx.evidence(entity, cpa_ratio=round(ratio, 3)),
                impact={"cost": m.cost, "excess_cost": round(excess, 2)},
                recommendation=(
                    "Reduce its budget by 30% and reallocate to entities under the account CPA; review the audience / keywords driving the cost."
                    if level == Level.CAMPAIGN else "Lower bids by 20% and tighten the match types / audience."
                ),
                change_ids=[change.id],
            ))


def _rule_search_term_waste(ctx: _Context, findings: list[AdsFinding], changes: list[ProposedChange]) -> None:
    terms = ctx.level(Level.SEARCH_TERM)
    if not terms or not ctx.conversions_tracked:
        return
    cfg = ctx.cfg
    wasted = [
        entity for entity in terms
        if entity.metrics.conversions <= 0
        and entity.metrics.cost >= cfg.search_term_min_cost
        and entity.metrics.clicks >= cfg.search_term_min_clicks
    ]
    if not wasted:
        return
    wasted.sort(key=lambda item: -item.metrics.cost)
    total_waste = round(sum(item.metrics.cost for item in wasted), 2)
    share = total_waste / ctx.total_cost if ctx.total_cost else 0.0
    severity = Severity.HIGH if share >= 0.10 else Severity.MEDIUM if share >= 0.03 else Severity.LOW
    ids: list[str] = []
    by_campaign: dict[str, list[EntityMetrics]] = defaultdict(list)
    for entity in wasted[: cfg.max_changes_per_rule]:
        target = {"campaign": entity.campaign}
        if entity.ad_group:
            target["ad_group"] = entity.ad_group
        change = ProposedChange(
            platform=entity.platform, action="add_negative_keyword", level=Level.SEARCH_TERM, target=target,
            params={"keyword": entity.search_term, "match_type": "exact", "scope": "ad_group" if entity.ad_group else "campaign"},
            rationale=f"{entity.metrics.clicks:.0f} clicks, {ctx.money(entity.metrics.cost)}, 0 conversions.",
            finding_code="search_term_waste", estimated_monthly_savings=ctx.monthly(entity.metrics.cost),
        )
        changes.append(change)
        ids.append(change.id)
        by_campaign[entity.campaign].append(entity)
    top = [
        {"search_term": item.search_term, "campaign": item.campaign, "ad_group": item.ad_group,
         "clicks": item.metrics.clicks, "cost": item.metrics.cost}
        for item in wasted[:25]
    ]
    findings.append(AdsFinding(
        code="search_term_waste", category="targeting", severity=severity,
        title=f"{len(wasted)} search terms spend without converting",
        message=(
            f"{len(wasted)} search terms consumed {ctx.money(total_waste)} ({share:.1%} of spend) "
            f"with 0 conversions across {len(by_campaign)} campaign(s)."
        ),
        platform=wasted[0].platform, level=Level.SEARCH_TERM,
        entity={"campaigns": ", ".join(sorted(by_campaign))[:200]},
        evidence=[
            Evidence(kind="search_terms", value=top, source=ctx.source),
            Evidence(kind="totals", value={"wasted_cost": total_waste, "total_cost": ctx.total_cost, "terms": len(wasted)}, source=ctx.source),
        ],
        impact={"cost": total_waste, "wasted_cost": total_waste, "monthly_cost": ctx.monthly(total_waste)},
        recommendation="Add them as exact negatives (ad group scope when known) and review the broad / phrase keywords that matched them.",
        change_ids=ids,
    ))


def _rule_low_ctr(ctx: _Context, findings: list[AdsFinding], changes: list[ProposedChange]) -> None:
    cfg = ctx.cfg
    level = next((lvl for lvl in (Level.AD, Level.AD_GROUP, Level.CAMPAIGN) if ctx.level(lvl)), None)
    if level is None:
        return
    entities = ctx.level(level)
    impressions = sum(item.metrics.impressions for item in entities)
    clicks = sum(item.metrics.clicks for item in entities)
    if impressions <= 0:
        return
    benchmark = clicks / impressions
    if benchmark <= 0:
        return
    count = 0
    for entity in entities:
        m = entity.metrics
        if m.impressions < cfg.min_impressions_for_ctr or m.ctr is None or _is_off(entity):
            continue
        if m.ctr > benchmark * cfg.low_ctr_ratio:
            continue
        if count >= cfg.max_changes_per_rule:
            break
        count += 1
        change = ProposedChange(
            platform=entity.platform, action="refresh_creative", level=level, target=entity.target(),
            params={"ctr": m.ctr, "benchmark_ctr": round(benchmark, 6)},
            rationale=f"CTR {m.ctr:.2%} vs {benchmark:.2%} for the {level.value.replace('_', ' ')} average.",
            finding_code="low_ctr",
        )
        changes.append(change)
        findings.append(AdsFinding(
            code="low_ctr", category="creative",
            severity=Severity.MEDIUM if (m.spend_share or 0.0) >= cfg.min_spend_share else Severity.LOW,
            title=f"CTR half the average: {entity.key}",
            message=f"{_label(entity)} has a {m.ctr:.2%} CTR over {m.impressions:.0f} impressions vs {benchmark:.2%} on average.",
            platform=entity.platform, level=level, entity=entity.target(),
            evidence=ctx.evidence(entity, benchmark_ctr=round(benchmark, 6)),
            impact={"cost": m.cost},
            recommendation="Rewrite the headlines / hook against the winners and check keyword-to-ad relevance; pause the creative if a new variant beats it.",
            change_ids=[change.id],
        ))


def _rule_low_quality_score(ctx: _Context, findings: list[AdsFinding], changes: list[ProposedChange]) -> None:
    cfg = ctx.cfg
    keywords = [
        item for item in ctx.level(Level.KEYWORD)
        if "quality_score" in item.extras and item.extras["quality_score"] <= cfg.low_quality_score
        and item.metrics.cost >= cfg.min_cost_no_conversion / 2 and not _is_off(item)
    ]
    if not keywords:
        return
    keywords.sort(key=lambda item: -item.metrics.cost)
    ids: list[str] = []
    for entity in keywords[: cfg.max_changes_per_rule]:
        change = ProposedChange(
            platform=entity.platform, action="review_landing_page", level=Level.KEYWORD, target=entity.target(),
            params={"quality_score": entity.extras["quality_score"]},
            rationale=f"Quality score {entity.extras['quality_score']:.0f}/10 with {ctx.money(entity.metrics.cost)} spent.",
            finding_code="low_quality_score",
        )
        changes.append(change)
        ids.append(change.id)
    cost = round(sum(item.metrics.cost for item in keywords), 2)
    findings.append(AdsFinding(
        code="low_quality_score", category="relevance", severity=Severity.MEDIUM,
        title=f"{len(keywords)} keywords with quality score <= {cfg.low_quality_score:.0f}",
        message=f"{len(keywords)} keywords spend {ctx.money(cost)} with a quality score of {cfg.low_quality_score:.0f} or less; their CPC is inflated.",
        platform=keywords[0].platform, level=Level.KEYWORD,
        entity={"keywords": ", ".join(item.keyword for item in keywords[:10])[:200]},
        evidence=[Evidence(kind="keywords", value=[
            {"keyword": item.keyword, "campaign": item.campaign, "ad_group": item.ad_group,
             "quality_score": item.extras["quality_score"], "cost": item.metrics.cost, "cpa": item.metrics.cpa}
            for item in keywords[:25]
        ], source=ctx.source)],
        impact={"cost": cost},
        recommendation="Group them in tighter ad groups with matching ad copy and landing pages; pause the ones that also fail to convert.",
        change_ids=ids,
    ))


def _rule_creative_fatigue(ctx: _Context, findings: list[AdsFinding], changes: list[ProposedChange]) -> None:
    cfg = ctx.cfg
    for level in (Level.AD, Level.AD_GROUP, Level.CAMPAIGN):
        entities = [item for item in ctx.level(level) if "frequency" in item.extras]
        if not entities:
            continue
        for entity in entities:
            if entity.extras["frequency"] < cfg.fatigue_frequency or _is_off(entity):
                continue
            m = entity.metrics
            change = ProposedChange(
                platform=entity.platform, action="refresh_creative", level=level, target=entity.target(),
                params={"frequency": entity.extras["frequency"]},
                rationale=f"Frequency {entity.extras['frequency']:.1f} over the period.",
                finding_code="creative_fatigue",
            )
            changes.append(change)
            findings.append(AdsFinding(
                code="creative_fatigue", category="creative", severity=Severity.MEDIUM,
                title=f"Creative fatigue: {entity.key}",
                message=f"{_label(entity)} reached a frequency of {entity.extras['frequency']:.1f} ({ctx.money(m.cost)} spent).",
                platform=entity.platform, level=level, entity=entity.target(),
                evidence=ctx.evidence(entity),
                impact={"cost": m.cost},
                recommendation="Rotate in new creatives or widen the audience; cap frequency if the platform allows it.",
                change_ids=[change.id],
            ))
        return


def _rule_winners(ctx: _Context, findings: list[AdsFinding], changes: list[ProposedChange]) -> None:
    account_cpa = ctx.totals.cpa
    if not account_cpa:
        return
    cfg = ctx.cfg
    for entity in ctx.level(Level.CAMPAIGN):
        m = entity.metrics
        if not m.cpa or m.conversions < cfg.winner_min_conversions or _is_off(entity):
            continue
        if m.cpa > account_cpa * cfg.winner_cpa_ratio or (m.spend_share or 0.0) > cfg.winner_max_spend_share:
            continue
        share = entity.extras.get("impression_share")
        if share is not None and share >= 0.9:
            continue
        change = ProposedChange(
            platform=entity.platform, action="increase_budget", level=Level.CAMPAIGN, target=entity.target(),
            params={"budget_change_pct": 20, "watch": "CPA must stay under the account average for 7 days"},
            rationale=f"CPA {m.cpa:.2f} vs {account_cpa:.2f} account average with {m.conversions:.0f} conversions.",
            finding_code="scale_winner",
        )
        changes.append(change)
        findings.append(AdsFinding(
            code="scale_winner", category="growth", severity=Severity.INFO,
            title=f"Efficient campaign with room to scale: {entity.key}",
            message=(
                f"{_label(entity)} converts at {m.cpa:.2f} ({m.cpa / account_cpa:.0%} of the account CPA) "
                f"on {m.spend_share or 0:.1%} of spend."
            ),
            platform=entity.platform, level=Level.CAMPAIGN, entity=entity.target(),
            evidence=ctx.evidence(entity),
            impact={"cost": m.cost},
            recommendation="Raise its budget by 20% and watch CPA for a week before the next step.",
            confidence=Confidence.MEDIUM,
            change_ids=[change.id],
        ))


def _rule_impression_share(ctx: _Context, findings: list[AdsFinding], changes: list[ProposedChange]) -> None:
    account_cpa = ctx.totals.cpa
    flagged = {change.target.get("campaign") for change in changes if change.finding_code == "scale_winner"}
    for entity in ctx.level(Level.CAMPAIGN):
        share = entity.extras.get("impression_share")
        if share is None or share > ctx.cfg.low_impression_share or entity.campaign in flagged or _is_off(entity):
            continue
        m = entity.metrics
        if account_cpa and (not m.cpa or m.cpa > account_cpa):
            continue
        if m.conversions <= 0:
            continue
        change = ProposedChange(
            platform=entity.platform, action="increase_budget", level=Level.CAMPAIGN, target=entity.target(),
            params={"budget_change_pct": 15, "impression_share": share},
            rationale=f"Impression share {share:.0%} on a campaign converting at or under the account CPA.",
            finding_code="impression_share_limited",
        )
        changes.append(change)
        findings.append(AdsFinding(
            code="impression_share_limited", category="growth", severity=Severity.LOW,
            title=f"Impression share {share:.0%}: {entity.key}",
            message=f"{_label(entity)} shows on {share:.0%} of eligible searches while converting at {m.cpa:.2f}.",
            platform=entity.platform, level=Level.CAMPAIGN, entity=entity.target(),
            evidence=ctx.evidence(entity),
            impact={"cost": m.cost},
            recommendation="Budget or rank is capping it: raise the budget 15% or bids on the converting keywords.",
            confidence=Confidence.MEDIUM,
            change_ids=[change.id],
        ))


def _rule_concentration(ctx: _Context, findings: list[AdsFinding], changes: list[ProposedChange]) -> None:
    campaigns = ctx.level(Level.CAMPAIGN)
    if len(campaigns) < 2:
        return
    top = campaigns[0]
    share = top.metrics.spend_share or 0.0
    if share < ctx.cfg.concentration_share:
        return
    account_cpa = ctx.totals.cpa
    if account_cpa and top.metrics.cpa and top.metrics.cpa <= account_cpa:
        return
    change = ProposedChange(
        platform=top.platform, action="rebalance_budget", level=Level.CAMPAIGN, target=top.target(),
        params={"spend_share": round(share, 4), "campaign_cpa": top.metrics.cpa, "account_cpa": account_cpa},
        rationale=f"{share:.0%} of spend sits in one campaign with a CPA above the account average.",
        finding_code="spend_concentration",
    )
    changes.append(change)
    findings.append(AdsFinding(
        code="spend_concentration", category="budget", severity=Severity.LOW,
        title=f"{share:.0%} of spend in one campaign: {top.key}",
        message=f"{_label(top)} takes {share:.0%} of spend with a CPA of {top.metrics.cpa if top.metrics.cpa is not None else 'n/a'} vs {account_cpa if account_cpa is not None else 'n/a'} for the account.",
        platform=top.platform, level=Level.CAMPAIGN, entity=top.target(),
        evidence=ctx.evidence(top),
        impact={"cost": top.metrics.cost},
        recommendation="Move 10-20% of its budget to the cheaper converting campaigns and re-check after a week.",
        confidence=Confidence.MEDIUM,
        change_ids=[change.id],
    ))


def _rule_pacing(
    ctx: _Context, findings: list[AdsFinding], changes: list[ProposedChange], *, monthly_budget: float
) -> None:
    if monthly_budget <= 0:
        return
    projected = ctx.monthly(ctx.total_cost)
    ratio = projected / monthly_budget
    tolerance = ctx.cfg.pacing_tolerance
    if abs(ratio - 1) <= tolerance:
        return
    over = ratio > 1
    platform = ctx.rows[0].platform if ctx.rows else Platform.UNKNOWN
    change = ProposedChange(
        platform=platform, action="rebalance_budget", level=Level.ACCOUNT, target={"account": "all"},
        params={"monthly_budget": monthly_budget, "projected_spend": projected, "pacing_ratio": round(ratio, 3)},
        rationale=f"Projected {projected:.2f} vs {monthly_budget:.2f} monthly budget ({ratio:.0%}).",
        finding_code="budget_pacing",
    )
    changes.append(change)
    findings.append(AdsFinding(
        code="budget_pacing", category="budget",
        severity=Severity.HIGH if ratio > 1.3 else Severity.MEDIUM,
        title=("Over" if over else "Under") + f"-pacing at {ratio:.0%} of the monthly budget",
        message=(
            f"Spend projects to {ctx.money(projected)} per month against a {ctx.money(monthly_budget)} budget "
            f"({'period-based' if ctx.days else 'export taken as one month'})."
        ),
        platform=platform, level=Level.ACCOUNT, entity={"account": "all"},
        evidence=[Evidence(kind="pacing", value={
            "total_cost": ctx.total_cost, "days": ctx.days, "projected_monthly": projected,
            "monthly_budget": monthly_budget, "ratio": round(ratio, 3),
        }, source=ctx.source)],
        impact={"cost": ctx.total_cost, "projected_gap": round(projected - monthly_budget, 2)},
        recommendation=(
            "Lower daily budgets on the worst-CPA campaigns first." if over
            else "Raise budgets on the campaigns under the account CPA or widen targeting."
        ),
        confidence=Confidence.HIGH if ctx.days else Confidence.LOW,
        change_ids=[change.id],
    ))


# --- helpers ----------------------------------------------------------------


def _cover_key(entity: EntityMetrics) -> tuple[str, str]:
    if entity.level == Level.CAMPAIGN:
        return ("campaign", entity.campaign)
    if entity.level == Level.AD_GROUP:
        return ("ad_group", f"{entity.campaign}\n{entity.ad_group}")
    return (entity.level.value, entity.key)


def _covered(entity: EntityMetrics, covered: set[tuple[str, str]]) -> bool:
    if ("campaign", entity.campaign) in covered and entity.level != Level.CAMPAIGN:
        return True
    return entity.level in {Level.KEYWORD, Level.AD} and (
        "ad_group", f"{entity.campaign}\n{entity.ad_group}"
    ) in covered


def _dedupe_changes(changes: list[ProposedChange]) -> list[ProposedChange]:
    seen: dict[str, ProposedChange] = {}
    for change in changes:
        if change.id not in seen:
            seen[change.id] = change
    return list(seen.values())


def _link_changes(findings: list[AdsFinding], changes: list[ProposedChange]) -> None:
    known = {change.id for change in changes}
    for finding in findings:
        finding.change_ids = [change_id for change_id in finding.change_ids if change_id in known]
