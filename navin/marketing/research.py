"""Competitive map built from real web search results.

Sources, in order: hits the caller injects (agent, API), the brand's own
competitor list, then a live DuckDuckGo pass over a handful of queries derived
from the product. A routed model reads the hits when one is configured; the
heuristic reader below runs otherwise. Nothing is seeded from a hard-coded
list any more: an empty map means the desk found nothing, and says so.
"""

from __future__ import annotations

import os
import re
import time
import urllib.parse
from collections import Counter
from collections.abc import Callable
from typing import Any

from loguru import logger

from navin.marketing import ai
from navin.marketing.errors import MarketingError
from navin.marketing.store import MarketingStore

Search = Callable[[str, int], list[dict[str, str]]]

OFFLINE_FLAG = "NAVIN_MARKETING_OFFLINE"
QUERY_LIMIT = 7
RESULTS_PER_QUERY = 6

# Directories, marketplaces, media and social hosts: useful for trends, never a competitor.
_SKIP_HOSTS = {
    "g2.com", "capterra.com", "capterra.fr", "getapp.com", "getapp.fr", "softwareadvice.com",
    "trustradius.com", "trustpilot.com", "appvizer.fr", "appvizer.com", "saasworthy.com",
    "sourceforge.net", "slashdot.org", "alternativeto.net", "crozdesk.com", "selecthub.com",
    "clutch.co", "goodfirms.co", "producthunt.com", "wikipedia.org", "youtube.com", "linkedin.com",
    "facebook.com", "twitter.com", "x.com", "reddit.com", "medium.com", "quora.com", "github.com",
    "amazon.com", "amazon.fr", "gartner.com", "forbes.com", "techcrunch.com", "lesechos.fr",
    "journaldunet.com", "duckduckgo.com", "google.com", "bing.com", "apple.com", "microsoft.com",
    "play.google.com", "apps.apple.com", "zapier.com", "hubspot.com", "shopify.com", "notion.so",
    "welcometothejungle.com", "indeed.com", "glassdoor.com", "statista.com", "mckinsey.com",
    "bpifrance.fr", "economie.gouv.fr", "legifrance.gouv.fr", "service-public.fr", "europa.eu",
    # App stores, mirrors and startup directories: homonyms and listings, not rivals.
    "apkpure.com", "apkcombo.com", "uptodown.com", "softonic.com", "softonic.fr", "apkmirror.com",
    "f6s.com", "crunchbase.com", "pitchbook.com", "tracxn.com", "owler.com", "zoominfo.com",
    "chromewebstore.google.com", "chrome.google.com", "npmjs.com", "pypi.org",
}
_LISTICLE_RE = re.compile(
    r"\b(top ?\d*|best|meilleurs?|alternatives?|vs\.?|versus|comparatif|comparaison|comparison|"
    r"review|avis|liste|list of|classement|ranking|guide|tutorial|tutoriel|definition|qu'est-ce|"
    r"what is|how to|comment)\b|\b\d+ (best|tools|outils|logiciels|solutions|apps)\b",
    re.I,
)
# A trend headline talks about the market itself; a year or "AI" alone is not enough.
_TREND_RE = re.compile(
    r"\b(trends?|tendances?|future|avenir|market|marche|statisti\w*|report|rapport|etude|"
    r"study|survey|barometre|state of|outlook|forecast|previsions?|predictions?)\b",
    re.I,
)


def _latin(text: str) -> bool:
    """True when every letter is Latin (the desk writes for FR/EN markets)."""
    return all(not ch.isalpha() or ord(ch) <= 0x024F for ch in text)
_STOP = {
    "avec", "pour", "dans", "votre", "vous", "nous", "cette", "leur", "plus", "sans", "sont", "tout",
    "tous", "toute", "toutes", "comme", "mais", "aussi", "entre", "chez", "vers", "sous", "des",
    "les", "une", "the", "and", "for", "with", "that", "this", "from", "your", "you", "are", "our",
    "all", "any", "can", "how", "what", "why", "who", "best", "top", "vs", "free", "gratuit", "online",
    "software", "logiciel", "logiciels", "solution", "solutions", "tool", "tools", "outil", "outils",
    "app", "apps", "platform", "plateforme", "2024", "2025", "2026", "www", "com", "http", "https",
}


def offline() -> bool:
    return str(os.environ.get(OFFLINE_FLAG) or "").strip().lower() in {"1", "true", "yes", "on"}


def default_search() -> Search:
    if offline():
        return lambda _query, _limit: []
    from navin.utils.quick_search import search_text

    return search_text


