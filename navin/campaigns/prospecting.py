# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Find prospects and enrich contacts from Campaigns, with the Leads desk engine.

The providers and keys are the Leads desk's (Settings live in Leads > Keys):
free sources (French SIRENE registry, open web, hiring signals,
OpenStreetMap) and the user's own keys (Apollo, Hunter, People Data Labs,
Google Places, Crunchbase, OpenCorporates, Companies House). LinkedIn pages
are never fetched. A search is previewed first; nothing enters the book
until the user imports the rows they want.

Without any key a search still returns each company's general contact
(contact@ address, switchboard) read on its own public pages, marked
``contact_kind="general"``. A named decision maker with a direct email
(``contact_kind="person"``) needs an Apollo or Hunter key.
"""

from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from navin.campaigns import store
from navin.campaigns.crm_source import import_people
from navin.campaigns.store import CampaignError

FREE_SOURCES = ("sirene", "web", "hiring", "osm")
PAID_PROVIDERS = ("apollo", "hunter", "pdl", "places", "crunchbase", "opencorporates", "companies_house", "pappers")
MAX_RESULTS = 200
MAX_ENRICH = 200
# Public company pages read at once during a search (general contact, free).
SITE_WORKERS = 4
GENERAL_CONTACT_HINT = (
    "Free sources give each company's general contact (contact@ address, switchboard) from its public "
    "site. A named decision maker needs an Apollo or Hunter key, added in the Leads desk (Keys)."
)


def _secrets() -> dict[str, str]:
    from navin.leads.store import LeadsStore

    try:
        return LeadsStore().load_secrets()
    except Exception:  # noqa: BLE001 - no Leads book yet: free sources only
        return {}


def providers() -> dict[str, Any]:
    keys = _secrets()
    return {
        "free": list(FREE_SOURCES),
        "keys": {name: bool(keys.get(name)) for name in PAID_PROVIDERS},
        "hint": GENERAL_CONTACT_HINT,
        # Free: the company's own contact page, never a named person.
        "can_find_contact": True,
        "can_find_people": bool(keys.get("apollo") or keys.get("hunter")),
        "can_find_email": bool(keys.get("hunter")),
        "can_verify": bool(keys.get("hunter")),
        "can_enrich_company": bool(keys.get("pdl")),
    }


def _person_from(company: dict[str, Any], person: dict[str, Any]) -> dict[str, Any]:
    first = person.get("first_name") or ""
    last = person.get("last_name") or ""
    if not first and person.get("person"):
        first, _, last = str(person["person"]).partition(" ")
    return {
        "company": company.get("company") or "", "domain": company.get("domain") or "",
        "website": company.get("website") or "", "country": company.get("country") or "",
        "sector": company.get("sector") or "", "size": company.get("size") or "",
        "first_name": first, "last_name": last, "role": person.get("role") or "",
        "email": person.get("email") or "", "email_status": person.get("email_status") or "",
        "phone": person.get("phone") or company.get("phone") or "",
        "linkedin_url": person.get("linkedin_url") or company.get("linkedin_url") or "",
        "source": person.get("source") or company.get("source") or "",
        "signal": company.get("signal") or "",
        "contact_kind": "",
    }


def _general_contact(company: dict[str, Any]) -> dict[str, Any]:
    """Free: contact@ address and switchboard from the company's public pages."""
    from navin.leads import scrape_net

    try:
        found = scrape_net.enrich_site(
            str(company.get("website") or company.get("domain") or ""), country=str(company.get("country") or ""),
        )
    except Exception:  # noqa: BLE001 - a dead site must not fail the whole search
        return {}
    return {key: found[key] for key in ("email", "phone", "email_status") if (found or {}).get(key)}


