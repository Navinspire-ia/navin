#!/usr/bin/env python3
"""Enrich lead CSVs with Hunter / Apollo when API keys are present.

Reads a prospects CSV, optionally looks up emails/people via public APIs,
and writes an enriched CSV with honest verification status.

Env:
  HUNTER_API_KEY   - domain-search, email-finder, email-verifier
  APOLLO_API_KEY   - mixed people search

Without keys, rows keep existing contact hints and email_status=unverified.
Never invents verified emails.

Canonical input columns (flexible): company, website, person, first_name,
last_name, domain, email, contact_hint, source, confidence
Output adds/updates: email, email_status, email_score, enrichment_source
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

URL_RE = re.compile(r"^https?://", re.I)


def _domain_from_website(website: str) -> str:
    value = (website or "").strip()
    if not value:
        return ""
    if not URL_RE.match(value):
        value = "https://" + value
    host = urlparse(value).netloc.lower().removeprefix("www.")
    return host


def _split_name(person: str) -> tuple[str, str]:
    parts = [p for p in (person or "").strip().split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def _http_json(url: str, *, method: str = "GET", body: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    data = None
    req_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {exc.code} for {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc}") from exc
    return json.loads(payload) if payload else {}


def hunter_find_email(
    *,
    api_key: str,
    domain: str,
    first_name: str,
    last_name: str,
) -> dict[str, str]:
    if not domain or not (first_name or last_name):
        return {}
    query = urllib.parse.urlencode(
        {
            "domain": domain,
            "first_name": first_name,
            "last_name": last_name,
            "api_key": api_key,
        }
    )
    payload = _http_json(f"https://api.hunter.io/v2/email-finder?{query}")
    data = payload.get("data") or {}
    email = (data.get("email") or "").strip()
    if not email:
        return {}
    status = (data.get("verification") or {}).get("status") or data.get("score")
    score = data.get("score")
    verified = str(status).lower() in {"valid", "verified"} or (
        isinstance(score, (int, float)) and float(score) >= 90
    )
    return {
        "email": email,
        "email_status": "verified" if verified else "unverified",
        "email_score": str(score if score is not None else ""),
        "enrichment_source": "hunter_email_finder",
    }


def hunter_verify_email(*, api_key: str, email: str) -> dict[str, str]:
    email = (email or "").strip()
    if not email or "@" not in email:
        return {}
    query = urllib.parse.urlencode({"email": email, "api_key": api_key})
    payload = _http_json(f"https://api.hunter.io/v2/email-verifier?{query}")
    data = payload.get("data") or {}
    status = str(data.get("status") or "").lower()
    score = data.get("score")
    mapping = {
        "valid": "verified",
        "accept_all": "catch_all",
        "webmail": "unverified",
        "invalid": "invalid",
        "unknown": "unverified",
        "disposable": "invalid",
    }
    return {
        "email": email,
        "email_status": mapping.get(status, "unverified"),
        "email_score": str(score if score is not None else ""),
        "enrichment_source": "hunter_email_verifier",
    }


def hunter_domain_emails(*, api_key: str, domain: str, limit: int = 3) -> list[dict[str, str]]:
    if not domain:
        return []
    query = urllib.parse.urlencode(
        {"domain": domain, "api_key": api_key, "limit": str(limit)}
    )
    payload = _http_json(f"https://api.hunter.io/v2/domain-search?{query}")
    data = payload.get("data") or {}
    out: list[dict[str, str]] = []
    for item in data.get("emails") or []:
        email = (item.get("value") or "").strip()
        if not email:
            continue
        conf = str(item.get("confidence") or "")
        try:
            verified = float(conf) >= 90
        except ValueError:
            verified = False
        out.append(
            {
                "email": email,
                "email_status": "verified" if verified else "unverified",
                "email_score": conf,
                "enrichment_source": "hunter_domain_search",
                "person": f"{item.get('first_name') or ''} {item.get('last_name') or ''}".strip(),
                "role": (item.get("position") or "").strip(),
            }
        )
    return out


def apollo_people(*, api_key: str, domain: str, person: str) -> dict[str, str]:
    if not domain:
        return {}
    body: dict[str, Any] = {
        "q_organization_domains": domain,
        "page": 1,
        "per_page": 5,
    }
    if person.strip():
        body["q_keywords"] = person.strip()
    payload = _http_json(
        "https://api.apollo.io/api/v1/mixed_people/search",
        method="POST",
        body=body,
        headers={"X-Api-Key": api_key, "Cache-Control": "no-cache"},
    )
    people = payload.get("people") or []
    if not people:
        return {}
    top = people[0]
    email = (top.get("email") or "").strip()
    # Apollo free tiers often return null email - keep honest status.
    status = "unverified"
    if email and not email.endswith("email_not_unlocked@"):
        # Only mark verified when Apollo explicitly says so.
        if str(top.get("email_status") or "").lower() in {"verified", "valid"}:
            status = "verified"
        else:
            status = "unverified"
    else:
        email = ""
    return {
        "email": email,
        "email_status": status if email else "unverified",
        "email_score": "",
        "enrichment_source": "apollo_people_search",
        "person": f"{top.get('first_name') or ''} {top.get('last_name') or ''}".strip()
        or person,
        "role": (top.get("title") or "").strip(),
        "profile_url": (top.get("linkedin_url") or "").strip(),
    }


def enrich_row(
    row: dict[str, str],
    *,
    hunter_key: str,
    apollo_key: str,
    verify_existing: bool,
) -> dict[str, str]:
    out = dict(row)
    domain = (out.get("domain") or "").strip() or _domain_from_website(out.get("website") or "")
    if domain and not out.get("domain"):
        out["domain"] = domain

    first = (out.get("first_name") or "").strip()
    last = (out.get("last_name") or "").strip()
    if not first and not last:
        first, last = _split_name(out.get("person") or "")

    existing_email = (out.get("email") or out.get("contact_hint") or "").strip()
    if existing_email and "@" not in existing_email:
        existing_email = ""

    try:
        if hunter_key and existing_email and verify_existing:
            verified = hunter_verify_email(api_key=hunter_key, email=existing_email)
            if verified:
                out.update({k: v for k, v in verified.items() if v})
                return out

        if hunter_key and domain and (first or last):
            found = hunter_find_email(
                api_key=hunter_key,
                domain=domain,
                first_name=first,
                last_name=last,
            )
            if found:
                out.update({k: v for k, v in found.items() if v})
                return out

        if hunter_key and domain and not out.get("email"):
            domain_hits = hunter_domain_emails(api_key=hunter_key, domain=domain, limit=1)
            if domain_hits:
                hit = domain_hits[0]
                for key in ("email", "email_status", "email_score", "enrichment_source"):
                    if hit.get(key):
                        out[key] = hit[key]
                if hit.get("person") and not out.get("person"):
                    out["person"] = hit["person"]
                if hit.get("role") and not out.get("role"):
                    out["role"] = hit["role"]
                return out

        if apollo_key and domain:
            found = apollo_people(
                api_key=apollo_key,
                domain=domain,
                person=out.get("person") or f"{first} {last}".strip(),
            )
            if found:
                for key, value in found.items():
                    if value and (key not in out or not out.get(key)):
                        out[key] = value
                return out
    except RuntimeError as exc:
        out["enrichment_error"] = str(exc)[:200]
        out.setdefault("email_status", "unverified")
        return out

    # Fallback: keep pattern hints honest.
    if existing_email and not out.get("email"):
        out["email"] = existing_email
    out.setdefault("email_status", "unverified")
    out.setdefault("enrichment_source", "none")
    return out


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
    parser.add_argument("csv_path", type=Path, nargs="?")
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help="Re-verify emails already present via Hunter email-verifier",
    )
    parser.add_argument(
        "--keys-check",
        action="store_true",
        help="Print which enrichment keys are set and exit",
    )
    args = parser.parse_args()

    hunter_key = (os.environ.get("HUNTER_API_KEY") or "").strip()
    apollo_key = (os.environ.get("APOLLO_API_KEY") or "").strip()

    if args.keys_check:
        print(f"HUNTER_API_KEY={'set' if hunter_key else 'missing'}")
        print(f"APOLLO_API_KEY={'set' if apollo_key else 'missing'}")
        raise SystemExit(0 if (hunter_key or apollo_key) else 2)

    if args.csv_path is None:
        parser.error("csv_path is required unless --keys-check is set")

    rows = _read(args.csv_path)
    if not rows:
        print("CSV has no data rows", file=sys.stderr)
        raise SystemExit(1)

    enriched = [
        enrich_row(
            row,
            hunter_key=hunter_key,
            apollo_key=apollo_key,
            verify_existing=args.verify_existing,
        )
        for row in rows
    ]

    out_path = args.output or args.csv_path.with_name(args.csv_path.stem + "-enriched.csv")
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in enriched:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    # Atomic write: a crash mid-write must not corrupt the sales CSV. Write to a
    # sibling temp file, then os.replace (atomic on the same filesystem).
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{out_path.name}.", suffix=".tmp", dir=str(out_path.parent)
    )
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(enriched)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, out_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    verified = sum(1 for r in enriched if (r.get("email_status") or "").lower() == "verified")
    print(
        f"Wrote {out_path} ({len(enriched)} rows, verified={verified}, "
        f"hunter={'yes' if hunter_key else 'no'}, apollo={'yes' if apollo_key else 'no'})"
    )
    if not hunter_key and not apollo_key:
        print(
            "NOTE: no HUNTER_API_KEY / APOLLO_API_KEY - emails stay unverified. "
            "Enable Exa/Firecrawl MCP for deeper public research, then re-run enrich.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
