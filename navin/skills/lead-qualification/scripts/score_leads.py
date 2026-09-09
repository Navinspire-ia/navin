#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Validate and score lead CSVs for the Leads studio.

Canonical columns (subset required for validate):
  company, website, source, confidence
Optional scoring columns (0-5): fit, need, timing, authority, budget
Optional: icp_score (0-100) used as-is when BANT columns absent

Weights: fit 0.30, need 0.25, timing 0.20, authority 0.15, budget 0.10
Tiers: A >=70, B >=40, else C
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

REQUIRED = ("company", "website", "source", "confidence")
BANT = ("fit", "need", "timing", "authority", "budget")
WEIGHTS = {
    "fit": 0.30,
    "need": 0.25,
    "timing": 0.20,
    "authority": 0.15,
    "budget": 0.10,
}
URL_RE = re.compile(r"^https?://", re.I)


def _tier(score: float) -> str:
    if score >= 70:
        return "A"
    if score >= 40:
        return "B"
    return "C"


def _valid_url(value: str) -> bool:
    value = (value or "").strip()
    if not value:
        return False
    if not URL_RE.match(value):
        value = "https://" + value
    parsed = urlparse(value)
    return bool(parsed.netloc and "." in parsed.netloc)


def validate(rows: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    if not rows:
        return ["CSV has no data rows"]
    # rows already normalized to lower keys
    for col in REQUIRED:
        if col not in rows[0]:
            errors.append(f"missing column: {col}")
    if errors:
        return errors

    seen_domains: set[str] = set()
    for i, row in enumerate(rows, start=2):
        company = (row.get("company") or "").strip()
        if not company:
            errors.append(f"row {i}: empty company")
        website = (row.get("website") or "").strip()
        if not _valid_url(website):
            errors.append(f"row {i}: invalid website '{website}'")
        else:
            host = urlparse(
                website if URL_RE.match(website) else "https://" + website
            ).netloc.lower().removeprefix("www.")
            if host in seen_domains:
                errors.append(f"row {i}: duplicate domain {host}")
            seen_domains.add(host)
        source = (row.get("source") or "").strip()
        if not source:
            errors.append(f"row {i}: empty source")
        elif source.lower() != "unverified" and not _valid_url(source):
            # allow non-URL sources like "Pappers search" but prefer URLs
            pass
        conf = (row.get("confidence") or "").strip().lower()
        if conf not in {"high", "medium", "med", "low", "unverified", "h", "m", "l"}:
            errors.append(
                f"row {i}: confidence should be high|medium|low|unverified (got '{conf}')"
            )
    return errors


def score_row(row: dict[str, str]) -> tuple[float, str]:
    if all((row.get(k) or "").strip() != "" for k in BANT):
        total = 0.0
        for key, weight in WEIGHTS.items():
            try:
                val = float(row[key])
            except ValueError:
                val = 0.0
            val = max(0.0, min(5.0, val))
            total += (val / 5.0) * 100.0 * weight
        return round(total, 1), _tier(total)
    raw = (row.get("icp_score") or "").strip()
    if raw:
        try:
            total = max(0.0, min(100.0, float(raw)))
        except ValueError:
            total = 0.0
        return total, _tier(total)
    return 0.0, "C"


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return []
        rows = []
        for raw in reader:
            rows.append({(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()})
        return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    rows = _read(args.csv_path)
    errors = validate(rows)
    if errors:
        print("VALIDATION FAILED", file=sys.stderr)
        for err in errors:
            print(f"- {err}", file=sys.stderr)
        raise SystemExit(1)
    print(f"VALIDATION OK ({len(rows)} rows)")

    if args.validate_only:
        return

    out_rows = []
    for row in rows:
        total, tier = score_row(row)
        enriched = dict(row)
        enriched["icp_score"] = str(total)
        enriched["tier"] = tier
        if total >= 70:
            enriched.setdefault("next_action", "contact_now")
        elif total >= 40:
            enriched.setdefault("next_action", "nurture")
        else:
            enriched.setdefault("next_action", "discard_or_revisit")
        out_rows.append(enriched)

    out_path = args.output or args.csv_path.with_name(args.csv_path.stem + "-scored.csv")
    fieldnames = list(out_rows[0].keys())
    # Atomic write: never leave a half-written scored CSV on crash.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{out_path.name}.", suffix=".tmp", dir=str(out_path.parent)
    )
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(out_rows)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, out_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    a = sum(1 for r in out_rows if r["tier"] == "A")
    b = sum(1 for r in out_rows if r["tier"] == "B")
    c = sum(1 for r in out_rows if r["tier"] == "C")
    print(f"Wrote {out_path} (A={a} B={b} C={c})")


if __name__ == "__main__":
    main()
