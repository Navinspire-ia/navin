# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Target a project's Navin CRM: contacts, leads, opportunities, companies.

The CRM lives per project (``<project>/.navin/crm``). A preview counts who the
filters reach and lists the facet values to filter on; an import copies those
people into the Campaigns book (deduplicated by email/phone) with their CRM
facts as attributes, so a message can say ``{{attrs.stage}}``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.campaigns import store
from navin.campaigns.store import CampaignError

CRM_KINDS = ("contacts", "leads", "opportunities", "companies")


def _records(project: Path, kind: str) -> list[dict[str, Any]]:
    from navin.crm.store import CrmError, list_records

    try:
        return list_records(project, kind)
    except CrmError as exc:
        raise CampaignError(f"CRM: {exc.message}", status=exc.status) from exc


def _match(record: dict[str, Any], filters: dict[str, Any], fields: dict[str, str]) -> bool:
    for key, field in fields.items():
        wanted = [str(v).lower() for v in filters.get(key) or [] if str(v).strip()]
        if not wanted:
            continue
        value = record.get(field)
        values = [str(v).lower() for v in value] if isinstance(value, list) else [str(value or "").lower()]
        if not set(values) & set(wanted):
            return False
    q = str(filters.get("q") or "").strip().lower()
    if q and q not in " ".join(str(v) for v in record.values() if isinstance(v, str)).lower():
        return False
    return True


def _person(record: dict[str, Any], kind: str, *, company: str = "", extra: dict[str, str] | None = None) -> dict[str, Any]:
    if kind == "leads":
        first, _, last = (record.get("name") or "").partition(" ")
        company = company or record.get("company") or ""
    else:
        first, last = record.get("firstName") or "", record.get("lastName") or ""
    phone = record.get("whatsapp") or record.get("phone") or ""
    attrs = {"crm_kind": kind, "crm_id": str(record.get("id") or ""), "company": company,
             "owner": str(record.get("owner") or ""), **(extra or {})}
    return {
        "email": record.get("email") or "", "phone": phone, "first_name": first, "last_name": last,
        "company": company, "title": record.get("title") or "", "country": record.get("country") or "",
        "tags": record.get("tags") or [], "attrs": {k: v for k, v in attrs.items() if v},
        "source": f"crm:{kind}",
    }


def audience(project: Path, kinds: list[str], filters: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """People reached by the filters (deduplicated) and the facet values seen."""
    kinds = [k for k in kinds if k in CRM_KINDS] or ["contacts"]
    contacts = _records(project, "contacts")
    companies = {c["id"]: c for c in _records(project, "companies")}
    by_id = {c["id"]: c for c in contacts}
    facets: dict[str, set[str]] = {k: set() for k in
                                   ("status", "stage", "owner", "tags", "source", "country", "industry")}
    people: list[dict[str, Any]] = []

    def company_name(record: dict[str, Any]) -> str:
        return str((companies.get(record.get("companyId") or "") or {}).get("name") or "")

    def note(record: dict[str, Any], *keys: str) -> None:
        for key in keys:
            value = record.get(key)
            for item in value if isinstance(value, list) else [value]:
                if item:
                    facets[{"stage": "stage", "industry": "industry"}.get(key, key)].add(str(item))

    if "contacts" in kinds:
        for record in contacts:
            note(record, "status", "owner", "tags", "source", "country")
            if _match(record, filters, {"status": "status", "owner": "owner", "tags": "tags",
                                        "source": "source", "country": "country"}):
                people.append(_person(record, "contacts", company=company_name(record)))
    if "leads" in kinds:
        floor = int(filters.get("score_min") or 0)
        for record in _records(project, "leads"):
            note(record, "status", "owner", "source", "country")
            if int(record.get("score") or 0) >= floor and _match(
                record, filters, {"status": "status", "owner": "owner", "source": "source", "country": "country"},
            ):
                people.append(_person(record, "leads", extra={"score": str(record.get("score") or "")}))
    if "opportunities" in kinds:
        floor = float(filters.get("amount_min") or 0)
        for record in _records(project, "opportunities"):
            note(record, "stage", "owner", "source")
            if float(record.get("amount") or 0) < floor or not _match(
                record, filters, {"stage": "stage", "owner": "owner", "source": "source"},
            ):
                continue
            for cid in record.get("contactIds") or []:
                contact = by_id.get(cid)
                if contact:
                    people.append(_person(contact, "opportunities", company=company_name(record) or company_name(contact),
                                          extra={"stage": record.get("stage") or "", "opportunity": record.get("name") or "",
                                                 "amount": str(record.get("amount") or "")}))
    if "companies" in kinds:
        for company in companies.values():
            note(company, "industry", "country", "tags", "owner")
            if not _match(company, filters, {"industry": "industry", "country": "country", "tags": "tags",
                                             "owner": "owner"}):
                continue
            for contact in contacts:
                if contact.get("companyId") == company["id"]:
                    people.append(_person(contact, "companies", company=company.get("name") or ""))

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for person in people:
        key = (person["email"] or "").lower() or store.normalize_phone(person["phone"])
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(person)
    return unique, {k: sorted(v) for k, v in facets.items()}


def preview(project: Path, kinds: list[str], filters: dict[str, Any]) -> dict[str, Any]:
    people, facets = audience(project, kinds, filters)
    return {
        "count": len(people),
        "with_email": sum(1 for p in people if p["email"]),
        "with_phone": sum(1 for p in people if p["phone"]),
        "sample": [{"name": f"{p['first_name']} {p['last_name']}".strip(), "email": p["email"],
                    "phone": p["phone"], "kind": p["attrs"].get("crm_kind", ""), "company": p["company"]}
                   for p in people[:12]],
        "facets": facets,
        "project": project.name,
    }


def import_people(people: list[dict[str, Any]], *, list_id: str = "", new_list_name: str = "",
                  tags: list[str] | None = None) -> dict[str, Any]:
    """Upsert people into the Campaigns book and an optional list."""
    if new_list_name.strip():
        list_id = store.save_list({"name": new_list_name})["id"]
    elif list_id:
        store.get_list(list_id)
    created = updated = skipped = 0
    errors: list[str] = []
    ids: list[str] = []
    for person in people:
        existing = store.find_contact(person.get("email", ""), person.get("phone", ""))
        body = dict(person)
        if tags:
            body["tags"] = sorted({*(person.get("tags") or []), *tags})
        if existing:
            body = {**body, "id": existing["id"],
                    "attrs": {**(existing.get("attrs") or {}), **(person.get("attrs") or {})}}
            if existing.get("status") != "subscribed":
                body.pop("status", None)
        try:
            saved = store.save_contact(body)
        except CampaignError as exc:
            skipped += 1
            if len(errors) < 20:
                errors.append(exc.message)
            continue
        ids.append(saved["id"])
        if existing:
            updated += 1
        else:
            created += 1
    if list_id and ids:
        store.add_to_list(list_id, ids)
    return {"created": created, "updated": updated, "skipped": skipped, "errors": errors, "list_id": list_id}
