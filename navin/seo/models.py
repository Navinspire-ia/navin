# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Stable, serializable SEO domain models."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    value: Any
    source: str


class Finding(BaseModel):
    """A versioned finding whose claim is always backed by evidence."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    code: str
    category: str
    severity: Severity
    title: str
    message: str
    url: str | None = None
    evidence: list[Evidence] = Field(min_length=1)
    recommendation: str
    confidence: Confidence = Confidence.HIGH


class PageRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    url: str
    final_url: str
    status: int
    content_type: str = ""
    html: str = ""
    text: str = ""
    title: str = ""
    meta_description: str = ""
    h1: list[str] = Field(default_factory=list)
    canonical: str | None = None
    hreflang: dict[str, str] = Field(default_factory=dict)
    robots: str = ""
    links: list[str] = Field(default_factory=list)
    redirect_chain: list[str] = Field(default_factory=list)
    error: str | None = None


class AuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    pages: list[PageRecord] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    data_gaps: list[dict[str, str]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def stable_dump(value: BaseModel | dict[str, Any] | list[Any]) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    return value
