# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin SEO engine.

Names load on first access: the config schema reaches ``navin.seo.models``
through the Ads tool config, and must not import the crawler (httpx) for it.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navin.seo.crawler import SeoCrawler
    from navin.seo.models import AuditResult, Evidence, Finding, PageRecord
    from navin.seo.onpage import audit_page, audit_site
    from navin.seo.performance import PerformanceClient
    from navin.seo.report import render_html, render_json, render_markdown, write_report
    from navin.seo.scoring import health_score
    from navin.seo.structured import extract_jsonld, validate_jsonld

_EXPORTS = {
    "AuditResult": "navin.seo.models",
    "Evidence": "navin.seo.models",
    "Finding": "navin.seo.models",
    "PageRecord": "navin.seo.models",
    "PerformanceClient": "navin.seo.performance",
    "SeoCrawler": "navin.seo.crawler",
    "audit_page": "navin.seo.onpage",
    "audit_site": "navin.seo.onpage",
    "extract_jsonld": "navin.seo.structured",
    "health_score": "navin.seo.scoring",
    "render_html": "navin.seo.report",
    "render_json": "navin.seo.report",
    "render_markdown": "navin.seo.report",
    "validate_jsonld": "navin.seo.structured",
    "write_report": "navin.seo.report",
}

__all__ = [
    "AuditResult",
    "Evidence",
    "Finding",
    "PageRecord",
    "PerformanceClient",
    "SeoCrawler",
    "audit_page",
    "audit_site",
    "extract_jsonld",
    "health_score",
    "render_html",
    "render_json",
    "render_markdown",
    "validate_jsonld",
    "write_report",
]


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
