"""Waterfall discovery and enrichment. Stop as soon as a field is filled."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from navin.leads.normalize import normalize_company, normalize_domain, normalize_email
from navin.leads.qualify import apply_qualification, size_display
from navin.leads.schema import Lead, validate_lead

TIMEOUT_S = 12
USER_AGENT = "NavinLeads/2.0 (+https://navin.live)"
_PLACE_COUNTRIES = {
    "FR": "France",
    "GB": "United Kingdom",
    "US": "United States",
    "DE": "Germany",
    "ES": "Spain",
    "IT": "Italy",
    "AE": "United Arab Emirates",
}


def _basic_auth(user: str, password: str = "") -> str:
    raw = f"{user}:{password}".encode("ascii", errors="ignore")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _http_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    return {"Accept": "application/json", "User-Agent": USER_AGENT, **(extra or {})}


def _get_json(url: str, *, headers: dict[str, str] | None = None) -> Any:
    request = urllib.request.Request(url, headers=_http_headers(headers))
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None) -> Any:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers=_http_headers({"Content-Type": "application/json", **(headers or {})}),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


def _sirene_effectif(size_min: int, size_max: int) -> list[str]:
    bands = [
        (10, 19, "11"),
        (20, 49, "12"),
        (50, 99, "21"),
        (100, 199, "22"),
        (200, 249, "31"),
        (250, 499, "32"),
        (500, 999, "41"),
        (1000, 1999, "42"),
        (2000, 4999, "51"),
    ]
    return [code for low, high, code in bands if high >= size_min and low <= size_max]


_SECTOR_NAF = {
    "saas": ["62.01Z", "62.02A", "62.02B", "62.09Z", "63.11Z", "63.12Z"],
    "software": ["62.01Z", "62.02A", "62.02B", "62.09Z"],
    "logiciel": ["62.01Z", "62.02A", "62.02B", "62.09Z"],
    "logistique": ["52.29A", "52.29B", "49.41Z", "52.10A", "52.10B"],
    "logistics": ["52.29A", "52.29B", "49.41Z", "52.10A", "52.10B"],
}

_SKIP_PREFIX = (
    "commune",
    "mairie ",
    "chu ",
    "chu de ",
    "centre hospitalier",
    "hopital",
    "hôpital",
    "departement ",
    "département ",
    "region ",
    "région ",
    "ville de ",
    "prefecture",
    "préfecture",
    "syndicat",
    "epci ",
    "association",
    "l'association",
    "fondation",
    "groupe d'etude",
    "groupe d'étude",
    "groupe d’etude",
    "groupe d’étude",
)
_SKIP_TOKEN = (
    " commune ",
    " hopital ",
    " hôpital ",
    " chu ",
    " mairie ",
    " assistance publique ",
    " intercommunal ",
    " centre hospitalier ",
    " association ",
    " fondation ",
)


def _sirene_officer(item: dict[str, Any]) -> dict[str, str]:
    """Officers ship on the public search payload. No Pappers credit."""
    officers = item.get("dirigeants") if isinstance(item.get("dirigeants"), list) else []
    for officer in officers:
        if not isinstance(officer, dict):
            continue
        if str(officer.get("type_dirigeant") or "") == "personne morale":
            continue
        first = str(officer.get("prenoms") or officer.get("prenom") or "").strip()
        last = str(officer.get("nom") or "").strip()
        person = " ".join(part for part in (first, last) if part)
        if not person:
            continue
        return {
            "person": person,
            "first_name": first.split()[0] if first else "",
            "last_name": last,
            "role": str(officer.get("qualite") or "dirigeant"),
        }
    return {}


def _sirene_skip(item: dict[str, Any], name: str) -> bool:
    if not name or is_public_body(name):
        return True
    etat = str(item.get("etat_administratif") or "A").upper()
    if etat and etat != "A":
        return True
    extra = item.get("complements") if isinstance(item.get("complements"), dict) else {}
    if extra.get("est_association") or extra.get("collectivite_territoriale"):
        return True
    return False


def is_public_body(name: str) -> bool:
    """Skip towns, hospitals and other public bodies that pollute an ICP hunt."""
    raw = str(name or "").strip()
    if not raw:
        return True
    text = raw.casefold()
    if text.startswith(_SKIP_PREFIX):
        return True
    padded = f" {text} "
    return any(token in padded for token in _SKIP_TOKEN)


def hunt_sirene(profile: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    query = str(profile.get("sector") or profile.get("icp_name") or "societe").strip()
    params = {"q": query, "per_page": str(min(25, max(5, limit))), "page": "1"}
    codes = _sirene_effectif(int(profile.get("size_min") or 20), int(profile.get("size_max") or 200))
    if codes:
        params["tranche_effectif_salarie"] = ",".join(codes)
    naf = _SECTOR_NAF.get(query.casefold())
    if naf:
        params["activite_principale"] = ",".join(naf)
        params["q"] = "societe"
    url = "https://recherche-entreprises.api.gouv.fr/search?" + urllib.parse.urlencode(params)
    try:
        payload = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    rows: list[dict[str, Any]] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("nom_complet") or item.get("nom_raison_sociale") or "").strip()
        if _sirene_skip(item, name):
            continue
        siege = item.get("siege") if isinstance(item.get("siege"), dict) else {}
        domain = normalize_domain(
            str(item.get("domaine") or item.get("site_internet") or siege.get("domaine") or "")
        )
        siren = str(item.get("siren") or "").strip()
        address = str(siege.get("geo_adresse") or siege.get("adresse") or "").strip()
        officer = _sirene_officer(item)
        rows.append(
            {
                "company": normalize_company(name),
                "domain": domain,
                "website": f"https://{domain}" if domain else "",
                "country": "FR",
                "sector": str(item.get("section_activite_principale") or profile.get("sector") or ""),
                "size": size_display(item.get("tranche_effectif_salarie") or ""),
                "source": "sirene",
                "signal": address or "INSEE / SIRENE",
                "confidence": "high",
                "extra": {"siren": siren, "siret": siege.get("siret") or ""},
                **officer,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def hunt_companies_house(profile: dict[str, Any], key: str, limit: int) -> list[dict[str, Any]]:
    if not key:
        return []
    query = str(profile.get("sector") or profile.get("icp_name") or "limited").strip()
    url = "https://api.company-information.service.gov.uk/search/companies?" + urllib.parse.urlencode(
        {"q": query, "items_per_page": str(min(20, limit))}
    )
    try:
        payload = _get_json(url, headers={"Authorization": _basic_auth(key)})
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return []
    rows: list[dict[str, Any]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("title") or "").strip()
        if not name:
            continue
        rows.append(
            {
                "company": normalize_company(name),
                "country": "GB",
                "sector": str(profile.get("sector") or ""),
                "source": "companies_house",
                "signal": str(item.get("company_status") or "Companies House"),
                "confidence": "high",
                "extra": {"company_number": item.get("company_number")},
            }
        )
        if len(rows) >= limit:
            break
    return rows


def enrich_hunter_domain(domain: str, key: str, titles: list[str]) -> dict[str, Any]:
    if not key or not domain:
        return {}
    wanted = {item.casefold() for item in titles}
    url = "https://api.hunter.io/v2/domain-search?" + urllib.parse.urlencode(
        {"domain": domain, "api_key": key, "limit": "10"}
    )
    try:
        payload = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return {}
    data = payload.get("data") if isinstance(payload, dict) else {}
    emails = data.get("emails") if isinstance(data, dict) else []
    if not isinstance(emails, list):
        return {}
    picked: dict[str, Any] | None = None
    for item in emails:
        if not isinstance(item, dict):
            continue
        position = str(item.get("position") or "")
        if wanted and not any(token in position.casefold() for token in wanted):
            continue
        picked = item
        break
    if picked is None and emails:
        first = emails[0]
        picked = first if isinstance(first, dict) else None
    if not picked:
        return {}
    email = normalize_email(str(picked.get("value") or ""))
    first = str(picked.get("first_name") or "").strip()
    last = str(picked.get("last_name") or "").strip()
    person = " ".join(part for part in (first, last) if part)
    return {
        "person": person,
        "first_name": first,
        "last_name": last,
        "role": str(picked.get("position") or ""),
        "email": email,
        "email_status": "verified" if picked.get("verification", {}).get("status") == "valid" else "unverified",
        "linkedin_url": str(picked.get("linkedin") or ""),
        "phone": str(picked.get("phone_number") or ""),
        "source": "hunter",
    }


def enrich_apollo_person(domain: str, key: str, titles: list[str]) -> dict[str, Any]:
    if not key or not domain:
        return {}
    try:
        payload = _post_json(
            "https://api.apollo.io/api/v1/mixed_people/search",
            {
                "q_organization_domains": domain,
                "person_titles": titles[:8],
                "page": 1,
                "per_page": 5,
            },
            headers={"X-Api-Key": key},
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return {}
    people = payload.get("people") if isinstance(payload, dict) else []
    if not isinstance(people, list) or not people:
        return {}
    first = people[0] if isinstance(people[0], dict) else {}
    email = normalize_email(str(first.get("email") or ""))
    return {
        "person": str(first.get("name") or "").strip(),
        "first_name": str(first.get("first_name") or ""),
        "last_name": str(first.get("last_name") or ""),
        "role": str(first.get("title") or ""),
        "email": email,
        "email_status": "verified" if first.get("email_status") == "verified" else ("unverified" if email else ""),
        "linkedin_url": str(first.get("linkedin_url") or ""),
        "phone": str((first.get("phone_numbers") or [{}])[0].get("sanitized_number") or "")
        if isinstance(first.get("phone_numbers"), list) and first.get("phone_numbers")
        else "",
        "source": "apollo",
    }


def enrich_pdl_company(domain: str, key: str) -> dict[str, Any]:
    if not key or not domain:
        return {}
    url = "https://api.peopledatalabs.com/v5/company/enrich?" + urllib.parse.urlencode({"website": domain})
    try:
        payload = _get_json(url, headers={"X-Api-Key": key})
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        "sector": str(payload.get("industry") or ""),
        "size": str(payload.get("size") or payload.get("employee_count") or ""),
        "linkedin_url": str(payload.get("linkedin_url") or ""),
        "source": "pdl",
    }


def enrich_pappers(siren: str, key: str) -> dict[str, Any]:
    if not key or not siren:
        return {}
    url = "https://api.pappers.fr/v2/entreprise?" + urllib.parse.urlencode({"siren": siren, "api_token": key})
    try:
        payload = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(payload, dict):
        return {}
    reps = payload.get("representants") if isinstance(payload.get("representants"), list) else []
    officer = reps[0] if reps and isinstance(reps[0], dict) else {}
    person = " ".join(
        part for part in (str(officer.get("prenom") or "").strip(), str(officer.get("nom") or "").strip()) if part
    )
    return {
        "person": person,
        "role": str(officer.get("qualite") or "dirigeant"),
        "phone": str(payload.get("telephone") or ""),
        "website": str(payload.get("site_internet") or ""),
        "source": "pappers",
        "signal": "Pappers officers",
    }


def apply_fields(row: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Fill only empty commercial fields. Never spend a second credit on the same email."""
    out = dict(row)
    for key, value in patch.items():
        if key in {"source", "signal", "extra"}:
            if key == "extra" and isinstance(value, dict):
                extra = dict(out.get("extra") or {})
                extra.update(value)
                out["extra"] = extra
            elif key == "source":
                if not out.get("source") and value:
                    out["source"] = value
                continue
            elif key == "signal" and value:
                current = str(out.get("signal") or "").strip()
                out["signal"] = f"{current}; {value}" if current and value not in current else (current or value)
            continue
        if value in (None, "", [], {}):
            continue
        if key == "email" and out.get("email"):
            continue
        if key == "email_status" and value == "verified":
            out[key] = value
            continue
        if not out.get(key):
            out[key] = value
    return out


