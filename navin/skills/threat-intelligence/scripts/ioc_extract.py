#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Extract, refang, classify and deduplicate indicators of compromise from text.

Usage:
    ioc_extract.py <report.txt|report.md|->  [--format json|csv|markdown] [--json]
                   [--keep-private] [--keep-common] [--types ipv4,domain,...]
                   [--extra-exclusions domains.txt] [--output iocs.json]

Reads a text file (or stdin with "-"), refangs defanged notation (hxxp, [.],
(.), [@], [:], \\.), and extracts: md5, sha1, sha256, ipv4, ipv6, domain, url,
email, filename, cve, mutex, yara (rule names), btc. Values are
deduplicated case-insensitively and excluded when they match a warninglist-
style allowlist: RFC 1918 and other special-purpose IPv4/IPv6 ranges, well-known
domains (microsoft.com, google.com, ...) and documentation TLDs. Every
indicator carries its type, the refanged value, the original defanged form,
how many times it appeared and a confidence hint.

Exit codes: 0 no indicator found, 1 at least one indicator kept, 2 usage or
input error.
"""

from __future__ import annotations

import argparse
import csv
import io
import ipaddress
import json
import re
import sys
from pathlib import Path
from typing import Any


def _unhxxp(match: re.Match[str]) -> str:
    return match.group(0).lower().replace("hxxp", "http")


DEFANG_RULES: list[tuple[re.Pattern[str], Any]] = [
    (re.compile(r"hxxps?://", re.IGNORECASE), _unhxxp),
    (re.compile(r"\[\.\]|\(\.\)|\{\.\}|\[dot\]|\(dot\)| \. ", re.IGNORECASE), "."),
    (re.compile(r"\[@\]|\(@\)|\[at\]", re.IGNORECASE), "@"),
    (re.compile(r"\[:\]|\[://\]"), ":"),
    (re.compile(r"\\\."), "."),
    (re.compile(r"\[(https?)\]", re.IGNORECASE), r"\1"),
]

TLD_STOP = {
    "exe", "dll", "ps1", "bat", "cmd", "vbs", "js", "jar", "zip", "rar", "7z", "doc", "docx", "docm",
    "xls", "xlsx", "xlsm", "pdf", "txt", "log", "json", "yml", "yaml", "py", "sh", "so", "bin",
    "dat", "tmp", "lnk", "iso", "img", "msi", "scr", "hta", "sys", "cfg", "ini", "md", "html",
    "css", "png", "jpg", "gif", "svg", "local", "localdomain", "internal", "lan", "corp", "home",
    "test", "example", "invalid", "localhost",
}

COMMON_DOMAINS = {
    "microsoft.com", "windows.com", "windowsupdate.com", "office.com", "live.com", "outlook.com",
    "azure.com", "azureedge.net", "google.com", "googleapis.com", "gstatic.com", "youtube.com",
    "gmail.com", "apple.com", "icloud.com", "amazon.com", "amazonaws.com", "cloudfront.net",
    "akamai.net", "akamaihd.net", "cloudflare.com", "github.com", "githubusercontent.com",
    "mozilla.org", "mozilla.net", "debian.org", "ubuntu.com", "python.org", "pypi.org",
    "npmjs.com", "docker.com", "docker.io", "wikipedia.org", "w3.org", "schema.org", "mitre.org",
    "attack.mitre.org", "virustotal.com", "abuse.ch", "misp-project.org", "oasis-open.org",
    "example.com", "example.org", "example.net", "facebook.com", "twitter.com", "linkedin.com",
    "adobe.com", "digicert.com", "letsencrypt.org", "sectigo.com", "globalsign.com", "verisign.com",
    "ocsp.digicert.com", "crl.microsoft.com", "pki.goog", "dropbox.com", "slack.com", "zoom.us",
}

PATTERNS: dict[str, re.Pattern[str]] = {
    "sha256": re.compile(r"\b[a-fA-F0-9]{64}\b"),
    "sha1": re.compile(r"\b[a-fA-F0-9]{40}\b"),
    "md5": re.compile(r"\b[a-fA-F0-9]{32}\b"),
    "url": re.compile(r'\b(?:https?|ftp)://[^\s"\'<>()\[\]]+', re.IGNORECASE),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "ipv4": re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
    "ipv6": re.compile(r"(?<![:\w.])(?:[A-Fa-f0-9]{1,4})?(?::[A-Fa-f0-9]{0,4}){2,7}(?![:\w])"),
    "domain": re.compile(r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z][a-zA-Z0-9-]{1,24}\b"),
    "cve": re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE),
    "filename": re.compile(
        r"(?<![\w.-])[\w-][\w.-]{0,80}\.(?:exe|dll|ps1|bat|cmd|vbs|js|jar|zip|rar|7z|docm|xlsm|doc|xls|pdf|lnk|iso|img|msi|scr|hta|sys|elf|sh|bin|dat)\b",
        re.IGNORECASE,
    ),
    "mutex": re.compile(r"\b(?:Global|Local)\\[\w{}\-.]{4,80}"),
    "yara": re.compile(r"\brule\s+([A-Za-z_][A-Za-z0-9_]{2,80})\s*(?::\s*[\w\s]+)?\{"),
    "btc": re.compile(r"\b(?:bc1[a-zA-HJ-NP-Z0-9]{25,62}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b"),
}

ORDER = ["sha256", "sha1", "md5", "url", "email", "ipv4", "ipv6", "domain", "cve", "filename", "mutex", "yara", "btc"]


def refang(text: str) -> str:
    out = text
    for pattern, repl in DEFANG_RULES:
        out = pattern.sub(repl, out)
    return out


def is_private_ip(value: str) -> bool:
    host = value
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return True
    return bool(
        addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
        or addr.is_reserved or addr.is_unspecified or getattr(addr, "is_site_local", False)
    )


def registrable(domain: str) -> str:
    parts = domain.lower().split(".")
    second_level = {"co", "com", "org", "net", "gov", "ac", "edu", "gouv", "asso"}
    if len(parts) >= 3 and parts[-2] in second_level and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_common_domain(domain: str, extra: set[str]) -> bool:
    reg = registrable(domain)
    return reg in COMMON_DOMAINS or reg in extra or domain.lower() in extra


def valid_domain(value: str) -> bool:
    tld = value.rsplit(".", 1)[-1].lower()
    if tld in TLD_STOP or tld.isdigit():
        return False
    if value.replace(".", "").isdigit():
        return False
    return len(value) <= 253


def confidence_hint(kind: str, value: str) -> str:
    if kind in ("sha256", "sha1", "md5", "mutex", "yara"):
        return "high"
    if kind in ("url", "email", "btc", "cve"):
        return "medium"
    if kind == "domain" and value.count(".") >= 2:
        return "medium"
    return "low"


def extract(text: str, wanted: set[str], keep_private: bool, keep_common: bool, extra: set[str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    refanged = refang(text)
    found: dict[tuple[str, str], dict[str, Any]] = {}
    excluded: dict[str, int] = {}
    consumed: list[str] = []

    lowered = text.lower()

    def add(kind: str, value: str) -> None:
        key = (kind, value.lower())
        if key in found:
            found[key]["count"] += 1
            return
        found[key] = {
            "type": kind,
            "value": value,
            "defanged_seen": value.lower() not in lowered,
            "count": 1,
            "confidence": confidence_hint(kind, value),
        }

    for kind in ORDER:
        if kind not in wanted:
            continue
        for match in PATTERNS[kind].finditer(refanged):
            value = match.group(1) if kind == "yara" else match.group(0)
            value = value.rstrip(".,;:)")
            if kind in ("sha1", "md5") and any(value.lower() in c for c in consumed):
                continue
            if kind == "domain":
                if any(value.lower() in c for c in consumed):
                    continue
                if not valid_domain(value):
                    continue
                if not keep_common and is_common_domain(value, extra):
                    excluded["common_domain"] = excluded.get("common_domain", 0) + 1
                    continue
            if kind == "ipv4" and not keep_private and is_private_ip(value):
                excluded["private_ip"] = excluded.get("private_ip", 0) + 1
                continue
            if kind == "ipv6":
                if value.count(":") < 2:
                    continue
                try:
                    ipaddress.IPv6Address(value)
                except ipaddress.AddressValueError:
                    continue
                if not keep_private and is_private_ip(value):
                    excluded["private_ip"] = excluded.get("private_ip", 0) + 1
                    continue
            if kind == "email":
                dom = value.split("@", 1)[1]
                if not keep_common and is_common_domain(dom, extra):
                    excluded["common_email_domain"] = excluded.get("common_email_domain", 0) + 1
                    continue
            if kind == "url":
                host = re.sub(r"^[a-z]+://", "", value.lower()).split("/")[0].split(":")[0]
                if not keep_common and is_common_domain(host, extra) and not re.search(r"/[^/]+\.(exe|zip|ps1|dll|hta|iso)", value, re.IGNORECASE):
                    excluded["common_url"] = excluded.get("common_url", 0) + 1
                    continue
            if kind == "filename" and any(value.lower() in c for c in consumed):
                continue
            consumed.append(value.lower())
            add(kind, value)
    rows = sorted(found.values(), key=lambda r: (ORDER.index(r["type"]), r["value"].lower()))
    return rows, excluded


def render(rows: list[dict[str, Any]], excluded: dict[str, int], fmt: str, source: str) -> str:
    if fmt == "json":
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["type"]] = counts.get(r["type"], 0) + 1
        return json.dumps({"source": source, "total": len(rows), "per_type": counts, "excluded": excluded, "iocs": rows}, indent=2)
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["type", "value", "defanged_seen", "count", "confidence"])
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
        return buf.getvalue().rstrip("\n")
    lines = [f"# IOCs extracted from {source}", "", f"{len(rows)} indicators kept; excluded: " + (", ".join(f"{k}={v}" for k, v in sorted(excluded.items())) or "none"), "",
             "| Type | Value (refanged) | Seen defanged | Count | Confidence hint |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['type']} | `{r['value']}` | {'yes' if r['defanged_seen'] else 'no'} | {r['count']} | {r['confidence']} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract and refang IOCs from a text report.")
    parser.add_argument("input", help="text/markdown file, or - for stdin")
    parser.add_argument("--format", choices=["json", "csv", "markdown"], default="markdown")
    parser.add_argument("--json", action="store_true", help="shortcut for --format json")
    parser.add_argument("--types", help="comma-separated subset of " + ",".join(ORDER))
    parser.add_argument("--keep-private", action="store_true", help="keep RFC 1918 and special-purpose addresses")
    parser.add_argument("--keep-common", action="store_true", help="keep well-known vendor domains")
    parser.add_argument("--extra-exclusions", help="file with one domain per line to exclude (customer warninglist)")
    parser.add_argument("--output", help="write the result to this file")
    args = parser.parse_args(argv)

    if args.input == "-":
        text = sys.stdin.read()
        source = "stdin"
    else:
        path = Path(args.input)
        if not path.is_file():
            print(f"error: {path} is not a file", file=sys.stderr)
            return 2
        text = path.read_text(encoding="utf-8", errors="replace")
        source = path.name
    wanted = set(ORDER)
    if args.types:
        wanted = {t.strip().lower() for t in args.types.split(",") if t.strip()}
        unknown = wanted - set(ORDER)
        if unknown:
            print(f"error: unknown types {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
    extra: set[str] = set()
    if args.extra_exclusions:
        ex_path = Path(args.extra_exclusions)
        if not ex_path.is_file():
            print(f"error: {ex_path} is not a file", file=sys.stderr)
            return 2
        extra = {line.strip().lower() for line in ex_path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")}

    rows, excluded = extract(text, wanted, args.keep_private, args.keep_common, extra)
    fmt = "json" if args.json else args.format
    out = render(rows, excluded, fmt, source)
    if args.output:
        Path(args.output).write_text(out + "\n", encoding="utf-8")
        print(f"{len(rows)} indicators -> {args.output}")
    else:
        print(out)
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