def search(
    criteria: dict[str, Any],
    *,
    hunt: Callable[..., list[dict[str, Any]]] | None = None,
    people: Callable[[str, list[str], dict[str, str]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Companies matching the criteria: a decision maker when keys allow, else the general contact."""
    from navin.leads import waterfall

    keys = _secrets()
    titles = [str(t).strip() for t in criteria.get("titles") or [] if str(t).strip()][:8]
    limit = max(1, min(MAX_RESULTS, int(criteria.get("limit") or 25)))
    find_people = bool(criteria.get("find_people", True))
    find_contact = bool(criteria.get("find_contact", True))
    domains = [waterfall.normalize_domain(str(d)) for d in criteria.get("domains") or [] if str(d).strip()]

    def best_person(domain: str) -> dict[str, Any]:
        if people is not None:
            return people(domain, titles, keys)
        found: dict[str, Any] = {}
        if keys.get("apollo"):
            found = waterfall.enrich_apollo_person(domain, keys["apollo"], titles)
        if not found.get("email") and keys.get("hunter"):
            hunter = waterfall.enrich_hunter_domain(domain, keys["hunter"], titles)
            found = {**hunter, **{k: v for k, v in found.items() if v}} if hunter else found
        return found

    if domains:
        companies = [{"company": d.split(".")[0].title(), "domain": d, "website": f"https://{d}",
                      "source": "domain"} for d in domains[:limit]]
    else:
        sector = str(criteria.get("sector") or criteria.get("keywords") or "").strip()
        if not sector:
            raise CampaignError("Give a sector or keywords, or a list of company domains")
        profile = {
            "icp_name": sector, "sector": sector, "keywords": criteria.get("keywords") or sector,
            "countries": [str(c).upper() for c in criteria.get("countries") or ["FR"]][:6],
            "size_min": int(criteria.get("size_min") or 1), "size_max": int(criteria.get("size_max") or 100000),
            "titles": titles, "cities": criteria.get("cities") or [],
            "sources": [s for s in criteria.get("sources") or ["web", "osm", "hiring"] if s in FREE_SOURCES],
        }
        companies = (hunt or waterfall.hunt_companies)(profile, keys, limit)
    companies = companies[:limit]
    persons = [best_person(c["domain"]) if find_people and c.get("domain") else {} for c in companies]
    # No direct email from Apollo / Hunter (or no key at all): read the company's own pages.
    wanted = [i for i, c in enumerate(companies) if find_contact and c.get("domain") and not persons[i].get("email")]
    general: dict[int, dict[str, Any]] = {}
    if wanted:
        with ThreadPoolExecutor(max_workers=min(SITE_WORKERS, len(wanted))) as pool:
            general = dict(zip(wanted, pool.map(lambda i: _general_contact(companies[i]), wanted)))
    rows: list[dict[str, Any]] = []
    for index, company in enumerate(companies):
        person = persons[index]
        direct = bool(person.get("email"))
        found = dict(general.get(index) or {})
        if company.get("phone"):
            found.pop("phone", None)  # a switchboard from the registry or the map stays
        if found:
            person = {**found, **{k: v for k, v in person.items() if v}}
        row = _person_from(company, person)
        row["contact_kind"] = "person" if direct else ("general" if row["email"] or row["phone"] else "")
        rows.append(row)
    search_id = uuid.uuid4().hex[:16]
    store.set_state(f"prospects:{search_id}", {"at": time.time(), "rows": rows, "criteria": criteria})
    _prune_searches()
    return {
        "search_id": search_id, "rows": rows, "count": len(rows),
        "with_email": sum(1 for r in rows if r["email"]), "with_person": sum(1 for r in rows if r["first_name"]),
        "with_general": sum(1 for r in rows if r["contact_kind"] == "general"),
        "providers": providers(),
    }


def _prune_searches() -> None:
    cutoff = time.time() - 24 * 3600
    with store.connect() as conn:
        for row in conn.execute("SELECT key, value FROM settings WHERE key LIKE 'state:prospects:%'").fetchall():
            try:
                if json.loads(row["value"]).get("at", 0) < cutoff:
                    conn.execute("DELETE FROM settings WHERE key=?", (row["key"],))
            except ValueError:
                conn.execute("DELETE FROM settings WHERE key=?", (row["key"],))


def import_search(search_id: str, *, indexes: list[int] | None = None, only_with_email: bool = False,
                  list_id: str = "", new_list_name: str = "", tags: list[str] | None = None) -> dict[str, Any]:
    saved = store.get_state(f"prospects:{search_id}")
    if not saved:
        raise CampaignError("This search expired; run it again", status=404)
    rows = saved["rows"]
    picked = [rows[i] for i in indexes if 0 <= i < len(rows)] if indexes else rows
    people = []
    for row in picked:
        if only_with_email and not row["email"]:
            continue
        if not row["email"] and not row["phone"]:
            continue
        people.append({
            "email": row["email"], "phone": row["phone"], "first_name": row["first_name"],
            "last_name": row["last_name"], "company": row["company"], "title": row["role"],
            "country": row["country"], "source": f"prospects:{row['source'] or 'search'}",
            "attrs": {k: v for k, v in {"domain": row["domain"], "website": row["website"], "sector": row["sector"],
                                         "size": row["size"], "linkedin": row["linkedin_url"],
                                         "email_status": row["email_status"], "signal": row["signal"],
                                         "contact_kind": row.get("contact_kind", "")}.items() if v},
        })
    if not people:
        raise CampaignError("No row with an email or a phone to import")
    return import_people(people, list_id=list_id, new_list_name=new_list_name, tags=tags)


def hunter_verify(email: str, key: str) -> dict[str, Any]:
    """Hunter email verifier: verified, invalid (undeliverable) or risky (unknown, accept-all...)."""
    import urllib.error
    import urllib.parse

    from navin.leads import waterfall

    if not key or "@" not in (email or ""):
        return {}
    url = "https://api.hunter.io/v2/email-verifier?" + urllib.parse.urlencode({"email": email, "api_key": key})
    try:
        payload = waterfall._get_json(url)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return {}
    data = payload.get("data") if isinstance(payload, dict) else {}
    status = str((data or {}).get("status") or "").lower()
    if status == "valid":
        return {"email_status": "verified"}
    if status in {"invalid", "disposable"}:
        return {"email_status": "invalid"}
    if status:
        return {"email_status": "risky"}
    return {}


def enrich(contact_ids: list[str], *, find_email: bool = True, verify: bool = True, company: bool = True,
           calls: dict[str, Callable[..., dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Fill missing emails, verify existing ones, add company facts, for selected contacts."""
    from navin.leads import waterfall

    keys = _secrets()
    fns = {"finder": waterfall.enrich_hunter_finder, "verify": hunter_verify,
           "company": waterfall.enrich_pdl_company, **(calls or {})}
    if not (keys.get("hunter") or keys.get("pdl")):
        raise CampaignError("Enrichment needs a Hunter or People Data Labs key, added in the Leads desk (Keys).",
                            status=409)
    result = {"processed": 0, "emails_found": 0, "verified": 0, "invalid": 0, "companies": 0, "skipped": 0,
              "errors": []}
    for contact_id in contact_ids[:MAX_ENRICH]:
        try:
            contact = store.get_contact(contact_id)
        except CampaignError:
            result["skipped"] += 1
            continue
        attrs = dict(contact.get("attrs") or {})
        email = contact.get("email") or ""
        domain = waterfall.normalize_domain(attrs.get("domain") or attrs.get("website") or
                                            (email.split("@", 1)[1] if "@" in email else ""))
        patch: dict[str, Any] = {}
        if find_email and not email and keys.get("hunter") and domain and (contact["first_name"] or contact["last_name"]):
            found = fns["finder"](domain, contact["first_name"], contact["last_name"], keys["hunter"])
            if found.get("email"):
                patch["email"] = found["email"]
                attrs["email_status"] = found.get("email_status") or "unverified"
                email = found["email"]
                result["emails_found"] += 1
        # A just-found address already carries the finder's verdict: one credit, not two.
        if verify and email and keys.get("hunter") and "email" not in patch:
            checked = fns["verify"](email, keys["hunter"])
            if checked:
                attrs["email_status"] = checked.get("email_status") or attrs.get("email_status", "")
                if checked.get("email_status") == "verified":
                    result["verified"] += 1
                elif checked.get("email_status") == "invalid":
                    result["invalid"] += 1
        if company and domain and keys.get("pdl") and not attrs.get("sector"):
            facts = fns["company"](domain, keys["pdl"])
            if facts:
                attrs.update({k: v for k, v in {"sector": facts.get("sector"), "size": facts.get("size"),
                                                "linkedin_company": facts.get("linkedin_url")}.items() if v})
                result["companies"] += 1
        if domain and not attrs.get("domain"):
            attrs["domain"] = domain
        attrs["enriched_at"] = store.now_iso()[:10]
        try:
            store.save_contact({"id": contact_id, **patch, "attrs": attrs})
        except CampaignError as exc:
            result["errors"].append(f"{contact.get('email') or contact_id}: {exc.message}")
            continue
        result["processed"] += 1
    return result
