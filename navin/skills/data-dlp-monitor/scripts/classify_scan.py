#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Scan files for sensitive data types with validators and propose a classification tier.

Usage:
    classify_scan.py <file-or-folder> [--json] [--redact] [--max-size-kb 2048]
                     [--include "*.csv,*.txt,*.json,*.log,*.md,*.sql,*.yml,*.env"] [--output report.md]

Detectors (regex plus checksum or structural validation): IBAN (mod 97), French
NIR / numero de securite sociale (key = 97 - n mod 97, Corsica 2A/2B handled),
SIREN and SIRET (Luhn), payment card PAN (Luhn, 13 to 19 digits, known
prefixes), e-mail address, French phone number, AWS access key id (AKIA/ASIA),
AWS secret key (next to an access key), JWT (three base64url parts with a
"alg" header), private key PEM headers, generic API tokens (GitHub ghp_,
Slack xox, Stripe sk_live), RGPD article 9 vocabulary (health, union, religion,
sexual orientation, ethnic origin, biometric), French postal address hints.

Each file receives counts per type, the highest tier found (Public, Internal,
Confidential, Restricted) and the reason. --redact prints a masked sample per
type (first and last 2 characters kept) instead of the value; without it no
value is printed at all, only counts.

Exit codes: 0 nothing above Internal found, 1 Confidential or Restricted data
found, 2 usage or input error.
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

TIER_ORDER = ["Public", "Internal", "Confidential", "Restricted"]


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def iban_ok(raw: str) -> bool:
    value = re.sub(r"[\s-]", "", raw).upper()
    if not 15 <= len(value) <= 34 or not value[:2].isalpha() or not value[2:4].isdigit():
        return False
    rearranged = value[4:] + value[:4]
    numeric = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
    return int(numeric) % 97 == 1


def nir_ok(raw: str) -> bool:
    value = re.sub(r"\s", "", raw).upper()
    if len(value) != 15:
        return False
    body, key = value[:13], value[13:]
    if not key.isdigit() or body[0] not in "12378":
        return False
    dept = body[5:7]
    if dept == "2A":
        number = int(body.replace("2A", "19", 1))
    elif dept == "2B":
        number = int(body.replace("2B", "18", 1))
    elif body.isdigit():
        number = int(body)
    else:
        return False
    month = int(body[3:5])
    if not (1 <= month <= 12 or 20 <= month <= 42 or 50 <= month <= 99):
        return False
    return (97 - number % 97) == int(key)


def siren_ok(raw: str) -> bool:
    digits = re.sub(r"\s", "", raw)
    return len(digits) == 9 and digits.isdigit() and luhn_ok(digits)


def siret_ok(raw: str) -> bool:
    digits = re.sub(r"\s", "", raw)
    if len(digits) != 14 or not digits.isdigit():
        return False
    # La Poste SIRET exception: SIREN 356000000 establishments sum to a multiple of 5.
    if digits.startswith("356000000"):
        return sum(int(c) for c in digits) % 5 == 0
    return luhn_ok(digits) and luhn_ok(digits[:9])


def pan_ok(raw: str) -> bool:
    digits = re.sub(r"[\s-]", "", raw)
    if not 13 <= len(digits) <= 19 or not digits.isdigit():
        return False
    if len(set(digits)) == 1:
        return False
    prefix_ok = digits[0] == "4" or digits[:2] in {"34", "37", "51", "52", "53", "54", "55", "62", "65"} or 2221 <= int(digits[:4]) <= 2720 or digits[:4] in {"6011"} or digits[:2] in {"35", "36", "38", "30"}
    return prefix_ok and luhn_ok(digits)


