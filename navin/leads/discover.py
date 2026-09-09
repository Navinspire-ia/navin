# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Keyless discovery that scales with the market, not with a paid plan.

Three open sources feed the hunt on top of the registries:

* ``hunt_web``: localized multi-angle web searches per country and city, plus
  listicle mining. A "top 20 agences web Lyon" page carries twenty company sites;
  we fetch the page once and read its outbound links instead of keeping one hit.
* ``hunt_osm``: OpenStreetMap Nominatim for local businesses (offices, shops,
  agencies) with the website / email / phone the community already tagged.
* ``hunt_hiring``: public LinkedIn job listings as a buying signal. A company
  hiring the profile that consumes your offer is a warmer lead than a registry row.

All of it is injectable (``search_fn``, ``http_get``, ``jobs_fn``) so tests run
offline, and every row is attributed (``source`` / ``signal``) so the desk can
say where a lead came from. LinkedIn people pages are never fetched.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from loguru import logger

from navin.leads.normalize import (
    normalize_company,
    normalize_domain,
    normalize_email,
    normalize_phone,
)
from navin.leads.scrape_net import (
    MAX_RESULTS,
    SearchFn,
    _company_from_title,
    default_web_search,
    host_of,
    is_blocked_host,
    is_directory_host,
    is_linkedin_url,
    offline,
    parse_search_hits,
)

HttpGet = Callable[[str], tuple[int, str]]
JobsFn = Callable[..., dict[str, Any]]

USER_AGENT = "NavinLeads/2.0 (+https://navin.live; open-web prospecting desk)"
TIMEOUT_S = 12
MAX_HTML_BYTES = 1_500_000
WEB_QUERIES_PER_HUNT = 6
LISTICLES_PER_HUNT = 3
LINKS_PER_LISTICLE = 25
OSM_QUERIES_PER_HUNT = 3
OSM_MIN_INTERVAL_S = 1.05
HIRING_MAX_REQUESTS = 4
DOMAIN_LOOKUPS_PER_HUNT = 8
SITE_FETCHES_PER_HUNT = 4

