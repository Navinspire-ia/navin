# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Open-web discovery for Leads. Never fetches LinkedIn or social logins."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from loguru import logger

from navin.leads.normalize import (
    normalize_company,
    normalize_domain,
    normalize_email,
    normalize_phone,
)

MAX_QUERIES = 2
MAX_RESULTS = 6
MAX_FETCHES = 4

# Same contract as NAVIN_MARKETING_OFFLINE: the defaults never reach the network,
# injected search / fetch functions still run. The test suite sets it.
OFFLINE_FLAG = "NAVIN_LEADS_OFFLINE"


def offline() -> bool:
    import os

    return str(os.environ.get(OFFLINE_FLAG) or "").strip().lower() in {"1", "true", "yes", "on"}

_HIT_RE = re.compile(
    r"^\s*\d+\.\s+(?P<title>.+?)\s*\n\s+(?P<url>https?://\S+)",
    re.M,
)
_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
_PHONE_RE = re.compile(r"(?:\+|00)[1-9][\d\s.\-]{7,16}\d")
_BLOCKED_HOSTS = (
    "linkedin.com",
    "lnkd.in",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "youtube.com",
    "apollo.io",
    "hunter.io",
    "zoominfo.com",
)
_DIRECTORY_HOSTS = (
    "societe.com",
    "pappers.fr",
    "societeinfo.com",
    "infogreffe.fr",
    "verif.com",
    "manageo.fr",
    "companieshouse.gov.uk",
    "opencorporates.com",
    "crunchbase.com",
    "wikipedia.org",
    "wikidata.org",
    "pagesjaunes.fr",
    "yelp.com",
    "google.com",
    "maps.google.com",
    # People databases, phone books and job boards outrank small company sites
    # on a "<name> official website" query; none of them is the company.
    "rocketreach.co",
    "signalhire.com",
    "contactout.com",
    "lusha.com",
    "kaspr.io",
    "cognism.com",
    "dnb.com",
    "owler.com",
    "kompass.com",
    "europages.com",
    "telephone.fr",
    "118712.fr",
    "118000.fr",
    "infobel.com",
    "hoodspot.fr",
    "annuaire.com",
    "cylex.fr",
    "tuugo.fr",
    "glassdoor.com",
    "glassdoor.fr",
    "indeed.com",
    "indeed.fr",
    "welcometothejungle.com",
    "hellowork.com",
    "jobteaser.com",
    "apec.fr",
    "francetravail.fr",
    "pole-emploi.fr",
    "stepstone.fr",
    "monster.fr",
    "cadremploi.fr",
    "viadeo.com",
    "xing.com",
    "sortlist.com",
    "sortlist.fr",
    "clutch.co",
    "trustpilot.com",
    "trustpilot.fr",
    "capterra.com",
    "g2.com",
)
_NAME_NOISE = frozenset(
    {
        "sas", "sasu", "sarl", "eurl", "sa", "sca", "snc", "sci", "scop", "scm", "ltd",
        "limited", "plc", "llc", "inc", "corp", "co", "gmbh", "ag", "kg", "bv", "nv",
        "srl", "spa", "sl", "sau", "oy", "ab", "as", "the", "and", "et", "de", "du",
        "des", "la", "le", "les", "groupe", "group", "holding", "company", "compagnie",
        "societe", "société", "agence", "agency", "studio", "cabinet", "conseil",
        "consulting", "services", "solutions", "france", "international", "global",
    }
)


def _name_tokens(company: str) -> list[str]:
    """Distinctive lowercase ASCII words of a company name (legal forms dropped)."""
    import unicodedata

    folded = unicodedata.normalize("NFKD", str(company or "")).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", folded.lower())
    kept = [word for word in words if word not in _NAME_NOISE and (len(word) >= 3 or word.isdigit() or any(ch.isdigit() for ch in word))]
    return kept or [word for word in words if word not in _NAME_NOISE]