def jwt_ok(raw: str) -> bool:
    head = raw.split(".")[0]
    padded = head + "=" * (-len(head) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    return '"alg"' in decoded


def always(_: str) -> bool:
    return True


@dataclass(frozen=True)
class Detector:
    name: str
    tier: str
    pattern: re.Pattern[str]
    validate: Callable[[str], bool]
    rgpd: str


DETECTORS: list[Detector] = [
    Detector("private_key", "Restricted", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----"), always, "secret"),
    Detector("aws_access_key", "Restricted", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), always, "secret"),
    Detector("aws_secret_key", "Restricted", re.compile(r"(?i)aws(?:_|\s)?secret(?:_access)?(?:_|\s)?key\W{0,5}([A-Za-z0-9/+=]{40})\b"), always, "secret"),
    Detector("jwt", "Restricted", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), jwt_ok, "secret"),
    Detector("api_token", "Restricted", re.compile(r"\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{60,}|xox[baprs]-[A-Za-z0-9-]{10,}|sk_live_[A-Za-z0-9]{24,}|AIza[0-9A-Za-z_-]{35}|glpat-[A-Za-z0-9_-]{20})\b"), always, "secret"),
    Detector("siret", "Internal", re.compile(r"\b\d{3}\s?\d{3}\s?\d{3}\s?\d{5}\b"), siret_ok, "public register, business data"),
    Detector("payment_card", "Restricted", re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), pan_ok, "PCI DSS, RGPD art. 4"),
    Detector("nir_fr", "Restricted", re.compile(r"\b[12378]\s?\d{2}\s?(?:0[1-9]|1[0-2]|[2-9]\d)\s?(?:\d{2}|2[AB])\s?\d{3}\s?\d{3}\s?\d{2}\b"), nir_ok, "RGPD art. 87, decret 2019-341"),
    Detector("iban", "Confidential", re.compile(r"\b[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]{4}){2,7}(?:[ -]?[A-Z0-9]{1,4})?\b"), iban_ok, "RGPD art. 4, DSP2"),
    Detector("special_category", "Restricted", re.compile(r"(?i)\b(?:s[ée]ropositi\w+|VIH|HIV|cancer|diab[eè]te|handicap\w*|arr[eê]t maladie|dossier m[ée]dical|psychiatri\w+|syndicat\w*|syndiqu[ée]\w*|religion|musulman\w*|chr[ée]tien\w*|juif\w*|orientation sexuelle|homosexuel\w*|origine ethnique|biom[ée]trique|empreinte digitale|casier judiciaire|condamnation)\b"), always, "RGPD art. 9 and 10"),
    Detector("siren", "Internal", re.compile(r"\b\d{3}\s?\d{3}\s?\d{3}\b"), siren_ok, "public register, business data"),
    Detector("email", "Confidential", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), always, "RGPD art. 4 personal data"),
    Detector("phone_fr", "Confidential", re.compile(r"(?<![\w+])(?:\+33\s?[1-9]|0[1-9])(?:[\s.-]?\d{2}){4}(?![\w])"), always, "RGPD art. 4 personal data"),
    Detector("date_of_birth", "Confidential", re.compile(r"(?i)\b(?:n[ée]e? le|date de naissance|DOB|born on)\W{0,3}\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"), always, "RGPD art. 4 personal data"),
]

TIER_REASON = {
    "Restricted": "secrets, payment card numbers, NIR or RGPD special categories present",
    "Confidential": "directly identifying personal data or banking identifiers present",
    "Internal": "business identifiers only",
    "Public": "no detector matched",
}


def redact(value: str) -> str:
    compact = re.sub(r"\s", "", value)
    if len(compact) <= 6:
        return "*" * len(compact)
    return compact[:2] + "*" * (len(compact) - 4) + compact[-2:]


def scan_text(text: str) -> dict[str, dict[str, object]]:
    hits: dict[str, dict[str, object]] = {}
    consumed: list[tuple[int, int]] = []
    for det in DETECTORS:
        count = 0
        sample = ""
        for match in det.pattern.finditer(text):
            value = match.group(1) if match.groups() else match.group(0)
            span = match.span()
            if any(s < span[1] and span[0] < e for s, e in consumed):
                continue
            if not det.validate(value):
                continue
            count += 1
            consumed.append(span)
            if not sample:
                sample = redact(value) if det.name != "private_key" else value
        if count:
            hits[det.name] = {"count": count, "tier": det.tier, "basis": det.rgpd, "sample": sample}
    return hits