def _host(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url if "//" in url else f"https://{url}").hostname or ""
    except ValueError:
        return ""
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def _root_host(host: str) -> str:
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "gouv", "ac"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _skip_host(host: str) -> bool:
    root = _root_host(host)
    return not host or root in _SKIP_HOSTS or host in _SKIP_HOSTS


def web_queries(product: dict[str, Any], brand: dict[str, Any], positioning: dict[str, Any]) -> list[str]:
    """Five or six searches that surface alternatives, pricing and trends."""
    name = str(product.get("name") or brand.get("product") or "").strip()
    category = str(product.get("category") or "").strip() or str(brand.get("audience") or "").strip()
    pain = str(positioning.get("pain") or product.get("pain") or "").strip()
    year = time.strftime("%Y")
    countries = [str(item).strip() for item in (brand.get("countries") or []) if str(item).strip()]
    terms = [str(item).strip() for item in (product.get("search_terms") or []) if str(item).strip()]
    queries: list[str] = []
    if name:
        queries.append(f"{name} alternatives")
    # Buyer queries from the product brief first: they name the category the way the market does.
    queries.extend(terms[:2])
    if category:
        queries.append(f"{category} software")
        queries.append(f"best {category} tools {year}")
        queries.append(f"{category} trends {year}")
        queries.append(f"{category} pricing")
    if pain:
        queries.append(" ".join(pain.split()[:10]))
    for country in countries[:2]:
        if category:
            queries.append(f"{category} {country}")
    return list(dict.fromkeys(query for query in queries if query.strip()))[:QUERY_LIMIT]


