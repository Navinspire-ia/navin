"""Official API / Open Data fetchers. HTML portals go through scrape_net tools.

Every fetcher takes the collect brief (crafts, project types, countries,
recency) and asks the API for those notices: CPV divisions and full text on
TED, BOAMP descriptors and object text, country facets on the World Bank,
title keywords on SAM.gov. Feeds whose API cannot filter (Find a Tender,
Contracts Finder, CanadaBuys) are fetched wide, then kept only when the
brief matches. Without a brief they behave as before: latest notices first.
"""

from __future__ import annotations

import csv
import io
import os
import re
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from navin.tenders.brief import FetchBrief, filter_brief, matches_brief
from navin.tenders.http import get_bytes, get_json, post_json
from navin.tenders.needs import lookup_need
from navin.tenders.normalize import iso_date, normalize_tender

TED_LIMIT = 100
UK_LIMIT = 100
WB_ROWS = 40
BOAMP_LIMIT = 100
SAM_LIMIT = 50

_ISO3 = {
    "FRA": "FR",
    "DEU": "DE",
    "BEL": "BE",
    "NLD": "NL",
    "ITA": "IT",
    "ESP": "ES",
    "PRT": "PT",
    "IRL": "IE",
    "AUT": "AT",
    "CHE": "CH",
    "SWE": "SE",
    "NOR": "NO",
    "DNK": "DK",
    "FIN": "FI",
    "POL": "PL",
    "CZE": "CZ",
    "GRC": "GR",
    "ROU": "RO",
    "HUN": "HU",
    "HRV": "HR",
    "BGR": "BG",
    "SVK": "SK",
    "SVN": "SI",
    "LTU": "LT",
    "LVA": "LV",
    "EST": "EE",
    "MLT": "MT",
    "CYP": "CY",
    "LUX": "LU",
    "ISL": "IS",
    "GBR": "GB",
    "USA": "US",
    "CAN": "CA",
    "SAU": "SA",
    "ARE": "AE",
    "QAT": "QA",
    "KWT": "KW",
    "BHR": "BH",
    "OMN": "OM",
    "TUN": "TN",
    "MAR": "MA",
    "DZA": "DZ",
    "SEN": "SN",
    "CIV": "CI",
    "KEN": "KE",
    "EGY": "EG",
    "RWA": "RW",
    "ZAF": "ZA",
    "MDG": "MG",
    "IND": "IN",
    "AUS": "AU",
    "NZL": "NZ",
    "SGP": "SG",
    "BRA": "BR",
    "CHL": "CL",
    "MEX": "MX",
    "JOR": "JO",
}
_ISO2_TO_ISO3 = {iso2: iso3 for iso3, iso2 in _ISO3.items()}