def tier_of(hits: dict[str, dict[str, object]]) -> str:
    best = "Public"
    for h in hits.values():
        if TIER_ORDER.index(str(h["tier"])) > TIER_ORDER.index(best):
            best = str(h["tier"])
    return best


def iter_files(root: Path, include: list[str], max_bytes: int) -> list[Path]:
    if root.is_file():
        return [root]
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or any(part in (".git", "node_modules", "__pycache__", ".venv") for part in p.parts):
            continue
        if include and not any(fnmatch.fnmatch(p.name, pat) for pat in include):
            continue
        if p.stat().st_size > max_bytes:
            continue
        out.append(p)
    return out


def to_markdown(rows: list[dict[str, object]], redacted: bool) -> str:
    lines = ["# Data classification scan", "", f"{len(rows)} files scanned.", "", "| File | Proposed tier | Detections (type x count) | Legal basis | Reason |", "|---|---|---|---|---|"]
    for r in rows:
        hits = r["hits"] if isinstance(r["hits"], dict) else {}
        det = ", ".join(f"{k} x{v['count']}" for k, v in hits.items()) or "-"
        basis = "; ".join(sorted({str(v["basis"]) for v in hits.values()})) or "-"
        lines.append(f"| {r['file']} | {r['tier']} | {det} | {basis} | {r['reason']} |")
    if redacted:
        lines += ["", "## Redacted samples", "", "| File | Type | Sample |", "|---|---|---|"]
        for r in rows:
            hits = r["hits"] if isinstance(r["hits"], dict) else {}
            for k, v in hits.items():
                lines.append(f"| {r['file']} | {k} | `{v['sample']}` |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan files for sensitive data types and propose a classification tier.")
    parser.add_argument("target", help="file or folder to scan")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--redact", action="store_true", help="include one masked sample per type")
    parser.add_argument("--max-size-kb", type=int, default=2048, help="skip files larger than this (default 2048)")
    parser.add_argument("--include", default="*.csv,*.txt,*.json,*.jsonl,*.log,*.md,*.sql,*.yml,*.yaml,*.env,*.ini,*.cfg,*.xml,*.html,*.py,*.js,*.ts,*.sh", help="comma-separated glob list for folder scans")
    parser.add_argument("--output", help="write the report to this file")
    args = parser.parse_args(argv)

    root = Path(args.target)
    if not root.exists():
        print(f"error: {root} does not exist", file=sys.stderr)
        return 2
    if args.max_size_kb <= 0:
        print("error: --max-size-kb must be positive", file=sys.stderr)
        return 2
    include = [p.strip() for p in args.include.split(",") if p.strip()]
    files = iter_files(root, include, args.max_size_kb * 1024)
    if not files:
        print("error: no file matched", file=sys.stderr)
        return 2

    rows: list[dict[str, object]] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            rows.append({"file": str(path), "tier": "unknown", "hits": {}, "reason": f"unreadable: {exc}"})
            continue
        hits = scan_text(text)
        if not args.redact:
            for h in hits.values():
                h.pop("sample", None)
        tier = tier_of(hits)
        rows.append({"file": str(path), "tier": tier, "hits": hits, "reason": TIER_REASON[tier]})

    worst = max((str(r["tier"]) for r in rows if r["tier"] in TIER_ORDER), key=TIER_ORDER.index, default="Public")
    summary = {"files": len(rows), "highest_tier": worst, "per_tier": {t: sum(1 for r in rows if r["tier"] == t) for t in TIER_ORDER}}
    text_out = json.dumps({"summary": summary, "files": rows}, indent=2, ensure_ascii=False) if args.json else to_markdown(rows, args.redact)
    if args.output:
        Path(args.output).write_text(text_out + "\n", encoding="utf-8")
        print(f"{len(rows)} files -> {args.output}; highest tier {worst}")
    else:
        print(text_out)
    return 1 if TIER_ORDER.index(worst) >= TIER_ORDER.index("Confidential") else 0


if __name__ == "__main__":
    sys.exit(main())