def gather_hits(queries: list[str], search: Search, *, per_query: int = RESULTS_PER_QUERY) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in queries:
        try:
            rows = search(query, per_query) or []
        except Exception as exc:  # noqa: BLE001 - a dead search engine is not a desk failure
            logger.info("marketing research search failed for {!r}: {}", query, exc)
            rows = []
        for row in rows:
            url = str(row.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            hits.append(
                {
                    "query": query,
                    "title": " ".join(str(row.get("title") or "").split()),
                    "url": url,
                    "snippet": " ".join(str(row.get("snippet") or "").split()),
                }
            )
    return hits


def _brand_from_title(title: str, host: str) -> str:
    parts = [part.strip(" -|:") for part in re.split(r"\s+[|\-:]\s+|\s+\u00b7\s+", title) if part.strip(" -|:")]
    label = host.split(".")[0]
    for part in parts:
        # The brand is usually the shortest segment that shares letters with the domain.
        if 2 <= len(part) <= 32 and re.sub(r"[^a-z0-9]", "", part.lower()).startswith(label[:4]):
            return part
    short = [part for part in parts if 2 <= len(part) <= 24 and not _LISTICLE_RE.search(part)]
    if short:
        return min(short, key=len)
    return label.capitalize() if label else ""


def competitors_from_hits(hits: list[dict[str, Any]], *, own_name: str = "", own_site: str = "") -> list[dict[str, Any]]:
    """One competitor per domain, skipping directories, media and listicles."""
    own = own_name.strip().lower()
    own_host = _host(own_site) if own_site else ""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hit in hits:
        host = _host(str(hit.get("url") or ""))
        if _skip_host(host) or (own_host and _root_host(host) == _root_host(own_host)):
            continue
        title = str(hit.get("title") or "")
        if _LISTICLE_RE.search(title):
            continue
        root = _root_host(host)
        if root in seen:
            continue
        name = _brand_from_title(title, host)
        if not name or name.lower() == own:
            continue
        seen.add(root)
        snippet = str(hit.get("snippet") or "")
        pricing = ""
        match = re.search(r"(\d+(?:[.,]\d+)?\s?(?:\u20ac|\$|eur|usd|euros?)(?:\s?/\s?(?:mois|month|mo|an|year|user|utilisateur))?)", snippet, re.I)
        if match:
            pricing = match.group(1)
        rows.append(
            {
                "name": name,
                "url": f"https://{host}",
                "pricing": pricing,
                "angle": "alternative" if "alternative" in str(hit.get("query") or "") else "competitor",
                "note": snippet[:220],
                "source": str(hit.get("url") or ""),
            }
        )
    return rows[:8]


def trends_from_hits(hits: list[dict[str, Any]], *, own_name: str = "") -> list[str]:
    """Market headlines only: no listicles, no foreign scripts, not the product itself."""
    own = own_name.strip().lower()
    rows: list[str] = []
    for hit in hits:
        title = str(hit.get("title") or "")
        if not _TREND_RE.search(title) or _LISTICLE_RE.search(title) or not _latin(title):
            continue
        clean = re.split(r"\s+[|\-]\s+", title)[0].strip()
        if own and own in clean.lower():
            continue
        if 12 <= len(clean) <= 110 and clean not in rows:
            rows.append(clean)
    return rows[:6]


def keywords_from_hits(hits: list[dict[str, Any]], base: list[str]) -> list[str]:
    counter: Counter[str] = Counter()
    for hit in hits:
        text = f"{hit.get('title') or ''} {hit.get('snippet') or ''}".lower()
        for word in re.findall(r"[a-z\u00e0-\u00ff][a-z\u00e0-\u00ff0-9-]{3,}", text):
            if word not in _STOP:
                counter[word] += 1
    picked = [word for word, count in counter.most_common(24) if count >= 2][:10]
    return list(dict.fromkeys([*base, *picked]))[:20]


def _base_keywords(product: dict[str, Any], positioning: dict[str, Any]) -> list[str]:
    name = str(product.get("name") or "").strip()
    category = str(product.get("category") or "").strip()
    pain = str(positioning.get("pain") or product.get("pain") or "").strip()
    terms = [str(item).strip().lower() for item in (product.get("search_terms") or []) if str(item).strip()]
    # A pain written as a sentence is not a keyword; short phrases are.
    keys = [name.lower(), category.lower(), *terms, pain.lower() if pain and len(pain.split()) <= 5 else ""]
    return list(dict.fromkeys(key for key in keys if key))


def _brand_competitors(brand: dict[str, Any]) -> list[dict[str, Any]]:
    names = [str(item).strip() for item in (brand.get("competitors") or []) if str(item).strip()]
    return [
        {"name": name, "pricing": "", "angle": "incumbent", "note": f"declared by the brand: watch {name} pricing, landing and releases"}
        for name in names[:8]
    ]


def build_research(
    store: MarketingStore,
    *,
    hits: list[dict[str, Any]] | None = None,
    market: str = "",
    trends: list[str] | None = None,
    search: Search | None = None,
    web: bool = True,
) -> dict[str, Any]:
    product = store.load_product()
    brand = store.load_brand()
    positioning = store.load_positioning()
    settings = store.load_settings()
    incoming = [row for row in (hits or []) if isinstance(row, dict) and str(row.get("name") or "").strip()]
    declared = _brand_competitors(brand)

    queries: list[str] = []
    web_hits: list[dict[str, Any]] = []
    web_rows: list[dict[str, Any]] = []
    web_trends: list[str] = []
    mode = "manual" if incoming else "declared" if declared else "empty"
    if web and not incoming:
        queries = web_queries(product, brand, positioning)
        web_hits = gather_hits(queries, search or default_search())
        if web_hits:
            mode = "web"
            extracted = ai.extract_research(product, brand, web_hits, settings) if ai.enabled(settings) else None
            if extracted and extracted.get("competitors"):
                web_rows = extracted["competitors"]
                web_trends = extracted.get("trends") or []
                mode = "web+model"
            else:
                web_rows = competitors_from_hits(
                    web_hits,
                    own_name=str(product.get("name") or brand.get("product") or ""),
                    own_site=str(product.get("site") or brand.get("site") or ""),
                )
            if not web_trends:
                web_trends = trends_from_hits(web_hits, own_name=str(product.get("name") or brand.get("product") or ""))

    competitors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in [*incoming, *declared, *web_rows]:
        key = str(row.get("name") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        competitors.append(row)
    for row in competitors:
        store.upsert_competitor(row)

    base_keywords = _base_keywords(product, positioning)
    research = store.save_research(
        {
            "market": market or str(product.get("category") or "product"),
            "trends": trends or web_trends,
            "keywords": keywords_from_hits(web_hits, base_keywords) if web_hits else base_keywords,
            "competitors": competitors,
            "pricing_notes": [str(row.get("pricing") or "").strip() for row in competitors if str(row.get("pricing") or "").strip()],
            "sources": list(
                dict.fromkeys(
                    str(row.get("source") or row.get("url") or "").strip()
                    for row in [*incoming, *web_rows]
                    if str(row.get("source") or row.get("url") or "").strip()
                )
            )[:40],
            "queries": queries,
            "hits": len(web_hits),
            "mode": mode,
            "fetched_at": time.time(),
        }
    )
    store.append_journal(
        {
            "kind": "research",
            "text": f"competitive map ({mode}): {len(competitors)} competitors, {len(web_hits)} web hits",
        }
    )
    return research


def ingest_competitor(store: MarketingStore, row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise MarketingError("competitor payload must be an object")
    saved = store.upsert_competitor(row)
    research = store.load_research()
    names = {
        str(item.get("name") or "").strip().lower()
        for item in (research.get("competitors") or [])
        if isinstance(item, dict)
    }
    if saved["name"].lower() not in names:
        competitors = list(research.get("competitors") or [])
        competitors.insert(0, saved)
        store.save_research({"competitors": competitors})
    return saved