_NAME_ISO = {
    "france": "FR",
    "germany": "DE",
    "belgium": "BE",
    "netherlands": "NL",
    "italy": "IT",
    "spain": "ES",
    "portugal": "PT",
    "ireland": "IE",
    "austria": "AT",
    "switzerland": "CH",
    "sweden": "SE",
    "norway": "NO",
    "denmark": "DK",
    "finland": "FI",
    "poland": "PL",
    "czechia": "CZ",
    "czech republic": "CZ",
    "greece": "GR",
    "romania": "RO",
    "hungary": "HU",
    "croatia": "HR",
    "bulgaria": "BG",
    "slovakia": "SK",
    "slovenia": "SI",
    "lithuania": "LT",
    "latvia": "LV",
    "estonia": "EE",
    "malta": "MT",
    "cyprus": "CY",
    "luxembourg": "LU",
    "iceland": "IS",
    "ukraine": "UA",
    "moldova": "MD",
    "georgia": "GE",
    "armenia": "AM",
    "azerbaijan": "AZ",
    "turkiye": "TR",
    "turkey": "TR",
    "serbia": "RS",
    "albania": "AL",
    "bosnia and herzegovina": "BA",
    "north macedonia": "MK",
    "kosovo": "XK",
    "montenegro": "ME",
    "senegal": "SN",
    "tunisia": "TN",
    "morocco": "MA",
    "algeria": "DZ",
    "libya": "LY",
    "mauritania": "MR",
    "mali": "ML",
    "niger": "NE",
    "burkina faso": "BF",
    "benin": "BJ",
    "togo": "TG",
    "ghana": "GH",
    "nigeria": "NG",
    "cameroon": "CM",
    "chad": "TD",
    "gabon": "GA",
    "congo": "CG",
    "congo, democratic republic of": "CD",
    "democratic republic of congo": "CD",
    "guinea": "GN",
    "sierra leone": "SL",
    "liberia": "LR",
    "gambia, the": "GM",
    "gambia": "GM",
    "kenya": "KE",
    "uganda": "UG",
    "tanzania": "TZ",
    "ethiopia": "ET",
    "somalia": "SO",
    "djibouti": "DJ",
    "sudan": "SD",
    "south sudan": "SS",
    "egypt": "EG",
    "rwanda": "RW",
    "burundi": "BI",
    "madagascar": "MG",
    "mozambique": "MZ",
    "malawi": "MW",
    "zambia": "ZM",
    "zimbabwe": "ZW",
    "angola": "AO",
    "namibia": "NA",
    "botswana": "BW",
    "south africa": "ZA",
    "lesotho": "LS",
    "eswatini": "SZ",
    "comoros": "KM",
    "mauritius": "MU",
    "cabo verde": "CV",
    "cote d'ivoire": "CI",
    "côte d'ivoire": "CI",
    "ivory coast": "CI",
    "india": "IN",
    "pakistan": "PK",
    "bangladesh": "BD",
    "sri lanka": "LK",
    "nepal": "NP",
    "bhutan": "BT",
    "maldives": "MV",
    "afghanistan": "AF",
    "iraq": "IQ",
    "iran": "IR",
    "jordan": "JO",
    "lebanon": "LB",
    "syria": "SY",
    "yemen": "YE",
    "west bank and gaza": "PS",
    "saudi arabia": "SA",
    "united arab emirates": "AE",
    "qatar": "QA",
    "kuwait": "KW",
    "bahrain": "BH",
    "oman": "OM",
    "kazakhstan": "KZ",
    "uzbekistan": "UZ",
    "kyrgyz republic": "KG",
    "kyrgyzstan": "KG",
    "tajikistan": "TJ",
    "turkmenistan": "TM",
    "mongolia": "MN",
    "china": "CN",
    "vietnam": "VN",
    "viet nam": "VN",
    "cambodia": "KH",
    "lao pdr": "LA",
    "laos": "LA",
    "thailand": "TH",
    "myanmar": "MM",
    "malaysia": "MY",
    "indonesia": "ID",
    "philippines": "PH",
    "timor-leste": "TL",
    "papua new guinea": "PG",
    "fiji": "FJ",
    "solomon islands": "SB",
    "vanuatu": "VU",
    "samoa": "WS",
    "tonga": "TO",
    "australia": "AU",
    "new zealand": "NZ",
    "singapore": "SG",
    "canada": "CA",
    "united states": "US",
    "united states of america": "US",
    "mexico": "MX",
    "guatemala": "GT",
    "honduras": "HN",
    "el salvador": "SV",
    "nicaragua": "NI",
    "costa rica": "CR",
    "panama": "PA",
    "colombia": "CO",
    "ecuador": "EC",
    "peru": "PE",
    "bolivia": "BO",
    "brazil": "BR",
    "paraguay": "PY",
    "uruguay": "UY",
    "argentina": "AR",
    "chile": "CL",
    "haiti": "HT",
    "dominican republic": "DO",
    "jamaica": "JM",
    "united kingdom": "GB",
    "great britain": "GB",
}
_ISO_NAME = {iso: name.title() for name, iso in _NAME_ISO.items()}
_ISO_NAME.update({"US": "United States", "AE": "United Arab Emirates", "GB": "United Kingdom", "CI": "Cote d'Ivoire", "CD": "Congo, Democratic Republic of", "TR": "Turkiye", "KG": "Kyrgyz Republic", "LA": "Lao PDR", "VN": "Vietnam", "CZ": "Czechia"})

# EU / EEA buyers published on TED. Other profile countries never narrow the TED query.
_TED_ISO2 = frozenset(
    {
        "FR", "DE", "BE", "NL", "IT", "ES", "PT", "IE", "AT", "CH", "SE", "NO", "DK", "FI",
        "PL", "CZ", "GR", "RO", "HU", "HR", "BG", "SK", "SI", "LT", "LV", "EE", "MT", "CY",
        "LU", "IS",
    }
)

