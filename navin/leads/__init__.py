"""Deterministic leads engine: normalize, validate, dedupe, qualify, export.

Prospecting *breadth* stays agent-driven (search / scrape / directories), but
the shape of the data must not depend on the model: a lead pipeline that emits
CRM-ready rows needs reproducible email/phone/domain normalization, a strict
schema, stable dedupe keys, and atomic file writes. That is what this package
provides, so the agent produces volume while the engine guarantees quality.
"""

from __future__ import annotations

from navin.leads.engine import (
    LeadExport,
    dedupe_leads,
    export_leads,
    normalize_leads,
    qualify_tier,
    summarize_leads,
)
from navin.leads.normalize import (
    normalize_company,
    normalize_domain,
    normalize_email,
    normalize_phone,
    split_person_name,
)
from navin.leads.schema import CANONICAL_FIELDS, Lead, validate_lead

__all__ = [
    "CANONICAL_FIELDS",
    "Lead",
    "LeadExport",
    "dedupe_leads",
    "export_leads",
    "normalize_company",
    "normalize_domain",
    "normalize_email",
    "normalize_leads",
    "normalize_phone",
    "qualify_tier",
    "split_person_name",
    "summarize_leads",
    "validate_lead",
]