LANG = {
    "FR": "fr",
    "BE": "fr",
    "CH": "fr",
    "LU": "fr",
    "MC": "fr",
    "MA": "fr",
    "TN": "fr",
    "DZ": "fr",
    "SN": "fr",
    "CI": "fr",
    "CM": "fr",
    "DE": "de",
    "AT": "de",
    "ES": "es",
    "MX": "es",
    "AR": "es",
    "CO": "es",
    "CL": "es",
    "IT": "it",
    "NL": "nl",
    "PT": "pt",
    "BR": "pt",
}
COUNTRY_NAME = {
    "FR": {"fr": "France", "en": "France"},
    "BE": {"fr": "Belgique", "en": "Belgium", "nl": "Belgie"},
    "CH": {"fr": "Suisse", "en": "Switzerland", "de": "Schweiz"},
    "LU": {"fr": "Luxembourg", "en": "Luxembourg"},
    "MC": {"fr": "Monaco", "en": "Monaco"},
    "MA": {"fr": "Maroc", "en": "Morocco"},
    "TN": {"fr": "Tunisie", "en": "Tunisia"},
    "DZ": {"fr": "Algerie", "en": "Algeria"},
    "SN": {"fr": "Senegal", "en": "Senegal"},
    "CI": {"fr": "Cote d'Ivoire", "en": "Ivory Coast"},
    "CM": {"fr": "Cameroun", "en": "Cameroon"},
    "CA": {"fr": "Canada", "en": "Canada"},
    "GB": {"en": "United Kingdom"},
    "IE": {"en": "Ireland"},
    "US": {"en": "United States"},
    "AE": {"en": "United Arab Emirates"},
    "SA": {"en": "Saudi Arabia"},
    "QA": {"en": "Qatar"},
    "DE": {"de": "Deutschland", "en": "Germany"},
    "AT": {"de": "Osterreich", "en": "Austria"},
    "ES": {"es": "Espana", "en": "Spain"},
    "MX": {"es": "Mexico", "en": "Mexico"},
    "IT": {"it": "Italia", "en": "Italy"},
    "NL": {"nl": "Nederland", "en": "Netherlands"},
    "PT": {"pt": "Portugal", "en": "Portugal"},
    "BR": {"pt": "Brasil", "en": "Brazil"},
    "SE": {"en": "Sweden"},
    "DK": {"en": "Denmark"},
    "NO": {"en": "Norway"},
    "FI": {"en": "Finland"},
    "PL": {"en": "Poland"},
    "CZ": {"en": "Czech Republic"},
    "SG": {"en": "Singapore"},
    "AU": {"en": "Australia"},
    "IN": {"en": "India"},
}
CITIES = {
    "FR": [
        "Paris",
        "Lyon",
        "Marseille",
        "Toulouse",
        "Nantes",
        "Bordeaux",
        "Lille",
        "Montpellier",
        "Strasbourg",
        "Rennes",
    ],
    "BE": ["Bruxelles", "Anvers", "Gand", "Liege", "Namur", "Louvain"],
    "CH": ["Zurich", "Geneve", "Lausanne", "Bale", "Berne", "Lugano"],
    "LU": ["Luxembourg", "Esch-sur-Alzette"],
    "MC": ["Monaco"],
    "MA": ["Casablanca", "Rabat", "Tanger", "Marrakech", "Agadir"],
    "TN": ["Tunis", "Sfax", "Sousse"],
    "DZ": ["Alger", "Oran", "Constantine"],
    "SN": ["Dakar"],
    "CI": ["Abidjan"],
    "CM": ["Douala", "Yaounde"],
    "CA": ["Montreal", "Toronto", "Quebec", "Vancouver", "Ottawa", "Calgary"],
    "GB": ["London", "Manchester", "Birmingham", "Leeds", "Bristol", "Edinburgh", "Glasgow"],
    "IE": ["Dublin", "Cork", "Galway"],
    "US": [
        "New York",
        "San Francisco",
        "Los Angeles",
        "Chicago",
        "Austin",
        "Boston",
        "Seattle",
        "Miami",
    ],
    "AE": ["Dubai", "Abu Dhabi", "Sharjah"],
    "SA": ["Riyadh", "Jeddah"],
    "QA": ["Doha"],
    "DE": ["Berlin", "Munchen", "Hamburg", "Frankfurt", "Koln", "Stuttgart", "Dusseldorf"],
    "AT": ["Wien", "Graz", "Linz"],
    "ES": ["Madrid", "Barcelona", "Valencia", "Sevilla", "Bilbao", "Malaga"],
    "MX": ["Ciudad de Mexico", "Guadalajara", "Monterrey"],
    "IT": ["Milano", "Roma", "Torino", "Bologna", "Napoli", "Firenze"],
    "NL": ["Amsterdam", "Rotterdam", "Utrecht", "Eindhoven", "Den Haag"],
    "PT": ["Lisboa", "Porto", "Braga"],
    "BR": ["Sao Paulo", "Rio de Janeiro", "Belo Horizonte"],
    "SE": ["Stockholm", "Goteborg", "Malmo"],
    "DK": ["Copenhagen", "Aarhus"],
    "NO": ["Oslo", "Bergen"],
    "FI": ["Helsinki", "Tampere"],
    "PL": ["Warszawa", "Krakow", "Wroclaw"],
    "CZ": ["Praha", "Brno"],
    "SG": ["Singapore"],
    "AU": ["Sydney", "Melbourne", "Brisbane"],
    "IN": ["Bangalore", "Mumbai", "Delhi", "Hyderabad", "Pune"],
}
# Query angles per language. {sector} {city} {country} are filled in; the
# listicle angles (top / list / directory) are what makes one search worth twenty rows.
TEMPLATES = {
    "fr": [
        "{sector} {city} site officiel",
        "liste des {sector} a {city}",
        "top {sector} {country}",
        "meilleures entreprises {sector} {country}",
        "annuaire {sector} {country}",
        "{sector} {city} agence OR societe OR cabinet",
    ],
    "en": [
        "{sector} companies {city} official website",
        "top {sector} companies in {country}",
        "list of {sector} companies {city}",
        "best {sector} agencies {country}",
        "{sector} {city} company OR agency OR firm",
        "{sector} directory {country}",
    ],
    "de": [
        "{sector} {city} Unternehmen Website",
        "beste {sector} Unternehmen {country}",
        "Liste {sector} Firmen {city}",
        "{sector} Agentur {city}",
        "{sector} Anbieter {country} Verzeichnis",
    ],
    "es": [
        "empresas de {sector} en {city} sitio web",
        "mejores empresas {sector} {country}",
        "lista empresas {sector} {city}",
        "agencia {sector} {city}",
        "directorio empresas {sector} {country}",
    ],
    "it": [
        "aziende {sector} {city} sito ufficiale",
        "migliori aziende {sector} {country}",
        "elenco aziende {sector} {city}",
        "agenzia {sector} {city}",
    ],
    "nl": [
        "{sector} bedrijven {city} website",
        "beste {sector} bedrijven {country}",
        "lijst {sector} bedrijven {city}",
        "{sector} bureau {city}",
    ],
    "pt": [
        "empresas de {sector} {city} site oficial",
        "melhores empresas {sector} {country}",
        "lista empresas {sector} {city}",
        "agencia {sector} {city}",
    ],
}
_LISTICLE_RE = re.compile(
    r"\b(top|meilleur|meilleures|best|liste|list|annuaire|directory|classement|ranking|selection|guide|comparatif|"
    r"agences|companies|entreprises|empresas|unternehmen|aziende|bedrijven|firmen|\d{1,3}\s+(?:agences|companies|entreprises|empresas|aziende|bedrijven))\b",
    re.I,
)
_ANCHOR_RE = re.compile(r"<a\b[^>]*href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_GENERIC_ANCHORS = {
    "site web",
    "site",
    "website",
    "web",
    "visiter",
    "visiter le site",
    "voir le site",
    "en savoir plus",
    "learn more",
    "read more",
    "more",
    "plus",
    "lire la suite",
    "ici",
    "here",
    "click here",
    "cliquez ici",
    "www",
    "home",
    "accueil",
    "link",
    "lien",
    "visit",
    "visit website",
    "official website",
    "site officiel",
    "source",
    "voir",
    "details",
    "detail",
    "contact",
}
_AGGREGATOR_HOSTS = (
    "google.",
    "bing.com",
    "yahoo.",
    "duckduckgo.com",
    "wikipedia.org",
    "wikimedia.org",
    "amazon.",
    "apple.com",
    "microsoft.com",
    "adobe.com",
    "cloudflare.com",
    "wordpress.",
    "wix.com",
    "squarespace.com",
    "shopify.com",
    "godaddy.com",
    "sortlist.",
    "clutch.co",
    "goodfirms.co",
    "designrush.com",
    "trustpilot.",
    "glassdoor.",
    "indeed.",
    "welcometothejungle.",
    "malt.",
    "upwork.com",
    "fiverr.com",
    "medium.com",
    "substack.com",
    "notion.so",
    "canva.com",
    "figma.com",
    "github.com",
    "gitlab.com",
    "youtube.",
    "vimeo.com",
    "pinterest.",
    "reddit.com",
    "quora.com",
    "t.me",
    "wa.me",
    "whatsapp.com",
    "goo.gl",
    "bit.ly",
    "linktr.ee",
    "pagesjaunes.",
    "yelp.",
    "tripadvisor.",
    "booking.com",
    "kompass.com",
    "europages.",
    "societe.com",
    "pappers.fr",
    "verif.com",
    "infogreffe.fr",
    "manageo.fr",
    "annuaire-entreprises.data.gouv.fr",
    "lefigaro.fr",
    "lemonde.fr",
    "lesechos.fr",
    "bfmtv.com",
    "forbes.com",
    "techcrunch.com",
    "maddyness.com",
    "frenchweb.fr",
    "usine-digitale.fr",
    "journaldunet.com",
    "capital.fr",
    "ouest-france.fr",
    "20minutes.fr",
    "challenges.fr",
    "latribune.fr",
    "bpifrance.",
    "gouv.fr",
    "europa.eu",
    "cci.fr",
)
_OSM_KEEP_CLASSES = {
    "office",
    "shop",
    "craft",
    "amenity",
    "industrial",
    "commercial",
    "company",
    "building",
    "landuse",
    "man_made",
}
_OSM_DROP_TYPES = {
    "house",
    "residential",
    "apartments",
    "parking",
    "yes",
    "school",
    "hospital",
    "place_of_worship",
    "bench",
    "toilets",
}
_RESIDUAL_TLD_RE = re.compile(
    r"\.(com|fr|be|ch|lu|ma|tn|dz|ca|co\.uk|uk|ie|us|ae|de|at|es|it|nl|pt|br|io|ai|co|eu|net|org|info|biz|tech|agency|studio|digital|consulting|group|solutions|services|pro|app|dev|me|tv|fm|se|dk|no|fi|pl|cz|sg|au|in|mx|ar|cl|sn|ci|cm|qa|sa)$"
)