# BOAMP descriptor labels (DILA thesaurus) for each need family.
_BOAMP_DESCRIPTORS: dict[str, tuple[str, ...]] = {
    "it-services": ("Informatique",),
    "ai": ("Informatique",),
    "data": ("Informatique",),
    "cloud": ("Informatique",),
    "digital": ("Informatique",),
    "cyber": ("Informatique",),
    "software": ("Informatique",),
    "office-it": ("Informatique",),
    "software-build": ("Informatique",),
    "tma": ("Informatique",),
    "data-platform": ("Informatique",),
    "cloud-migration": ("Informatique",),
    "cyber-audit": ("Informatique",),
    "erp": ("Informatique",),
    "web": ("Informatique",),
    "network": ("Télécommunications", "Informatique"),
    "postal-telecom": ("Télécommunications",),
    "telecom-hw": ("Télécommunications",),
    "business": ("Conseil", "Études"),
    "consulting": ("Conseil", "Études"),
    "amo-study": ("Études", "Conseil"),
    "research": ("Études",),
    "education": ("Formation",),
    "training-delivery": ("Formation",),
    "construction": ("Bâtiment",),
    "construction-delivery": ("Bâtiment",),
    "structures": ("Bâtiment",),
    "civil-works": ("Voirie", "Génie civil"),
    "architecture": ("Maîtrise d'oeuvre", "Architecture"),
    "environment": ("Déchets", "Assainissement"),
    "cleaning": ("Nettoyage", "Déchets"),
    "water": ("Eau", "Assainissement"),
    "water-works": ("Assainissement", "Eau"),
    "energy-fuel": ("Énergie", "Électricité"),
    "energy-works": ("Chauffage", "Énergie"),
    "electrical": ("Électricité",),
    "medical": ("Médical",),
    "health": ("Santé", "Médical"),
    "medical-fitout": ("Médical",),
    "security": ("Sécurité", "Surveillance"),
    "security-ops": ("Surveillance", "Gardiennage"),
    "transport": ("Transport",),
    "transport-eq": ("Véhicules",),
    "fleet": ("Véhicules",),
    "finance": ("Assurance", "Banque"),
    "furniture": ("Mobilier",),
    "food": ("Restauration",),
    "catering": ("Restauration",),
    "print": ("Imprimerie",),
    "culture": ("Culture",),
    "hospitality": ("Restauration", "Hôtellerie"),
    "facility": ("Nettoyage", "Maintenance"),
    "maintenance": ("Maintenance",),
    "installation": ("Installation",),
    "real-estate": ("Immobilier",),
    "agriculture": ("Agriculture",),
    "agri-services": ("Espaces verts",),
    "sport": ("Sport",),
    "lab": ("Laboratoire",),
    "chemicals": ("Produits chimiques",),
    "clothing": ("Habillement",),
    "textile": ("Textile",),
    "mining": ("Mines",),
    "oil-gas": ("Pétrole",),
    "admin": ("Administration",),
    "community": ("Services",),
    "utilities": ("Réseaux",),
    "industry-machines": ("Machines",),
    "works-machines": ("Engins",),
    "agri-machinery": ("Machines agricoles",),
    "transport-support": ("Manutention", "Logistique"),
}


