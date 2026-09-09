"""Stable, serializable paid-media domain models."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from navin.seo.models import Confidence, Evidence, Severity

__all__ = [
    "AdRow",
    "AdsAnalysis",
    "AdsFinding",
    "ChangeAction",
    "ChangeStatus",
    "Confidence",
    "EntityMetrics",
    "Evidence",
    "Level",
    "Metrics",
    "Platform",
    "ProposedChange",
    "Severity",
]


class Platform(StrEnum):
    GOOGLE = "google"
    MICROSOFT = "microsoft"
    META = "meta"
    LINKEDIN = "linkedin"
    TIKTOK = "tiktok"
    REDDIT = "reddit"
    UNKNOWN = "unknown"


PLATFORM_LABELS: dict[str, str] = {
    Platform.GOOGLE: "Google Ads",
    Platform.MICROSOFT: "Microsoft Ads",
    Platform.META: "Meta Ads",
    Platform.LINKEDIN: "LinkedIn Ads",
    Platform.TIKTOK: "TikTok Ads",
    Platform.REDDIT: "Reddit Ads",
    Platform.UNKNOWN: "Ads",
}


class Level(StrEnum):
    ACCOUNT = "account"
    CAMPAIGN = "campaign"
    AD_GROUP = "ad_group"
    AD = "ad"
    KEYWORD = "keyword"
    SEARCH_TERM = "search_term"


# Entity columns that identify a row at each level (most specific last).
LEVEL_KEYS: dict[Level, tuple[str, ...]] = {
    Level.ACCOUNT: ("account",),
    Level.CAMPAIGN: ("campaign",),
    Level.AD_GROUP: ("campaign", "ad_group"),
    Level.AD: ("campaign", "ad_group", "ad"),
    Level.KEYWORD: ("campaign", "ad_group", "keyword"),
    Level.SEARCH_TERM: ("campaign", "ad_group", "search_term"),
}


class AdRow(BaseModel):
    """One normalized export line (daily or aggregated) at any level."""

    model_config = ConfigDict(extra="forbid")

    platform: Platform = Platform.UNKNOWN
    account: str = ""
    campaign: str = ""
    ad_group: str = ""
    ad: str = ""
    keyword: str = ""
    match_type: str = ""
    search_term: str = ""
    date: str = ""
    status: str = ""
    impressions: float = 0
    clicks: float = 0
    cost: float = 0
    conversions: float = 0
    conversion_value: float = 0
    # Optional platform numbers: quality_score, impression_share, frequency,
    # reach, video_views, daily_budget, leads.
    extras: dict[str, float] = Field(default_factory=dict)
    source: str = ""

    def level(self) -> Level:
        if self.search_term:
            return Level.SEARCH_TERM
        if self.keyword:
            return Level.KEYWORD
        if self.ad:
            return Level.AD
        if self.ad_group:
            return Level.AD_GROUP
        if self.campaign:
            return Level.CAMPAIGN
        return Level.ACCOUNT


class Metrics(BaseModel):
    """Derived KPIs. Ratios are ``None`` when their denominator is zero."""

    model_config = ConfigDict(extra="forbid")

    impressions: float = 0
    clicks: float = 0
    cost: float = 0
    conversions: float = 0
    conversion_value: float = 0
    ctr: float | None = None
    cpc: float | None = None
    cpm: float | None = None
    cpa: float | None = None
    cvr: float | None = None
    roas: float | None = None
    spend_share: float | None = None


class EntityMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Level
    key: str
    platform: Platform = Platform.UNKNOWN
    account: str = ""
    campaign: str = ""
    ad_group: str = ""
    ad: str = ""
    keyword: str = ""
    match_type: str = ""
    search_term: str = ""
    status: str = ""
    rows: int = 0
    days: int = 0
    metrics: Metrics = Field(default_factory=Metrics)
    extras: dict[str, float] = Field(default_factory=dict)

    def target(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for field in ("account", "campaign", "ad_group", "ad", "keyword", "match_type", "search_term"):
            value = getattr(self, field)
            if value:
                out[field] = value
        return out


ChangeAction = Literal[
    "pause",
    "reduce_budget",
    "increase_budget",
    "add_negative_keyword",
    "lower_bid",
    "refresh_creative",
    "review_landing_page",
    "review_tracking",
    "rebalance_budget",
]

ChangeStatus = Literal["proposed", "approved", "rejected", "applied"]


class ProposedChange(BaseModel):
    """A concrete, reversible change the user must approve before it runs."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    id: str = ""
    platform: Platform = Platform.UNKNOWN
    action: ChangeAction
    level: Level
    target: dict[str, str] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    finding_code: str
    estimated_monthly_savings: float | None = None
    requires_approval: bool = True
    status: ChangeStatus = "proposed"
    note: str = ""

    def model_post_init(self, __context: Any) -> None:
        if not self.id:
            self.id = change_id(self.platform, self.action, self.level, self.target, self.params)


def change_id(
    platform: str,
    action: str,
    level: str,
    target: dict[str, str],
    params: dict[str, Any] | None = None,
) -> str:
    """Deterministic short id so the same proposal maps to the same store entry."""
    keyed = "|".join(f"{key}={target[key]}" for key in sorted(target))
    extra = ""
    if params:
        keyed_params = {key: params[key] for key in ("keyword", "match_type") if key in params}
        extra = "|".join(f"{key}={keyed_params[key]}" for key in sorted(keyed_params))
    raw = f"{platform}|{action}|{level}|{keyed}|{extra}"
    return "chg_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


class AdsFinding(BaseModel):
    """A finding whose claim is always backed by exported numbers."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    code: str
    category: str
    severity: Severity
    title: str
    message: str
    platform: Platform = Platform.UNKNOWN
    level: Level = Level.ACCOUNT
    entity: dict[str, str] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(min_length=1)
    impact: dict[str, float] = Field(default_factory=dict)
    recommendation: str
    confidence: Confidence = Confidence.HIGH
    change_ids: list[str] = Field(default_factory=list)


class AdsAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    platforms: list[Platform] = Field(default_factory=list)
    currency: str = ""
    period: dict[str, Any] = Field(default_factory=dict)
    row_count: int = 0
    totals: Metrics = Field(default_factory=Metrics)
    entities: dict[str, list[EntityMetrics]] = Field(default_factory=dict)
    findings: list[AdsFinding] = Field(default_factory=list)
    changes: list[ProposedChange] = Field(default_factory=list)
    data_gaps: list[dict[str, str]] = Field(default_factory=list)
    columns: dict[str, dict[str, str]] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
