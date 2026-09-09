# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""JSON-LD extraction and type-specific validation."""

from __future__ import annotations

import json
import re
from typing import Any

from navin.seo.models import Confidence, Evidence, Finding, Severity

_REQUIRED: dict[str, tuple[str, ...]] = {
    "Article": ("headline", "author", "datePublished"),
    "BlogPosting": ("headline", "author", "datePublished"),
    "Product": ("name",),
    "Organization": ("name", "url"),
    "LocalBusiness": ("name", "address"),
    "BreadcrumbList": ("itemListElement",),
    "FAQPage": ("mainEntity",),
}


def extract_jsonld(html_text: str) -> tuple[list[dict[str, Any]], list[str]]:
    documents: list[dict[str, Any]] = []
    errors: list[str] = []
    pattern = r"<script\b[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>([\s\S]*?)</script>"
    for index, match in enumerate(re.finditer(pattern, html_text, re.I), start=1):
        try:
            value = json.loads(match.group(1).strip())
        except (ValueError, TypeError) as exc:
            errors.append(f"block {index}: {exc}")
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                documents.extend(node for node in item["@graph"] if isinstance(node, dict))
            elif isinstance(item, dict):
                documents.append(item)
    return documents, errors


def validate_jsonld(html_text: str, url: str) -> list[Finding]:
    documents, errors = extract_jsonld(html_text)
    findings: list[Finding] = []
    for error in errors:
        findings.append(Finding(
            code="jsonld_invalid", category="structured_data", severity=Severity.HIGH,
            title="Invalid JSON-LD", message="A JSON-LD block could not be parsed.",
            url=url, evidence=[Evidence(kind="parser_error", value=error, source=url)],
            recommendation="Fix the JSON syntax and validate the block.",
            confidence=Confidence.HIGH,
        ))
    for index, document in enumerate(documents, start=1):
        raw_types = document.get("@type")
        types = [raw_types] if isinstance(raw_types, str) else list(raw_types or [])
        if not types:
            findings.append(Finding(
                code="jsonld_type_missing", category="structured_data",
                severity=Severity.HIGH, title="JSON-LD type missing",
                message="A structured data node has no @type.", url=url,
                evidence=[Evidence(kind="jsonld_node", value=index, source=url)],
                recommendation="Add the most specific applicable schema.org @type.",
            ))
            continue
        for schema_type in types:
            missing = [
                field for field in _REQUIRED.get(str(schema_type), ())
                if document.get(field) in (None, "", [], {})
            ]
            if missing:
                findings.append(Finding(
                    code="jsonld_required_missing", category="structured_data",
                    severity=Severity.HIGH, title=f"Incomplete {schema_type} markup",
                    message=f"Required fields are missing: {', '.join(missing)}.",
                    url=url, evidence=[Evidence(
                        kind="jsonld_missing_fields",
                        value={"type": schema_type, "missing": missing, "node": index},
                        source=url,
                    )],
                    recommendation="Add only factual values visible or supported by the page.",
                ))
    return findings