def enrich_row(
    row: dict[str, Any],
    profile: dict[str, Any],
    secrets: dict[str, str],
    *,
    deep: bool = False,
    paid: bool | None = None,
) -> dict[str, Any]:
    """Fill empty fields. Paid APIs only when asked. LinkedIn never fetched.

    ``deep=False`` (bulk hunt): score the free hit. No BYOK spend.
    ``deep=True`` (single enrich): paid APIs + web domain lookup, site scrape, Hunter verify.
    """
    use_paid = bool(deep if paid is None else paid)
    titles = [str(item) for item in (profile.get("titles") or [])]
    domain = normalize_domain(str(row.get("domain") or row.get("website") or ""))
    next_row = dict(row)
    extra = next_row.get("extra") if isinstance(next_row.get("extra"), dict) else {}
    if domain:
        next_row["domain"] = domain
        if not next_row.get("website"):
            next_row["website"] = f"https://{domain}"

    if use_paid:
        if secrets.get("pappers") and extra.get("siren") and (
            not next_row.get("person") or not next_row.get("website")
        ):
            next_row = apply_fields(next_row, enrich_pappers(str(extra.get("siren")), secrets["pappers"]))
        if secrets.get("places") and extra.get("place_id") and (
            not next_row.get("website") or not next_row.get("phone")
        ):
            next_row = apply_fields(
                next_row, enrich_places_details(str(extra.get("place_id")), secrets["places"])
            )
        if secrets.get("opencorporates") and extra.get("company_number") and not next_row.get("person"):
            next_row = apply_fields(
                next_row,
                enrich_opencorporates(
                    str(extra.get("company_number")),
                    str(extra.get("jurisdiction") or ""),
                    secrets["opencorporates"],
                ),
            )
        if secrets.get("crunchbase") and extra.get("permalink") and not next_row.get("domain"):
            next_row = apply_fields(
                next_row, enrich_crunchbase(str(extra.get("permalink")), secrets["crunchbase"])
            )
        domain = normalize_domain(str(next_row.get("domain") or next_row.get("website") or ""))
        if secrets.get("pdl") and domain and not next_row.get("sector"):
            next_row = apply_fields(next_row, enrich_pdl_company(domain, secrets["pdl"]))
        if secrets.get("apollo") and domain and not next_row.get("email"):
            next_row = apply_fields(next_row, enrich_apollo_person(domain, secrets["apollo"], titles))
        if secrets.get("hunter") and domain and not next_row.get("email") and next_row.get("person"):
            parts = str(next_row.get("person") or "").split()
            first = str(next_row.get("first_name") or (parts[0] if parts else ""))
            last = str(next_row.get("last_name") or (parts[-1] if len(parts) > 1 else ""))
            next_row = apply_fields(next_row, enrich_hunter_finder(domain, first, last, secrets["hunter"]))
        if secrets.get("hunter") and domain and not next_row.get("email"):
            next_row = apply_fields(next_row, enrich_hunter_domain(domain, secrets["hunter"], titles))

    if deep:
        if not next_row.get("domain") and next_row.get("company"):
            from navin.leads.scrape_net import lookup_domain

            found_domain = lookup_domain(
                str(next_row.get("company") or ""),
                str(next_row.get("country") or ""),
            )
            if found_domain:
                next_row = apply_fields(
                    next_row,
                    {
                        "domain": found_domain,
                        "website": f"https://{found_domain}",
                        "source": "web",
                    },
                )
                domain = found_domain
        if domain and not next_row.get("email") and not next_row.get("phone"):
            from navin.leads.scrape_net import enrich_site

            next_row = apply_fields(
                next_row,
                enrich_site(
                    str(next_row.get("website") or domain),
                    country=str(next_row.get("country") or ""),
                ),
            )
        if secrets.get("hunter") and next_row.get("email") and next_row.get("email_status") != "verified":
            next_row = apply_fields(
                next_row, enrich_hunter_verify(str(next_row.get("email") or ""), secrets["hunter"])
            )

    next_row = apply_qualification(next_row, profile)
    lead = Lead.from_row(next_row)
    next_row["gaps"] = validate_lead(lead)
    return next_row