# -- helpers -----------------------------------------------------------------------


def lang_for(country: str) -> str:
    return LANG.get(str(country or "").upper()[:2], "en")


def country_name(country: str, lang: str) -> str:
    iso = str(country or "").upper()[:2]
    names = COUNTRY_NAME.get(iso) or {}
    return names.get(lang) or names.get("en") or iso


def default_http_get(url: str) -> tuple[int, str]:
    """Plain GET returning (status, text). Size-capped, UA set, redirects followed."""
    if offline():
        return 0, ""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.5",
            "Accept-Language": "fr,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310 - explicit https
            raw = response.read(MAX_HTML_BYTES)
            charset = response.headers.get_content_charset() or "utf-8"
            return int(response.status), raw.decode(charset, "replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), ""
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("leads discover GET failed {}: {}", url, exc)
        return 0, ""


def company_from_domain(domain: str) -> str:
    """``agence-lumiere.fr`` -> ``Agence Lumiere``. Good enough until the site title is read."""
    raw = normalize_domain(domain)
    if not raw:
        return ""
    raw = _RESIDUAL_TLD_RE.sub("", raw)
    raw = _RESIDUAL_TLD_RE.sub("", raw)
    stem = raw.split(".")[0] if raw else ""
    words = [part for part in re.split(r"[-_.]+", stem) if part]
    if not words:
        return ""
    return normalize_company(" ".join(word.capitalize() for word in words))[:90]


def _is_company_site(url: str, *, listicle_host: str = "") -> bool:
    host = host_of(url)
    if (
        not host
        or host == listicle_host
        or host.endswith("." + listicle_host)
        or (listicle_host and listicle_host.endswith("." + host))
    ):
        return False
    if is_linkedin_url(url) or is_blocked_host(url) or is_directory_host(url):
        return False
    if any(marker in host for marker in _AGGREGATOR_HOSTS):
        return False
    path = urllib.parse.urlparse(url).path or "/"
    # Deep article paths on a third-party site are content, not a company home.
    return path.count("/") <= 2 and not re.search(
        r"\.(pdf|jpg|jpeg|png|gif|svg|webp|zip|mp4)$", path, re.I
    )


def _looks_like_listicle(title: str, url: str) -> bool:
    text = f"{title} {urllib.parse.urlparse(url).path}"
    return bool(_LISTICLE_RE.search(text))


def _clean_anchor(text: str) -> str:
    plain = html_lib.unescape(_TAG_RE.sub(" ", text or ""))
    plain = " ".join(plain.split())
    return plain.strip(" -|:>«»\"'")


def mine_listicle(
    page_html: str, page_url: str, *, limit: int = LINKS_PER_LISTICLE
) -> list[dict[str, str]]:
    """Outbound company links of a directory / top-N page: (company, domain, website)."""
    base_host = host_of(page_url)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _ANCHOR_RE.finditer(page_html or ""):
        href = html_lib.unescape(match.group(1).strip())
        if (
            not href
            or href.startswith("#")
            or href.lower().startswith(("javascript:", "mailto:", "tel:"))
        ):
            continue
        absolute = urllib.parse.urljoin(page_url, href)
        # Redirect wrappers (?url=https://...) hide the real target.
        wrapped = urllib.parse.parse_qs(urllib.parse.urlparse(absolute).query)
        for key in ("url", "u", "target", "redirect", "to"):
            candidates = wrapped.get(key) or []
            if candidates and candidates[0].startswith("http"):
                absolute = candidates[0]
                break
        if not _is_company_site(absolute, listicle_host=base_host):
            continue
        domain = normalize_domain(absolute)
        if not domain or domain in seen:
            continue
        anchor = _clean_anchor(match.group(2))
        if (
            len(anchor) > 70
            or anchor.lower() in _GENERIC_ANCHORS
            or re.fullmatch(r"https?://\S+|www\.\S+", anchor.lower())
        ):
            anchor = ""
        if anchor and (len(anchor) < 2 or anchor.lower().startswith(("http", "www."))):
            anchor = ""
        company = normalize_company(anchor)[:90] if anchor else company_from_domain(domain)
        if not company:
            continue
        seen.add(domain)
        rows.append({"company": company, "domain": domain, "website": f"https://{domain}"})
        if len(rows) >= limit:
            break
    return rows


def web_queries(
    profile: dict[str, Any], *, cursor: int = 0, per_country: int = WEB_QUERIES_PER_HUNT
) -> list[tuple[str, str]]:
    """(country, query) pairs. The cursor rotates cities and angles between runs."""
    sector = str(profile.get("sector") or profile.get("icp_name") or "").strip()
    if not sector:
        return []
    countries = [
        str(item).upper()[:2] for item in (profile.get("countries") or ["FR"]) if str(item).strip()
    ] or ["FR"]
    wanted_cities = [
        str(item).strip() for item in (profile.get("cities") or []) if str(item).strip()
    ]
    keywords = [str(item).strip() for item in (profile.get("keywords") or []) if str(item).strip()]
    out: list[tuple[str, str]] = []
    for country in countries:
        lang = lang_for(country)
        name = country_name(country, lang)
        cities = wanted_cities or CITIES.get(country) or [name]
        templates = TEMPLATES.get(lang) or TEMPLATES["en"]
        seen: set[str] = set()
        for step in range(per_country):
            index = cursor * per_country + step
            template = templates[index % len(templates)]
            city = cities[(index // len(templates) + step) % len(cities)]
            query = " ".join(template.format(sector=sector, city=city, country=name).split())
            if query.casefold() in seen:
                continue
            seen.add(query.casefold())
            out.append((country, query))
        for keyword in keywords[:2]:
            out.append((country, f"{keyword} {cities[cursor % len(cities)]} {name}"))
    return out


def hunt_web(
    profile: dict[str, Any],
    limit: int,
    *,
    search_fn: SearchFn | None = None,
    http_get: HttpGet | None = None,
    cursor: int = 0,
    listicles: int = LISTICLES_PER_HUNT,
) -> list[dict[str, Any]]:
    """Localized searches plus listicle mining. Attribution: source=web, signal=query or list page."""
    if limit <= 0:
        return []
    search = search_fn or default_web_search
    get = http_get or default_http_get
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    fetched = 0
    sector = str(profile.get("sector") or "")

    def _add(country: str, company: str, domain: str, signal: str, confidence: str) -> bool:
        if not domain or domain in seen or not company:
            return False
        seen.add(domain)
        rows.append(
            {
                "company": company,
                "domain": domain,
                "website": f"https://{domain}",
                "country": country,
                "sector": sector,
                "source": "web",
                "signal": signal,
                "confidence": confidence,
                "linkedin_url": "",
            }
        )
        return len(rows) >= limit

    for country, query in web_queries(profile, cursor=cursor):
        text = search(query, MAX_RESULTS)
        for hit in parse_search_hits(str(text)):
            url = hit["url"]
            title = hit["title"]
            if is_linkedin_url(url) or is_blocked_host(url):
                continue
            if fetched < listicles and (_looks_like_listicle(title, url) or is_directory_host(url)):
                fetched += 1
                status, page = get(url)
                if status == 200 and page:
                    for found in mine_listicle(page, url):
                        if _add(
                            country,
                            found["company"],
                            found["domain"],
                            f"listed on {host_of(url)}: {title[:60]}",
                            "medium",
                        ):
                            return rows
                continue
            if is_directory_host(url) or not _is_company_site(url):
                continue
            domain = normalize_domain(url)
            company = _company_from_title(title) or company_from_domain(domain)
            if _add(country, company, domain, f"web: {query}", "medium"):
                return rows
    return rows


# -- OpenStreetMap ---------------------------------------------------------------------


def osm_queries(
    profile: dict[str, Any], *, cursor: int = 0, per_country: int = OSM_QUERIES_PER_HUNT
) -> list[tuple[str, str, str]]:
    sector = str(profile.get("sector") or profile.get("icp_name") or "").strip()
    if not sector:
        return []
    countries = [
        str(item).upper()[:2] for item in (profile.get("countries") or ["FR"]) if str(item).strip()
    ] or ["FR"]
    wanted_cities = [
        str(item).strip() for item in (profile.get("cities") or []) if str(item).strip()
    ]
    out: list[tuple[str, str, str]] = []
    for country in countries:
        cities = wanted_cities or CITIES.get(country) or [country_name(country, lang_for(country))]
        for step in range(per_country):
            city = cities[(cursor * per_country + step) % len(cities)]
            out.append((country, city, f"{sector} {city}"))
    return out


def parse_osm_results(
    payload: Any, *, country: str, city: str, sector: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            display = str(item.get("display_name") or "")
            name = display.split(",")[0].strip() if display else ""
        klass = str(item.get("class") or item.get("category") or "")
        kind = str(item.get("type") or "")
        if not name or kind in _OSM_DROP_TYPES or (klass and klass not in _OSM_KEEP_CLASSES):
            continue
        tags = item.get("extratags") if isinstance(item.get("extratags"), dict) else {}
        website = str(
            tags.get("website") or tags.get("contact:website") or tags.get("url") or ""
        ).strip()
        domain = normalize_domain(website) if website else ""
        email = normalize_email(str(tags.get("contact:email") or tags.get("email") or ""))
        phone = normalize_phone(
            str(tags.get("contact:phone") or tags.get("phone") or ""), country=country or None
        )
        address = item.get("address") if isinstance(item.get("address"), dict) else {}
        town = str(address.get("city") or address.get("town") or address.get("village") or city)
        rows.append(
            {
                "company": normalize_company(name)[:90],
                "domain": domain,
                "website": f"https://{domain}" if domain else "",
                "email": email or "",
                "email_status": "unverified" if email else "",
                "phone": phone or "",
                "country": country,
                "sector": sector,
                "source": "osm",
                "signal": f"local business ({klass}/{kind}) in {town}",
                "confidence": "high" if domain else "medium",
                "linkedin_url": "",
                "extra": {
                    "osm_id": item.get("osm_id"),
                    "osm_type": item.get("osm_type"),
                    "city": town,
                },
            }
        )
    return rows


def hunt_osm(
    profile: dict[str, Any],
    limit: int,
    *,
    http_get: HttpGet | None = None,
    cursor: int = 0,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Nominatim search per city. One request per second, as the usage policy asks."""
    if limit <= 0:
        return []
    get = http_get or default_http_get
    sector = str(profile.get("sector") or "")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    first = True
    for country, city, query in osm_queries(profile, cursor=cursor):
        if not first:
            sleep(OSM_MIN_INTERVAL_S)
        first = False
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
            {
                "q": query,
                "format": "jsonv2",
                "extratags": 1,
                "addressdetails": 1,
                "limit": 30,
                "countrycodes": country.lower(),
            }
        )
        status, text = get(url)
        if status != 200 or not text:
            continue
        try:
            payload = json.loads(text)
        except ValueError:
            continue
        for row in parse_osm_results(payload, country=country, city=city, sector=sector):
            key = (
                row["domain"]
                or f"{row['company'].casefold()}|{row['extra'].get('city', '')}".casefold()
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
            if len(rows) >= limit:
                return rows
    return rows


# -- hiring signals -------------------------------------------------------------------


def hiring_titles(profile: dict[str, Any]) -> list[str]:
    """Roles a prospect hires when it needs the offer: profile.signals, else the sector."""
    wanted = [str(item).strip() for item in (profile.get("signals") or []) if str(item).strip()]
    if wanted:
        return wanted[:3]
    sector = str(profile.get("sector") or "").strip()
    return [sector] if sector else []


def hunt_hiring(
    profile: dict[str, Any],
    limit: int,
    *,
    jobs_fn: JobsFn | None = None,
) -> list[dict[str, Any]]:
    """Companies with an open role matching the buying signal. LinkedIn public listings only."""
    if limit <= 0:
        return []
    titles = hiring_titles(profile)
    if not titles:
        return []
    countries = [
        str(item).upper()[:2] for item in (profile.get("countries") or ["FR"]) if str(item).strip()
    ] or ["FR"]
    if jobs_fn is None:
        if offline():
            return []
        from navin.career.linkedin import search_linkedin_jobs

        jobs_fn = search_linkedin_jobs
    try:
        result = jobs_fn(
            titles=titles,
            countries=countries,
            max_requests=HIRING_MAX_REQUESTS,
            max_pages=1,
            max_details=0,
        )
    except Exception as exc:
        logger.warning("leads hiring signals failed: {}", exc)
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    sector = str(profile.get("sector") or "")
    for job in (result or {}).get("jobs") or []:
        if not isinstance(job, dict):
            continue
        company = normalize_company(str(job.get("company") or ""))[:90]
        if not company or company.casefold() in seen:
            continue
        country = str(job.get("country") or "").upper()[:2] or countries[0]
        if country not in countries:
            continue
        seen.add(company.casefold())
        title = str(job.get("title") or "").strip()
        rows.append(
            {
                "company": company,
                "domain": "",
                "website": "",
                "country": country,
                "sector": sector,
                "source": "hiring",
                "signal": f"hiring: {title}"
                + (f" ({job.get('location')})" if job.get("location") else ""),
                "confidence": "medium",
                "linkedin_url": "",
                "extra": {
                    "job_url": str(job.get("url") or ""),
                    "job_title": title,
                    "job_location": str(job.get("location") or ""),
                    "posted_at": str(job.get("posted_at") or ""),
                },
            }
        )
        if len(rows) >= limit:
            break
    return rows


# -- bulk enrichment on a budget ------------------------------------------------------

_EMAIL_PATTERNS = (
    "{first}.{last}",
    "{f}{last}",
    "{first}",
    "{first}{last}",
    "{f}.{last}",
    "{last}",
)
_MX_CACHE: dict[str, bool] = {}


def _ascii(text: str) -> str:
    import unicodedata

    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", plain.lower())


def domain_accepts_mail(domain: str, *, resolver: Callable[[str], bool] | None = None) -> bool:
    """MX (dnspython when present) or A record. Cached per process; never raises."""
    key = normalize_domain(domain)
    if not key:
        return False
    if key in _MX_CACHE:
        return _MX_CACHE[key]
    if resolver is not None:
        ok = bool(resolver(key))
    elif offline():
        return False
    else:
        ok = False
        try:
            import dns.resolver  # type: ignore[import-not-found]

            ok = bool(dns.resolver.resolve(key, "MX", lifetime=4))
        except Exception:
            try:
                socket.getaddrinfo(key, None)
                ok = True
            except OSError:
                ok = False
    _MX_CACHE[key] = ok
    return ok


def guess_email(first: str, last: str, domain: str, *, pattern: str = "") -> str:
    f_clean = _ascii(first)
    l_clean = _ascii(last)
    dom = normalize_domain(domain)
    if not dom or not (f_clean or l_clean):
        return ""
    template = pattern or (
        _EMAIL_PATTERNS[0] if f_clean and l_clean else "{first}" if f_clean else "{last}"
    )
    local = template.format(first=f_clean, last=l_clean, f=f_clean[:1])
    local = local.strip(".")
    return normalize_email(f"{local}@{dom}") if local else ""


def bulk_enrich(
    rows: list[dict[str, Any]],
    profile: dict[str, Any],
    *,
    search_fn: SearchFn | None = None,
    fetch_fn: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    resolver: Callable[[str], bool] | None = None,
    domain_budget: int = DOMAIN_LOOKUPS_PER_HUNT,
    site_budget: int = SITE_FETCHES_PER_HUNT,
) -> dict[str, int]:
    """Cheap, budgeted fill-ins after a hunt: domains for named companies, guessed
    emails for named officers, one contact-page pass for the best rows. In place."""
    from navin.leads.scrape_net import enrich_site, lookup_domain

    spent = {"domains": 0, "emails": 0, "sites": 0}

    def _priority(row: dict[str, Any]) -> tuple[int, int]:
        return (
            1 if row.get("person") else 0,
            1 if row.get("source") in {"hiring", "sirene", "companies_house"} else 0,
        )

    ordered = sorted(rows, key=_priority, reverse=True)
    for row in ordered:
        if spent["domains"] >= domain_budget:
            break
        if row.get("domain") or not row.get("company"):
            continue
        spent["domains"] += 1
        found = lookup_domain(
            str(row["company"]), str(row.get("country") or ""), search_fn=search_fn
        )
        if found:
            row["domain"] = found
            row["website"] = f"https://{found}"
            if row.get("source") in {"sirene", "companies_house", "hiring", "osm"}:
                row.setdefault("extra", {})
                row["extra"]["domain_source"] = "web"
    for row in ordered:
        if row.get("email") or not row.get("domain"):
            continue
        first = str(row.get("first_name") or "")
        last = str(row.get("last_name") or "")
        if not (first or last):
            parts = str(row.get("person") or "").split()
            first, last = (
                (parts[0], parts[-1]) if len(parts) > 1 else (parts[0] if parts else "", "")
            )
        if not (first or last):
            continue
        if not domain_accepts_mail(str(row["domain"]), resolver=resolver):
            continue
        guess = guess_email(first, last, str(row["domain"]))
        if guess:
            row["email"] = guess
            row["email_status"] = "guessed"
            spent["emails"] += 1
    for row in ordered:
        if spent["sites"] >= site_budget:
            break
        if not row.get("domain") or row.get("email") or row.get("phone"):
            continue
        spent["sites"] += 1
        found = enrich_site(
            str(row.get("website") or row["domain"]),
            country=str(row.get("country") or ""),
            fetch_fn=fetch_fn,
        )
        for key in ("email", "email_status", "phone"):
            if found.get(key) and not row.get(key):
                row[key] = found[key]
    return spent