def domain_matches_company(domain: str, company: str) -> bool:
    """True when the domain visibly belongs to the company: a distinctive word of the
    name is in the domain label, or the label is the name glued together (or its acronym)."""
    label = re.sub(r"[^a-z0-9]", "", str(domain or "").lower().split(".")[0])
    tokens = _name_tokens(company)
    if not label or not tokens:
        return False
    if any(token in label for token in tokens if len(token) >= 3 or token.isdigit()):
        return True
    glued = "".join(tokens)
    if len(label) >= 3 and (label in glued or glued in label):
        return True
    acronym = "".join(token[0] for token in tokens)
    return len(tokens) >= 2 and label == acronym

SearchFn = Callable[[str, int], str]
FetchFn = Callable[[list[str]], list[dict[str, Any]]]


def host_of(url: str) -> str:
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""
    return host.removeprefix("www.")


def is_linkedin_url(url: str) -> bool:
    host = host_of(url)
    return host == "linkedin.com" or host.endswith(".linkedin.com") or host == "lnkd.in"


def is_blocked_host(url: str) -> bool:
    host = host_of(url)
    if not host:
        return "linkedin.com" in (url or "").lower()
    return any(host == blocked or host.endswith("." + blocked) for blocked in _BLOCKED_HOSTS)


def is_directory_host(url: str) -> bool:
    host = host_of(url)
    return any(host == blocked or host.endswith("." + blocked) for blocked in _DIRECTORY_HOSTS)


def parse_search_hits(text: str) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    if not text or str(text).startswith("No results"):
        return hits
    for match in _HIT_RE.finditer(text):
        title = " ".join(match.group("title").split())
        url = match.group("url").rstrip(").,;")
        if title and url:
            hits.append({"title": title, "url": url})
    return hits


def _run(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=90)


def default_web_search(query: str, count: int = MAX_RESULTS) -> str:
    if offline():
        return f"No results for: {query}"
    from navin.agent.tools.web import WebSearchTool

    tool = WebSearchTool()
    try:
        return str(_run(tool.execute(query=query, count=count)))
    except Exception as exc:
        logger.warning("leads web_search failed: {}", exc)
        return f"No results for: {query}"


def default_scrape_fetch(urls: list[str]) -> list[dict[str, Any]]:
    if offline():
        return [{"url": url, "error": "offline"} for url in urls]
    from navin.agent.tools.scrape import ScrapeTool, ScrapeToolConfig

    allowed = [url for url in urls if not is_blocked_host(url)]
    blocked = [url for url in urls if url not in allowed]
    if blocked:
        logger.info("leads scrape refused closed urls: {}", blocked[:6])
    if not allowed:
        return [{"url": url, "error": "blocked_host"} for url in blocked]
    workspace = Path(tempfile.mkdtemp(prefix="navin-leads-scrape-"))
    try:
        tool = ScrapeTool(
            workspace=workspace,
            config=ScrapeToolConfig(
                max_pages=min(len(allowed), MAX_FETCHES),
                concurrency=min(3, max(1, len(allowed))),
                timeout_seconds=15,
            ),
        )
        raw = _run(
            tool.execute(
                action="fetch",
                urls="\n".join(allowed),
                max_pages=len(allowed),
            )
        )
        if getattr(raw, "is_error", False):
            logger.warning("leads scrape fetch error: {}", raw)
            return [{"url": url, "error": str(raw)[:200]} for url in allowed]
        payload = json.loads(str(raw)) if isinstance(raw, str) or not isinstance(raw, dict) else raw
        pages = list(payload.get("pages") or []) if isinstance(payload, dict) else []
        return pages
    except Exception as exc:
        logger.warning("leads scrape fetch failed: {}", exc)
        return [{"url": url, "error": str(exc)[:200]} for url in allowed]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _company_from_title(title: str) -> str:
    raw = title.split("|")[0].split(" - ")[0].strip()
    return normalize_company(raw)[:90]


def search_queries(profile: dict[str, Any]) -> list[str]:
    sector = str(profile.get("sector") or profile.get("icp_name") or "companies").strip()
    countries = [str(item).upper() for item in (profile.get("countries") or ["FR"])]
    country = countries[0] if countries else "FR"
    return [
        f"{sector} companies {country} official website -linkedin",
        f"{sector} {country} entreprise site officiel -linkedin",
    ]


