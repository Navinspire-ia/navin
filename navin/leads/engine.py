"""Leads pipeline: normalize -> dedupe -> qualify -> export (atomic).

Pure functions over lists of rows so the same core backs the agent tool, the
skill scripts, and any future API. Exports are atomic (tmp + os.replace) so a
crash mid-write never leaves a half-written CSV - the old scripts used a plain
``open("w")`` and could corrupt the sales file.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from navin.leads.normalize import company_dedupe_key
from navin.leads.schema import CANONICAL_FIELDS, Lead, validate_lead
from navin.utils.atomic_io import atomic_write_bytes, atomic_write_text

_EXPORT_FORMATS = ("csv", "json", "jsonl", "xlsx")


def normalize_leads(
    rows: list[dict[str, Any]], *, default_country: str | None = None
) -> list[Lead]:
    return [Lead.from_row(r).normalized(default_country=default_country) for r in rows]


def _dedupe_key(lead: Lead) -> str:
    """Stable identity for a lead. Email is strongest; else person@domain; else company."""
    if lead.email:
        return f"email:{lead.email}"
    if lead.domain and (lead.first_name or lead.last_name):
        who = f"{lead.first_name}.{lead.last_name}".lower()
        return f"person:{who}@{lead.domain}"
    if lead.domain:
        return f"domain:{lead.domain}"
    key = company_dedupe_key(lead.company)
    return f"company:{key}" if key else f"id:{id(lead)}"


_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1, "": 0}


def dedupe_leads(leads: list[Lead]) -> tuple[list[Lead], int]:
    """Merge duplicates by identity key, keeping the richest record.

    Returns (unique_leads, removed_count). When two rows collide, fields present
    on one but blank on the other are merged in, and the higher confidence wins.
    """
    merged: dict[str, Lead] = {}
    removed = 0
    for lead in leads:
        key = _dedupe_key(lead)
        existing = merged.get(key)
        if existing is None:
            merged[key] = lead
            continue
        removed += 1
        merged[key] = _merge(existing, lead)
    return list(merged.values()), removed


def _merge(a: Lead, b: Lead) -> Lead:
    """Fill blanks in *a* from *b*; keep the stronger confidence/score."""
    winner = a
    for fname in CANONICAL_FIELDS:
        if fname in ("confidence", "score", "tier"):
            continue
        if not getattr(winner, fname) and getattr(b, fname):
            setattr(winner, fname, getattr(b, fname))
    if _CONFIDENCE_RANK.get(b.confidence, 0) > _CONFIDENCE_RANK.get(winner.confidence, 0):
        winner.confidence = b.confidence
    winner.score = max(winner.score, b.score)
    merged_extra = dict(b.extra)
    merged_extra.update(winner.extra)
    winner.extra = merged_extra
    return winner


def qualify_tier(lead: Lead) -> str:
    """Assign an A/B/C tier from a deterministic completeness + confidence score."""
    score = lead.score
    if not score:
        score = _completeness_score(lead)
    if score >= 70:
        return "A"
    if score >= 40:
        return "B"
    return "C"


def _completeness_score(lead: Lead) -> int:
    score = 0
    if lead.email and lead.email_status == "verified":
        score += 35
    elif lead.email:
        score += 20
    if lead.phone:
        score += 15
    if lead.person and lead.role:
        score += 15
    if lead.domain:
        score += 10
    if lead.signal:
        score += 10
    score += {"high": 15, "medium": 8, "low": 3}.get(lead.confidence, 0)
    return min(score, 100)


def summarize_leads(leads: list[Lead]) -> dict[str, Any]:
    total = len(leads)
    with_email = sum(1 for x in leads if x.email)
    verified = sum(1 for x in leads if x.email_status == "verified")
    with_phone = sum(1 for x in leads if x.phone)
    tiers: dict[str, int] = {}
    issues = 0
    for lead in leads:
        tiers[lead.tier or "?"] = tiers.get(lead.tier or "?", 0) + 1
        if validate_lead(lead):
            issues += 1
    return {
        "total": total,
        "with_email": with_email,
        "verified_email": verified,
        "with_phone": with_phone,
        "tiers": tiers,
        "rows_with_issues": issues,
    }


@dataclass(frozen=True)
class LeadExport:
    path: str
    format: str
    count: int


def _rows_for_export(leads: list[Lead]) -> list[dict[str, Any]]:
    return [lead.to_row() for lead in leads]


def export_leads(leads: list[Lead], fmt: str, path: str | Path) -> LeadExport:
    """Atomically write leads to csv/json/jsonl/xlsx."""
    fmt = (fmt or "csv").strip().lower()
    out = Path(path)
    rows = _rows_for_export(leads)
    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(CANONICAL_FIELDS))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in CANONICAL_FIELDS})
        atomic_write_text(out, buffer.getvalue())
    elif fmt == "json":
        atomic_write_text(out, json.dumps(rows, ensure_ascii=False, indent=2))
    elif fmt == "jsonl":
        body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
        atomic_write_text(out, body + ("\n" if body else ""))
    elif fmt in ("xlsx", "excel"):
        _export_xlsx(rows, out)
    else:
        raise ValueError(
            f"unsupported format '{fmt}' (csv, json, jsonl, xlsx)"
        )
    return LeadExport(path=str(out), format=fmt, count=len(rows))


def _export_xlsx(rows: list[dict[str, Any]], out: Path) -> None:
    try:
        from openpyxl import Workbook
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ValueError("xlsx export needs openpyxl") from exc
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "leads"
    sheet.append(list(CANONICAL_FIELDS))
    for row in rows:
        sheet.append([row.get(k, "") for k in CANONICAL_FIELDS])
    buffer = io.BytesIO()
    workbook.save(buffer)
    atomic_write_bytes(out, buffer.getvalue())