def _apollo_employee_ranges(size_min: int, size_max: int) -> list[str]:
    bands = [
        (1, 10, "1,10"),
        (11, 20, "11,20"),
        (21, 50, "21,50"),
        (51, 100, "51,100"),
        (101, 200, "101,200"),
        (201, 500, "201,500"),
        (501, 1000, "501,1000"),
        (1001, 2000, "1001,2000"),
        (2001, 5000, "2001,5000"),
        (5001, 10000, "5001,10000"),
    ]
    return [code for low, high, code in bands if high >= size_min and low <= size_max]


def hunt_apollo(profile: dict[str, Any], key: str, limit: int) -> list[dict[str, Any]]:
    if not key or limit <= 0:
        return []
    countries = [_PLACE_COUNTRIES.get(str(item).upper(), str(item)) for item in (profile.get("countries") or [])]
    ranges = _apollo_employee_ranges(int(profile.get("size_min") or 20), int(profile.get("size_max") or 200))
    body: dict[str, Any] = {
        "q_organization_keyword_tags": [str(profile.get("sector") or profile.get("icp_name") or "").strip()][:1],
        "page": 1,
        "per_page": min(25, max(5, limit)),
    }
    if countries:
        body["organization_locations"] = countries
    if ranges:
        body["organization_num_employees_ranges"] = ranges
    try:
        payload = _post_json(
            "https://api.apollo.io/api/v1/mixed_companies/search",
            body,
            headers={"X-Api-Key": key},
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    orgs = payload.get("organizations") if isinstance(payload, dict) else []
    if not isinstance(orgs, list):
        orgs = payload.get("accounts") if isinstance(payload, dict) else []
    if not isinstance(orgs, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in orgs:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        domain = normalize_domain(str(item.get("primary_domain") or item.get("website_url") or ""))
        country = str((item.get("country") or item.get("organization_country") or "") or "")
        rows.append(
            {
                "company": normalize_company(name),
                "domain": domain,
                "website": str(item.get("website_url") or (f"https://{domain}" if domain else "")),
                "country": country[:2].upper() if country else "",
                "sector": str(item.get("industry") or profile.get("sector") or ""),
                "size": str(item.get("estimated_num_employees") or ""),
                "linkedin_url": str(item.get("linkedin_url") or ""),
                "phone": str(item.get("phone") or item.get("sanitized_phone") or ""),
                "source": "apollo",
                "signal": "Apollo company search",
                "confidence": "high",
            }
        )
        if len(rows) >= limit:
            break
    return rows


def hunt_places(profile: dict[str, Any], key: str, limit: int) -> list[dict[str, Any]]:
    if not key or limit <= 0:
        return []
    sector = str(profile.get("sector") or profile.get("icp_name") or "company").strip()
    labels = [_PLACE_COUNTRIES.get(str(item).upper(), str(item)) for item in (profile.get("countries") or [])]
    query = f"{sector} {labels[0]}" if labels else sector
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json?" + urllib.parse.urlencode(
        {"query": query, "key": key}
    )
    try:
        payload = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    if isinstance(payload, dict) and payload.get("status") not in {None, "", "OK", "ZERO_RESULTS"}:
        return []
    results = payload.get("results") if isinstance(payload, dict) else []
    if not isinstance(results, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        loc = item.get("plus_code") if isinstance(item.get("plus_code"), dict) else {}
        iso = str((profile.get("countries") or [""])[0] or "").upper()[:2]
        rows.append(
            {
                "company": normalize_company(name),
                "country": iso,
                "sector": str(profile.get("sector") or ""),
                "phone": "",
                "source": "places",
                "signal": str(item.get("formatted_address") or loc.get("compound_code") or "Google Places"),
                "confidence": "medium",
                "extra": {"place_id": item.get("place_id")},
            }
        )
        if len(rows) >= limit:
            break
    return rows


_OC_JURISDICTION = {
    "FR": "fr",
    "GB": "gb",
    "UK": "gb",
    "DE": "de",
    "ES": "es",
    "IT": "it",
    "US": "us_de",
    "NL": "nl",
    "BE": "be",
    "IE": "ie",
}


def hunt_opencorporates(profile: dict[str, Any], key: str, limit: int) -> list[dict[str, Any]]:
    if not key or limit <= 0:
        return []
    query = str(profile.get("sector") or profile.get("icp_name") or "limited").strip()
    country = str((profile.get("countries") or ["FR"])[0] or "FR").upper()[:2]
    params = {
        "q": query,
        "per_page": str(min(20, max(5, limit))),
        "api_token": key,
    }
    jurisdiction = _OC_JURISDICTION.get(country)
    if jurisdiction:
        params["jurisdiction_code"] = jurisdiction
    url = "https://api.opencorporates.com/v0.4/companies/search?" + urllib.parse.urlencode(params)
    try:
        payload = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    results = payload.get("results") if isinstance(payload, dict) else {}
    companies = results.get("companies") if isinstance(results, dict) else []
    if not isinstance(companies, list):
        return []
    rows: list[dict[str, Any]] = []
    for wrap in companies:
        item = wrap.get("company") if isinstance(wrap, dict) else None
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        number = str(item.get("company_number") or "").strip()
        rows.append(
            {
                "company": normalize_company(name),
                "country": country,
                "sector": str(profile.get("sector") or ""),
                "source": "opencorporates",
                "signal": str(item.get("registered_address_in_full") or "OpenCorporates"),
                "confidence": "high",
                "extra": {
                    "company_number": number,
                    "jurisdiction": item.get("jurisdiction_code") or jurisdiction,
                },
            }
        )
        if len(rows) >= limit:
            break
    return rows


def hunt_crunchbase(profile: dict[str, Any], key: str, limit: int) -> list[dict[str, Any]]:
    if not key or limit <= 0:
        return []
    query = str(profile.get("sector") or profile.get("icp_name") or "software").strip()
    url = "https://api.crunchbase.com/api/v4/autocompletes?" + urllib.parse.urlencode(
        {"query": query, "collection_ids": "organizations", "limit": str(min(20, max(5, limit)))}
    )
    try:
        payload = _get_json(url, headers={"X-cb-user-key": key})
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    entities = payload.get("entities") if isinstance(payload, dict) else []
    if not isinstance(entities, list):
        return []
    country = str((profile.get("countries") or [""])[0] or "").upper()[:2]
    rows: list[dict[str, Any]] = []
    for item in entities:
        if not isinstance(item, dict):
            continue
        ident = item.get("identifier") if isinstance(item.get("identifier"), dict) else {}
        name = str(ident.get("value") or item.get("value") or "").strip()
        if not name:
            continue
        permalink = str(ident.get("permalink") or item.get("permalink") or "").strip()
        rows.append(
            {
                "company": normalize_company(name),
                "country": country,
                "sector": str(profile.get("sector") or ""),
                "source": "crunchbase",
                "signal": str(item.get("short_description") or "Crunchbase"),
                "confidence": "medium",
                "extra": {"permalink": permalink},
            }
        )
        if len(rows) >= limit:
            break
    return rows


def enrich_opencorporates(company_number: str, jurisdiction: str, key: str) -> dict[str, Any]:
    if not key or not company_number or not jurisdiction:
        return {}
    path = urllib.parse.quote(f"{jurisdiction}/{company_number}")
    url = f"https://api.opencorporates.com/v0.4/companies/{path}?" + urllib.parse.urlencode(
        {"api_token": key}
    )
    try:
        payload = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return {}
    wrap = payload.get("results") if isinstance(payload, dict) else {}
    item = wrap.get("company") if isinstance(wrap, dict) else {}
    if not isinstance(item, dict):
        return {}
    officers = item.get("officers") if isinstance(item.get("officers"), list) else []
    first = officers[0] if officers and isinstance(officers[0], dict) else {}
    officer = first.get("officer") if isinstance(first.get("officer"), dict) else first
    person = str(officer.get("name") or "").strip()
    return {
        "person": person,
        "role": str(officer.get("position") or "officer"),
        "source": "opencorporates",
        "signal": "OpenCorporates officers",
    }


def enrich_crunchbase(permalink: str, key: str) -> dict[str, Any]:
    if not key or not permalink:
        return {}
    url = f"https://api.crunchbase.com/api/v4/entities/organizations/{urllib.parse.quote(permalink)}"
    try:
        payload = _get_json(url, headers={"X-cb-user-key": key})
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return {}
    props = payload.get("properties") if isinstance(payload, dict) else {}
    if not isinstance(props, dict):
        return {}
    website = ""
    raw_web = props.get("website")
    if isinstance(raw_web, dict):
        website = str(raw_web.get("value") or "")
    elif isinstance(raw_web, str):
        website = raw_web
    domain = normalize_domain(website)
    funding = props.get("funding_total")
    signal = ""
    if isinstance(funding, dict) and funding.get("value_usd"):
        signal = f"funding {funding.get('value_usd')} USD"
    return {
        "domain": domain,
        "website": website or (f"https://{domain}" if domain else ""),
        "size": str(props.get("num_employees_enum") or ""),
        "sector": str(props.get("short_description") or "")[:80],
        "source": "crunchbase",
        "signal": signal or "Crunchbase",
    }


def enrich_places_details(place_id: str, key: str) -> dict[str, Any]:
    if not key or not place_id:
        return {}
    url = "https://maps.googleapis.com/maps/api/place/details/json?" + urllib.parse.urlencode(
        {
            "place_id": place_id,
            "fields": "website,formatted_phone_number,international_phone_number",
            "key": key,
        }
    )
    try:
        payload = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return {}
    if isinstance(payload, dict) and payload.get("status") not in {None, "", "OK"}:
        return {}
    result = payload.get("result") if isinstance(payload, dict) else {}
    if not isinstance(result, dict):
        return {}
    website = str(result.get("website") or "")
    domain = normalize_domain(website)
    phone = str(result.get("international_phone_number") or result.get("formatted_phone_number") or "")
    return {
        "domain": domain,
        "website": website or (f"https://{domain}" if domain else ""),
        "phone": phone,
        "source": "places",
    }


def enrich_hunter_finder(domain: str, first: str, last: str, key: str) -> dict[str, Any]:
    if not key or not domain or not (first or last):
        return {}
    url = "https://api.hunter.io/v2/email-finder?" + urllib.parse.urlencode(
        {"domain": domain, "first_name": first, "last_name": last, "api_key": key}
    )
    try:
        payload = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return {}
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return {}
    email = normalize_email(str(data.get("email") or ""))
    if not email:
        return {}
    status = str((data.get("verification") or {}).get("status") or "").lower()
    score = data.get("score")
    verified = status in {"valid", "verified"} or (
        isinstance(score, (int, float)) and float(score) >= 90
    )
    return {
        "email": email,
        "email_status": "verified" if verified else "unverified",
        "source": "hunter",
    }


def enrich_hunter_verify(email: str, key: str) -> dict[str, Any]:
    cleaned = normalize_email(email)
    if not key or not cleaned:
        return {}
    url = "https://api.hunter.io/v2/email-verifier?" + urllib.parse.urlencode(
        {"email": cleaned, "api_key": key}
    )
    try:
        payload = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return {}
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return {}
    status = str(data.get("status") or "").lower()
    if status == "valid":
        return {"email": cleaned, "email_status": "verified", "source": "hunter"}
    if status in {"invalid", "disposable", "unknown"}:
        return {"email": cleaned, "email_status": "unverified", "source": "hunter"}
    return {}


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same domain or same company name in the same country is one row; the first source wins,
    but a later row fills the blanks (a hiring row learns its domain from the web row)."""
    out: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    for row in rows:
        domain = normalize_domain(str(row.get("domain") or row.get("website") or ""))
        name = normalize_company(str(row.get("company") or "")).casefold()
        country = str(row.get("country") or "").upper()[:2]
        keys = [key for key in (f"d:{domain}" if domain else "", f"c:{country}:{name}" if name else "") if key]
        hit = next((index[key] for key in keys if key in index), None)
        if hit is None:
            out.append(dict(row))
            for key in keys:
                index[key] = len(out) - 1
            continue
        base = out[hit]
        for key, value in row.items():
            if value in (None, "", [], {}):
                continue
            if key == "signal" and base.get("signal") and value not in str(base["signal"]):
                base["signal"] = f"{base['signal']}; {value}"
            elif key == "extra" and isinstance(value, dict):
                base["extra"] = {**(base.get("extra") or {}), **value}
            elif not base.get(key):
                base[key] = value
        for key in keys:
            index.setdefault(key, hit)
    return out


def hunt_companies(
    profile: dict[str, Any],
    secrets: dict[str, str],
    limit: int,
    *,
    cursor: int = 0,
    search_fn: Any = None,
    http_get: Any = None,
    jobs_fn: Any = None,
) -> list[dict[str, Any]]:
    """Registries, then hiring signals, open web and OpenStreetMap, then paid BYOK.

    Every open source gets its own slice so one registry page never crowds out the
    warmer signals. ``cursor`` rotates cities and query angles between hunts.
    LinkedIn people pages are never fetched; job listings are public.
    """
    from navin.leads import discover

    countries = {str(item).upper() for item in (profile.get("countries") or ["FR"])}
    sources = set(profile.get("sources") or ["web", "osm", "hiring"])
    slice_size = max(10, limit // 3)
    found: list[dict[str, Any]] = []
    if "FR" in countries or not countries:
        found.extend(hunt_sirene(profile, slice_size))
    if "GB" in countries:
        found.extend(hunt_companies_house(profile, secrets.get("companies_house", ""), slice_size))
    if secrets.get("opencorporates"):
        found.extend(hunt_opencorporates(profile, secrets["opencorporates"], slice_size))
    if "hiring" in sources:
        found.extend(discover.hunt_hiring(profile, slice_size, jobs_fn=jobs_fn))
    if "web" in sources:
        found.extend(
            discover.hunt_web(
                profile, max(slice_size, limit - len(found)), search_fn=search_fn, http_get=http_get, cursor=cursor
            )
        )
    if "osm" in sources and len(found) < limit * 2:
        found.extend(discover.hunt_osm(profile, slice_size, http_get=http_get, cursor=cursor))
    if len(found) < limit and secrets.get("apollo"):
        found.extend(hunt_apollo(profile, secrets["apollo"], limit - len(found)))
    if len(found) < limit and secrets.get("places"):
        found.extend(hunt_places(profile, secrets["places"], limit - len(found)))
    if len(found) < limit and secrets.get("crunchbase"):
        found.extend(hunt_crunchbase(profile, secrets["crunchbase"], limit - len(found)))
    clean = [row for row in _dedupe_rows(found) if not is_public_body(str(row.get("company") or ""))]
    # Warm signals first, then rows that already carry a site, then the rest in source order.
    clean.sort(key=lambda row: (0 if row.get("source") == "hiring" else 1, 0 if row.get("domain") else 1))
    return clean[:limit]


def probe_sirene() -> dict[str, Any]:
    """One public ping so the Providers list can show Live / Down."""
    url = "https://recherche-entreprises.api.gouv.fr/search?" + urllib.parse.urlencode(
        {"q": "la poste", "per_page": "1", "page": "1"}
    )
    try:
        payload = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return {"id": "sirene", "live": False, "error": str(exc)[:160]}
    results = payload.get("results") if isinstance(payload, dict) else []
    if isinstance(results, list) and results:
        return {"id": "sirene", "live": True, "error": ""}
    return {"id": "sirene", "live": False, "error": "empty"}