def hunt_web(
    profile: dict[str, Any],
    limit: int,
    *,
    search_fn: SearchFn | None = None,
) -> list[dict[str, Any]]:
    """Company rows from web_search titles/URLs. No page fetch, no LinkedIn."""
    if limit <= 0:
        return []
    search = search_fn or default_web_search
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    country = str((profile.get("countries") or ["FR"])[0] or "FR").upper()[:2]
    for query in search_queries(profile)[:MAX_QUERIES]:
        text = search(query, MAX_RESULTS)
        for hit in parse_search_hits(str(text)):
            url = hit["url"]
            if is_linkedin_url(url) or is_blocked_host(url) or is_directory_host(url):
                continue
            domain = normalize_domain(url)
            if not domain or domain in seen:
                continue
            company = _company_from_title(hit["title"])
            if not company:
                continue
            seen.add(domain)
            rows.append(
                {
                    "company": company,
                    "domain": domain,
                    "website": f"https://{domain}",
                    "country": country,
                    "sector": str(profile.get("sector") or ""),
                    "source": "web",
                    "signal": "web_search",
                    "confidence": "medium",
                    "linkedin_url": "",
                }
            )
            if len(rows) >= limit:
                return rows
    return rows


def lookup_domain(
    company: str,
    country: str = "",
    *,
    search_fn: SearchFn | None = None,
) -> str:
    """First domain that visibly belongs to the company. Skips LinkedIn, directories,
    people databases and job boards; a hit whose domain shares nothing with the
    company name is noise (a wrong domain becomes a wrong email, sent to a stranger)."""
    name = str(company or "").strip()
    if not name:
        return ""
    search = search_fn or default_web_search
    query = f"{name} official website {country} -linkedin".strip()
    for hit in parse_search_hits(str(search(query, 5))):
        url = hit["url"]
        if is_linkedin_url(url) or is_blocked_host(url) or is_directory_host(url):
            continue
        domain = normalize_domain(url)
        if domain and domain_matches_company(domain, name):
            return domain
    return ""


def _contact_urls(website: str) -> list[str]:
    domain = normalize_domain(website)
    if not domain:
        return []
    root = f"https://{domain}"
    return [
        root,
        f"{root}/contact",
        f"{root}/about",
        f"{root}/equipe",
        f"{root}/team",
    ]


def extract_contacts(text: str, *, country: str = "") -> dict[str, str]:
    blob = str(text or "")
    email = ""
    for match in _EMAIL_RE.findall(blob):
        cleaned = normalize_email(match)
        if not cleaned:
            continue
        host = cleaned.split("@")[-1]
        if host in {"example.com", "sentry.io", "wixpress.com"}:
            continue
        email = cleaned
        break
    phone = ""
    for match in _PHONE_RE.findall(blob):
        cleaned = normalize_phone(match, country=country or None)
        if cleaned:
            phone = cleaned
            break
    return {"email": email, "phone": phone}


def enrich_site(
    website: str,
    *,
    country: str = "",
    fetch_fn: FetchFn | None = None,
) -> dict[str, Any]:
    """Fetch public /contact /about pages. LinkedIn URLs are dropped."""
    urls = [url for url in _contact_urls(website) if not is_linkedin_url(url)]
    if not urls:
        return {}
    fetch = fetch_fn or default_scrape_fetch
    pages = fetch(urls[:MAX_FETCHES])
    blob_parts: list[str] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        url = str(page.get("url") or page.get("finalUrl") or "")
        if is_linkedin_url(url) or is_blocked_host(url):
            continue
        if page.get("error") or page.get("wall"):
            continue
        blob_parts.append(str(page.get("text") or page.get("markdown") or "")[:6000])
    contacts = extract_contacts("\n".join(blob_parts), country=country)
    if not contacts.get("email") and not contacts.get("phone"):
        return {}
    return {
        **contacts,
        "email_status": "unverified" if contacts.get("email") else "",
        "source": "web",
        "signal": "public company site",
    }