def localized(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("eng", "fra", "en", "fr"):
            if value.get(key):
                return localized(value[key])
        return localized(next(iter(value.values()), ""))
    if isinstance(value, list):
        return localized(value[0]) if value else ""
    return str(value or "")


def joined(value: Any, sep: str = " | ") -> str:
    """Like localized() but keeps every lot line instead of the first one."""
    if isinstance(value, dict):
        for key in ("eng", "fra", "en", "fr"):
            if value.get(key):
                return joined(value[key], sep)
        return joined(next(iter(value.values()), ""), sep)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = joined(item, sep).strip()
            if text and text not in parts:
                parts.append(text)
        return sep.join(parts)
    return str(value or "")


def country_code(value: Any) -> str:
    text = localized(value).strip()
    if not text:
        return "INTL"
    upper = text.upper()
    if len(upper) == 2 and upper.isalpha():
        return upper
    if upper in _ISO3:
        return _ISO3[upper]
    return _NAME_ISO.get(text.lower(), upper[:16])


def country_name(iso: str) -> str:
    return _ISO_NAME.get(str(iso or "").upper(), str(iso or ""))


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _since(brief: FetchBrief | None, default_days: int = 30) -> date:
    days = brief.days if brief is not None else default_days
    return _today() - timedelta(days=max(1, int(days)))


def _future_deadline(value: Any) -> bool:
    text = iso_date(value)
    if len(text) < 10:
        return True
    try:
        return date.fromisoformat(text[:10]) >= _today()
    except ValueError:
        return True


def _first_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, list):
        for item in value:
            number = _first_number(item)
            if number is not None:
                return number
        return None
    text = str(value or "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _ocds_url(release: dict[str, Any], fallback: str) -> str:
    tender = release.get("tender") if isinstance(release.get("tender"), dict) else {}
    docs = tender.get("documents") if isinstance(tender.get("documents"), list) else []
    for doc in docs:
        if isinstance(doc, dict) and doc.get("url"):
            return str(doc["url"])
    return fallback


def _ocds_cpv(tender: dict[str, Any]) -> str:
    codes: list[str] = []
    classification = tender.get("classification")
    if isinstance(classification, dict) and classification.get("id"):
        codes.append(str(classification["id"]))
    extra = tender.get("additionalClassifications")
    if isinstance(extra, list):
        for item in extra:
            if isinstance(item, dict) and item.get("id"):
                codes.append(str(item["id"]))
    items = tender.get("items")
    if isinstance(items, list):
        for item in items:
            cls = item.get("classification") if isinstance(item, dict) else None
            if isinstance(cls, dict) and cls.get("id"):
                codes.append(str(cls["id"]))
    return " ".join(dict.fromkeys(codes))[:80]


def _ocds_notice(
    release: dict[str, Any],
    *,
    source_id: str,
    country: str,
    fallback_url: str,
) -> dict[str, Any] | None:
    if not isinstance(release, dict):
        return None
    tender = release.get("tender") if isinstance(release.get("tender"), dict) else {}
    status = str(tender.get("status") or "").lower()
    if status in {"cancelled", "unsuccessful", "withdrawn"}:
        return None
    period = tender.get("tenderPeriod") if isinstance(tender.get("tenderPeriod"), dict) else {}
    deadline = period.get("endDate") or tender.get("tenderPeriod")
    if status == "complete" and not _future_deadline(deadline):
        return None
    buyer = release.get("buyer") if isinstance(release.get("buyer"), dict) else {}
    value = tender.get("value") if isinstance(tender.get("value"), dict) else {}
    ocid = str(release.get("ocid") or release.get("id") or "")
    title = localized(tender.get("title") or release.get("id"))
    if not title:
        return None
    return normalize_tender(
        {
            "title": title,
            "description": localized(tender.get("description")),
            "country": country,
            "buyer": localized(buyer.get("name") or tender.get("procuringEntity")),
            "deadline": deadline,
            "publication_date": release.get("date") or period.get("startDate"),
            "source_url": _ocds_url(release, fallback_url),
            "reference": ocid or str(tender.get("id") or ""),
            "budget": value.get("amount") if isinstance(value.get("amount"), (int, float)) else None,
            "currency": value.get("currency") or "GBP",
            "cpv": _ocds_cpv(tender),
            "status": status or "open",
        },
        source_id=source_id,
    )


# --- World Bank -----------------------------------------------------------

_WB_BASE = "https://search.worldbank.org/api/v2/procnotices"
_WB_AWARD_TYPES = ("contract award", "award")


def _wb_rows(qs: dict[str, Any]) -> list[dict[str, Any]]:
    raw = get_json(f"{_WB_BASE}?{urllib.parse.urlencode(qs)}")
    rows: Any = []
    if isinstance(raw, dict):
        rows = raw.get("procnotices") or raw.get("documents") or raw.get("rows") or []
        if not rows and isinstance(raw.get("response"), dict):
            rows = raw["response"].get("docs") or []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _wb_notice(item: dict[str, Any]) -> dict[str, Any] | None:
    notice_type = str(item.get("notice_type") or "")
    if any(marker in notice_type.lower() for marker in _WB_AWARD_TYPES):
        return None
    country = country_code(
        item.get("project_ctry_name")
        or item.get("countryshortname")
        or item.get("countryname")
        or item.get("country")
        or item.get("contact_ctry_name")
    )
    notice_id = str(item.get("id") or item.get("project_id") or "")
    notice_url = str(item.get("url") or item.get("noticeurl") or "")
    if not notice_url and notice_id:
        notice_url = (
            "https://projects.worldbank.org/en/projects-operations/"
            f"procurement-detail/{urllib.parse.quote(notice_id)}"
        )
    description = " ".join(
        str(part or "").strip()
        for part in (
            item.get("bid_description") or item.get("notice_text"),
            item.get("procurement_method_name"),
            notice_type,
        )
        if str(part or "").strip()
    )
    return normalize_tender(
        {
            "title": item.get("project_name") or item.get("bid_description") or notice_type or item.get("title"),
            "description": description or item.get("project_name") or notice_type,
            "country": country,
            "buyer": item.get("contact_organization") or "World Bank / borrower",
            "deadline": item.get("submission_deadline_date")
            or item.get("deadline_date")
            or item.get("deadline"),
            "publication_date": item.get("noticedate") or item.get("publication_date"),
            "source_url": notice_url
            or "https://projects.worldbank.org/en/projects-operations/procurement-search",
            "reference": notice_id,
            "sector": notice_type,
            "status": item.get("notice_status") or "open",
        },
        source_id="world-bank",
    )


def from_world_bank(brief: FetchBrief | None = None) -> tuple[list[dict[str, Any]], str]:
    """World Bank procurement notices, one call per profile country.

    The search API accepts `project_ctry_name_exact` plus a free-text `qterm`
    that understands `OR`, so a data shop hunting in Morocco asks for
    "Morocco AND (data OR cloud OR ...)" instead of every borrower notice on
    earth. Award notices are dropped (nothing left to bid on) and notices
    older than the brief window are ignored.
    """
    base = {"format": "json", "rows": WB_ROWS, "srt": "noticedate", "order": "desc"}
    calls: list[dict[str, Any]] = []
    qterm = brief.or_query() if brief is not None else ""
    if brief is not None:
        for iso in brief.countries[:6]:
            name = country_name(iso)
            if name and name != iso:
                call = {**base, "project_ctry_name_exact": name}
                if qterm:
                    call["qterm"] = qterm
                calls.append(call)
        if not calls and qterm:
            calls.append({**base, "qterm": qterm})
    if not calls:
        calls.append(dict(base))
    # Borrower EOIs stay open for months and the feed is thin: never look
    # back less than a quarter here even when the brief window is 30 days.
    since = ""
    if brief is not None:
        since = (_today() - timedelta(days=max(int(brief.days), 90))).isoformat()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[str] = []
    for qs in calls:
        try:
            rows = _wb_rows(qs)
        except (OSError, ValueError) as exc:
            errors.append(str(exc)[:80])
            continue
        for item in rows[:WB_ROWS]:
            notice = _wb_notice(item)
            if not notice or notice["id"] in seen:
                continue
            published = str(notice.get("publication_date") or "")
            if since and len(published) == 10 and published < since:
                continue
            # Without a server-side keyword the brief still has to match.
            if "qterm" not in qs and not matches_brief(notice, brief):
                continue
            seen.add(notice["id"])
            out.append(notice)
    if not out and errors and len(errors) == len(calls):
        raise ValueError("World Bank: " + "; ".join(errors[:2]))
    return out, f"{len(out)} World Bank notices"


# --- TED ------------------------------------------------------------------

_TED_FIELDS = [
    "publication-number",
    "notice-title",
    "buyer-name",
    "buyer-country",
    "publication-date",
    "deadline-receipt-tender-date-lot",
    "classification-cpv",
    "description-lot",
    "estimated-value-lot",
    "estimated-value-cur-lot",
    "notice-type",
    "place-of-performance",
]


def _ted_quote(term: str) -> str:
    return '"' + re.sub(r"[\"()]", " ", term).strip() + '"'


def ted_query(brief: FetchBrief | None) -> str:
    """Expert-search query: competition notices, recent, on-country, on-craft."""
    since = _since(brief).strftime("%Y%m%d")
    clauses = ["form-type=competition", f"publication-date>={since}"]
    if brief is not None:
        iso3 = [_ISO2_TO_ISO3[iso] for iso in brief.countries if iso in _TED_ISO2 and iso in _ISO2_TO_ISO3]
        if iso3:
            clauses.append(f"buyer-country IN ({' '.join(dict.fromkeys(iso3))})")
        needs: list[str] = []
        if brief.cpv:
            needs.append(f"classification-cpv IN ({' '.join(brief.cpv)})")
        terms = [_ted_quote(term) for term in brief.search_terms if _ted_quote(term) != '""']
        if terms:
            needs.append("FT ~ (" + " OR ".join(terms) + ")")
        if needs:
            clauses.append("(" + " OR ".join(needs) + ")")
    return " AND ".join(clauses) + " SORT BY publication-date DESC"


def from_ted(brief: FetchBrief | None = None) -> tuple[list[dict[str, Any]], str]:
    payload = {
        "query": ted_query(brief),
        "fields": list(_TED_FIELDS),
        "limit": TED_LIMIT if brief is not None else 25,
        "page": 1,
        "scope": "ACTIVE",
        "paginationMode": "PAGE_NUMBER",
    }
    raw = post_json("https://api.ted.europa.eu/v3/notices/search", payload)
    notices = []
    if isinstance(raw, dict):
        notices = raw.get("notices") or raw.get("results") or []
    if not isinstance(notices, list):
        notices = []
    out: list[dict[str, Any]] = []
    for item in notices[: payload["limit"]]:
        if not isinstance(item, dict):
            continue
        pub = str(item.get("publication-number") or item.get("publicationNumber") or "")
        title = localized(item.get("notice-title") or item.get("title") or pub)
        buyer = localized(item.get("buyer-name") or item.get("organisation-name-buyer") or item.get("buyer"))
        country = country_code(item.get("buyer-country") or item.get("place-of-performance-country") or "EU")
        cpv_raw = item.get("classification-cpv")
        cpv_codes = [str(code) for code in cpv_raw] if isinstance(cpv_raw, list) else [str(cpv_raw or "")]
        cpv = " ".join(dict.fromkeys(code for code in cpv_codes if code))
        currency = localized(item.get("estimated-value-cur-lot")) or "EUR"
        out.append(
            normalize_tender(
                {
                    "title": title,
                    "description": joined(item.get("description-lot")) or title,
                    "buyer": buyer,
                    "country": country,
                    "deadline": item.get("deadline-receipt-tender-date-lot")
                    or item.get("deadline-date-lot")
                    or item.get("deadline"),
                    "publication_date": item.get("publication-date"),
                    "reference": pub,
                    "cpv": cpv,
                    "budget": _first_number(item.get("estimated-value-lot")),
                    "currency": currency,
                    "sector": str(item.get("notice-type") or ""),
                    "source_url": (
                        f"https://ted.europa.eu/en/notice/-/detail/{urllib.parse.quote(pub)}"
                        if pub
                        else "https://ted.europa.eu/"
                    ),
                    "status": "open",
                },
                source_id="ted",
            )
        )
    return out, f"{len(out)} TED notices"


# --- United Kingdom -------------------------------------------------------

def from_find_a_tender(brief: FetchBrief | None = None) -> tuple[list[dict[str, Any]], str]:
    limit = UK_LIMIT if brief is not None else 25
    url = f"https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages?limit={limit}&stages=tender"
    if brief is not None:
        url += "&updatedFrom=" + urllib.parse.quote(_since(brief).isoformat() + "T00:00:00")
    raw = get_json(url)
    releases = raw.get("releases") if isinstance(raw, dict) else []
    if not isinstance(releases, list):
        releases = []
    out: list[dict[str, Any]] = []
    for item in releases[:limit]:
        notice_id = str((item or {}).get("id") or (item or {}).get("ocid") or "")
        fallback = (
            f"https://www.find-tender.service.gov.uk/Notice/{urllib.parse.quote(notice_id)}"
            if notice_id
            else "https://www.find-tender.service.gov.uk/"
        )
        row = _ocds_notice(item, source_id="find-a-tender", country="GB", fallback_url=fallback)
        if row:
            out.append(row)
    kept = filter_brief(out, brief)
    return kept, f"{len(kept)} Find a Tender notices" + (f" ({len(out)} fetched)" if len(kept) != len(out) else "")


def from_contracts_finder(brief: FetchBrief | None = None) -> tuple[list[dict[str, Any]], str]:
    criteria: dict[str, Any] = {"statuses": ["Open"]}
    if brief is not None:
        criteria["publishedFrom"] = _since(brief).isoformat()
    size = UK_LIMIT if brief is not None else 25
    raw = post_json(
        "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search",
        {"searchCriteria": criteria, "size": size},
    )
    releases = raw.get("releases") if isinstance(raw, dict) else []
    if not isinstance(releases, list):
        releases = []
    out: list[dict[str, Any]] = []
    for item in releases[:size]:
        ocid = str((item or {}).get("ocid") or (item or {}).get("id") or "")
        fallback = (
            f"https://www.contractsfinder.service.gov.uk/Notice/{urllib.parse.quote(ocid)}"
            if ocid
            else "https://www.contractsfinder.service.gov.uk/"
        )
        row = _ocds_notice(item, source_id="contracts-finder", country="GB", fallback_url=fallback)
        if row:
            out.append(row)
    kept = filter_brief(out, brief)
    return kept, f"{len(kept)} Contracts Finder notices" + (f" ({len(out)} fetched)" if len(kept) != len(out) else "")


# --- BOAMP ----------------------------------------------------------------

def _odsql_text(value: str) -> str:
    return '"' + str(value or "").replace('"', " ").strip() + '"'


def boamp_where(brief: FetchBrief | None) -> str:
    """ODSQL: open competition notices, narrowed to the brief's descriptors and object."""
    clauses = ["datelimitereponse >= now()"]
    if brief is None:
        return clauses[0]
    clauses.append('nature like "APPEL_OFFRE"')
    needs: list[str] = []
    seen: set[str] = set()
    families: list[str] = []
    for label in (*brief.keywords, *brief.keywords_fr):
        row = lookup_need(label)
        if row and row["id"] not in families:
            families.append(str(row["id"]))
    for family in families:
        for descriptor in _BOAMP_DESCRIPTORS.get(family, ()):
            clause = f"descripteur_libelle like {_odsql_text(descriptor)}"
            if clause not in seen:
                seen.add(clause)
                needs.append(clause)
    for term in brief.search_terms:
        clause = f"objet like {_odsql_text(term)}"
        if clause not in seen:
            seen.add(clause)
            needs.append(clause)
    if needs:
        clauses.append("(" + " OR ".join(needs[:16]) + ")")
    return " AND ".join(clauses)


def from_boamp(brief: FetchBrief | None = None) -> tuple[list[dict[str, Any]], str]:
    limit = BOAMP_LIMIT if brief is not None else 25
    params = urllib.parse.urlencode(
        {"limit": limit, "order_by": "dateparution desc", "where": boamp_where(brief)},
        quote_via=urllib.parse.quote,
    )
    url = (
        "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/"
        f"boamp/records?{params}"
    )
    raw = get_json(url)
    rows = raw.get("results") if isinstance(raw, dict) else []
    if not isinstance(rows, list):
        rows = []
    out: list[dict[str, Any]] = []
    for item in rows[:limit]:
        if not isinstance(item, dict):
            continue
        idweb = str(item.get("idweb") or item.get("id") or "")
        title = localized(item.get("objet") or item.get("nature_libelle") or idweb)
        if not title:
            continue
        descriptors = joined(item.get("descripteur_libelle"), ", ")
        procedure = localized(item.get("procedure_libelle"))
        departments = joined(item.get("code_departement"), ", ")
        description = " - ".join(
            part
            for part in (
                descriptors,
                procedure,
                f"Departement {departments}" if departments else "",
                localized(item.get("nature_libelle")),
            )
            if part
        )
        out.append(
            normalize_tender(
                {
                    "title": title,
                    "description": description or title,
                    "country": "FR",
                    "buyer": item.get("nomacheteur") or "Acheteur public",
                    "deadline": item.get("datelimitereponse"),
                    "publication_date": item.get("dateparution"),
                    "source_url": str(item.get("url_avis") or "")
                    or (
                        f"https://www.boamp.fr/pages/avis/?q=idweb:{urllib.parse.quote(idweb)}"
                        if idweb
                        else "https://www.boamp.fr/"
                    ),
                    "reference": idweb,
                    "sector": joined(item.get("type_marche"), ", ") or localized(item.get("nature_libelle")),
                    "status": "open",
                },
                source_id="boamp",
            )
        )
    return out, f"{len(out)} BOAMP notices"


# --- Canada ---------------------------------------------------------------

_CANADA_NEW = "https://canadabuys.canada.ca/opendata/pub/newTenderNotice-nouvelAvisAppelOffres.csv"
_CANADA_OPEN = "https://canadabuys.canada.ca/opendata/pub/openTenderNotice-ouvertAvisAppelOffres.csv"


def _parse_canada_csv(raw: bytes, limit: int = 40) -> list[dict[str, Any]]:
    text = raw.decode("utf-8-sig", errors="replace")
    try:
        rows_iter = list(csv.DictReader(io.StringIO(text)))
    except csv.Error as exc:
        raise ValueError(f"CanadaBuys CSV unreadable: {exc}") from exc
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in rows_iter:
        status = str(item.get("tenderStatus-appelOffresStatut-eng") or "").lower()
        if status and status not in {"open", "amended"}:
            continue
        title = item.get("title-titre-eng") or item.get("title-titre-fra")
        if not title:
            continue
        ref = str(
            item.get("referenceNumber-numeroReference")
            or item.get("solicitationNumber-numeroSollicitation")
            or title
        )
        if ref in seen:
            continue
        seen.add(ref)
        notice_url = item.get("noticeURL-URLavis-eng") or item.get("noticeURL-URLavis-fra") or ""
        out.append(
            normalize_tender(
                {
                    "title": title,
                    "description": item.get("tenderDescription-descriptionAppelOffres-eng")
                    or item.get("gsinDescription-nibsDescription-eng"),
                    "country": "CA",
                    "buyer": item.get("contractingEntityName-nomEntitContractante-eng")
                    or "Government of Canada",
                    "deadline": item.get("tenderClosingDate-appelOffresDateCloture"),
                    "publication_date": item.get("publicationDate-datePublication"),
                    "source_url": notice_url or "https://canadabuys.canada.ca/",
                    "reference": ref,
                    "sector": item.get("gsinDescription-nibsDescription-eng")
                    or item.get("unspscDescription-eng"),
                    "cpv": item.get("unspsc-eng") or item.get("unspsc") or "",
                    "currency": "CAD",
                    "status": "open",
                },
                source_id="canadabuys",
            )
        )
        if len(out) >= limit:
            break
    return out


def from_canadabuys(brief: FetchBrief | None = None) -> tuple[list[dict[str, Any]], str]:
    limit = 400 if brief is not None and brief.targeted else 40
    out = _parse_canada_csv(get_bytes(_CANADA_NEW), limit=limit)
    if len(out) < 10 or (brief is not None and brief.targeted):
        extra = _parse_canada_csv(get_bytes(_CANADA_OPEN), limit=limit)
        seen = {row.get("reference") for row in out}
        for row in extra:
            if row.get("reference") in seen:
                continue
            out.append(row)
            if len(out) >= limit:
                break
    kept = filter_brief(out, brief)[:60]
    return kept, f"{len(kept)} CanadaBuys notices" + (f" ({len(out)} fetched)" if len(kept) != len(out) else "")


# --- SAM.gov --------------------------------------------------------------

def from_sam(brief: FetchBrief | None = None) -> tuple[list[dict[str, Any]], str]:
    key = (os.environ.get("SAM_API_KEY") or os.environ.get("SAM_GOV_API_KEY") or "").strip()
    if not key:
        raise ValueError("SAM_API_KEY missing; US federal API stays catalog-only")
    posted = _since(brief)
    base = {
        "api_key": key,
        "limit": SAM_LIMIT if brief is not None else 25,
        "postedFrom": f"{posted.strftime('%m/%d/%Y')}",
        "postedTo": f"{_today().strftime('%m/%d/%Y')}",
        "ptype": "o,k,p,r",
    }
    calls: list[dict[str, Any]] = []
    if brief is not None and brief.search_terms:
        for term in brief.search_terms[:3]:
            calls.append({**base, "title": term})
    else:
        calls.append(dict(base))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for qs in calls:
        raw = get_json(f"https://api.sam.gov/opportunities/v2/search?{urllib.parse.urlencode(qs)}")
        rows = raw.get("opportunitiesData") if isinstance(raw, dict) else []
        if not isinstance(rows, list):
            rows = raw.get("data") if isinstance(raw, dict) else []
        if not isinstance(rows, list):
            rows = []
        for item in rows[: int(qs["limit"])]:
            if not isinstance(item, dict):
                continue
            notice_id = str(item.get("noticeId") or item.get("solicitationNumber") or "")
            notice = normalize_tender(
                {
                    "title": item.get("title"),
                    "description": item.get("description") or item.get("type"),
                    "country": "US",
                    "buyer": item.get("fullParentPathName") or item.get("organizationName") or "US federal",
                    "deadline": item.get("responseDeadLine") or item.get("responseDeadline"),
                    "publication_date": item.get("postedDate"),
                    "source_url": item.get("uiLink") or "https://sam.gov/content/opportunities",
                    "reference": notice_id,
                    "sector": item.get("naicsCode") or item.get("type"),
                    "currency": "USD",
                    "status": "open",
                },
                source_id="sam-gov",
            )
            if notice["id"] in seen:
                continue
            seen.add(notice["id"])
            out.append(notice)
    return out, f"{len(out)} SAM.gov notices"


Fetcher = Callable[..., tuple[list[dict[str, Any]], str]]

FETCHERS: dict[str, Fetcher] = {
    "ted": from_ted,
    "world-bank": from_world_bank,
    "boamp": from_boamp,
    "find-a-tender": from_find_a_tender,
    "contracts-finder": from_contracts_finder,
    "canadabuys": from_canadabuys,
    "sam-gov": from_sam,
}


def run_fetcher(fetcher: Fetcher, brief: FetchBrief | None) -> tuple[list[dict[str, Any]], str]:
    """Call a fetcher with the brief when it accepts one (custom zero-arg fetchers still work)."""
    import inspect

    try:
        params = inspect.signature(fetcher).parameters
    except (TypeError, ValueError):
        params = {}
    if params:
        return fetcher(brief)
    return fetcher()
