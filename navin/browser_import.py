# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Import reviewed browser records. No navigation, enrichment or outbound contact."""

from __future__ import annotations

import hashlib
import math
import re
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from filelock import FileLock, Timeout

from navin.career.errors import CareerError

KINDS = ("mission", "job", "candidate", "lead", "tender")
_TEXT = {"title": 240, "description": 12000, "company": 180, "name": 180, "headline": 240,
         "country": 2, "location": 180, "email": 254, "phone": 40, "currency": 3,
         "remote": 20, "need_type": 30, "posted_at": 10, "deadline": 10, "availability": 300}
_MONEY = ("daily_rate_min", "daily_rate_max", "budget_min", "budget_max", "salary_min", "salary_max")
_QUERY_IDS = {"id", "jobid", "job_id", "projectid", "project_id", "missionid", "mission_id",
              "currentjobid", "profileid", "profile_id", "reference", "ref"}


def source_url(raw: Any) -> str:
    try:
        parsed = urlsplit(str(raw or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        if len(str(raw)) > 3000 or parsed.port == 0:
            raise ValueError
        query = urlencode([(key, value) for key, value in parse_qsl(parsed.query)
                           if key.lower() in _QUERY_IDS and len(value) <= 180])
        fragment = ""
        if parsed.fragment.startswith("/"):
            route = urlsplit(parsed.fragment)
            fragment = route.path + ("?" + urlencode([(k, v) for k, v in parse_qsl(route.query)
                         if k.lower() in _QUERY_IDS and len(v) <= 180]) if route.query else "")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", query, fragment))
    except ValueError:
        raise CareerError("L'adresse source doit être une page HTTP(S) valide.") from None


def clean_record(raw: dict[str, Any], *, require_review: bool = True) -> dict[str, Any]:
    from navin.career.sources import html_to_text
    from navin.leads.normalize import normalize_email

    if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
        raise CareerError("Choisissez le type de fiche à importer.")
    if require_review and raw.get("reviewed") is not True:
        raise CareerError("Vérifiez les données avant de les envoyer à Navin.")
    row: dict[str, Any] = {key: html_to_text(str(raw.get(key) or "")).strip()[:limit] for key, limit in _TEXT.items()}
    row.update(kind=raw["kind"], url=source_url(raw.get("url")))
    row["source"] = (urlsplit(row["url"]).hostname or "").removeprefix("www.")
    mode = raw.get("capture_mode")
    if mode not in {"selection", "structured", "page", "manual"}:
        raise CareerError("Le mode de capture est invalide.")
    if (row["source"] == "linkedin.com" or row["source"].endswith(".linkedin.com")) and mode not in {"selection", "manual"}:
        raise CareerError("Sur LinkedIn, importez uniquement le texte sélectionné ou saisi par vous-même.")
    row["capture_mode"] = mode
    for key in ("country", "currency"):
        row[key] = row[key].upper()
        if row[key] and not re.fullmatch(r"[A-Z]{" + str(_TEXT[key]) + "}", row[key]):
            raise CareerError("Utilisez un code pays ISO de deux lettres et une devise de trois lettres.")
    for key in ("posted_at", "deadline"):
        if row[key]:
            try:
                date.fromisoformat(row[key])
            except ValueError:
                raise CareerError("Une date de publication ou d'échéance est invalide.") from None
    if row["remote"] not in {"", "unknown", "remote", "hybrid", "onsite"}:
        raise CareerError("Le mode de travail est invalide.")
    if row["need_type"] not in {"", "client_project", "rfp", "rfq", "rfi", "eoi", "sow"}:
        raise CareerError("Le type de besoin est invalide.")
    if row["email"] and not normalize_email(row["email"]):
        raise CareerError("L'adresse email est invalide.")
    for key in _MONEY:
        value = raw.get(key)
        try:
            amount = None if value is None or value == "" else float(value)
            if isinstance(value, bool) or (amount is not None and (not math.isfinite(amount) or amount <= 0)):
                raise ValueError
            row[key] = amount
        except (TypeError, ValueError):
            raise CareerError("Les montants doivent être positifs ou laissés vides.") from None
    for low, high in zip(_MONEY[::2], _MONEY[1::2]):
        if row[low] is not None and row[high] is not None and row[low] > row[high]:
            raise CareerError("Le minimum ne peut pas dépasser le maximum.")
    skills = raw.get("skills", [])
    if not isinstance(skills, list) or any(not isinstance(skill, str) for skill in skills):
        raise CareerError("Les compétences doivent être une liste de textes.")
    row["skills"] = list(dict.fromkeys(skill.strip()[:80] for skill in skills if skill.strip()))[:60]
    row["website"] = source_url(raw["website"]) if raw.get("website") else ""
    if row["kind"] in {"candidate", "lead"}:
        if not row["name"] and not (row["kind"] == "lead" and row["company"]):
            raise CareerError("Renseignez le nom du profil ou de l'entreprise.")
    elif not row["title"] or len(row["description"]) < 20:
        raise CareerError("Renseignez un titre et une description d'au moins 20 caractères.")
    return row


def import_record(raw: dict[str, Any], device: str, *, roots: dict[str, Path] | None = None) -> dict[str, Any]:
    row = clean_record(raw)
    kind = row.pop("kind")
    roots = roots or {}
    row.update(ingest="browser_extension_reviewed", browser_device=device, observed_at=time.time())
    try:
        if kind in {"mission", "job", "candidate"}:
            from navin.career.store import CareerStore

            store = CareerStore(roots.get("career"))
            with FileLock(str(store.root / "lifecycle.lock"), timeout=0):
                return _career(store, row, kind)
        if kind == "lead":
            from navin.leads.store import LeadsStore

            leads = LeadsStore(roots.get("leads"))
            with FileLock(str(leads.root / "desk.lock"), timeout=0):
                return _lead(leads, row)
        from navin.tenders.normalize import normalize_tender
        from navin.tenders.store import TenderStore

        tenders = TenderStore(roots.get("tenders"))
        with FileLock(str(tenders.root / "lifecycle.lock"), timeout=0):
            tender = normalize_tender({**row, "buyer": row["company"], "publication_date": row["posted_at"],
                                      "budget": row["budget_min"] or row["budget_max"]}, source_id="browser:" + row["source"])
            tender.update(country=row["country"], ingest=row["ingest"], browser_device=device)
            duplicate = any(old["id"] == tender["id"] for old in tenders.load_tenders())
            tenders.upsert_tenders([tender])
            return {"status": "updated" if duplicate else "imported", "module": "tenders", "id": tender["id"]}
    except Timeout:
        raise CareerError("Le module est occupé. Réessayez l'import dans un instant.", status=409) from None


def _career(store: Any, row: dict[str, Any], kind: str) -> dict[str, Any]:
    from navin.career.matching import score_opportunity
    from navin.career.normalize import enrich_facts
    from navin.career.prospecting import _save, _state
    from navin.career.scope import offer_rejection
    from navin.career.sources import stable_job_id

    state = _state(store)
    if kind == "candidate":
        cid = hashlib.sha256(row["url"].encode()).hexdigest()[:20]
        previous = next((item for item in state["candidates"] if item.get("url") == row["url"]), {})
        # A page observation cannot assert consent, interest or verified availability.
        candidate = {"id": previous.get("id", cid), "name": row["name"], "headline": row["headline"],
                     "snippet": row["description"], "skills": row["skills"], "country": row["country"],
                     "city": row["location"], "email": row["email"], "email_source": row["url"] if row["email"] else "",
                     "phone": row["phone"], "url": row["url"], "source": row["source"], "signal": "unknown",
                     "availability_note": row["availability"], "daily_rate": row["daily_rate_min"],
                     "currency": row["currency"], "observed_at": row["observed_at"], "provider": "browser_extension",
                     "browser_device": row["browser_device"]}
        merged = {**previous, **{key: value for key, value in candidate.items() if value not in (None, "", [])}}
        state["candidates"] = [item for item in state["candidates"] if item["id"] != merged["id"]] + [merged]
        _save(store, state)
        return {"status": "updated" if previous else "imported", "module": "career", "id": merged["id"], "kind": kind}
    row.update(track="freelance" if kind == "mission" else "jobs", stack=row.pop("skills"), stage="discovered")
    row["id"] = stable_job_id(row["source"], row["url"], row["title"])
    enrich_facts(row)
    rejection = offer_rejection(row, state["criteria"])
    if rejection:
        return {"status": "excluded", "module": "career", "reason": rejection}
    before = {item["id"] for item in store.load_opportunities()}
    rows = store.upsert_opportunities([score_opportunity(row, store.load_profile())])
    saved = next((item for item in rows if item["id"] == row["id"] or item.get("url") == row["url"]), row)
    return {"status": "updated" if saved["id"] in before else "imported", "module": "career", "id": saved["id"], "kind": kind}


def _lead(store: Any, row: dict[str, Any]) -> dict[str, Any]:
    from navin.leads.normalize import normalize_domain, normalize_email, normalize_phone
    from navin.leads.store import lead_key

    # Source websites are not the prospect's corporate domain.
    if not row["company"]:
        raise CareerError("Renseignez l'entreprise du prospect avant l'import.")
    lead = {"company": row["company"], "person": row["name"], "role": row["headline"],
            "email": normalize_email(row["email"]), "email_status": "unverified" if row["email"] else "unknown",
            "phone": normalize_phone(row["phone"], country=row["country"]), "country": row["country"],
            "domain": normalize_domain(row["website"]), "website": row["website"], "source": row["source"],
            "source_url": row["url"], "confidence": "low", "ingest": row["ingest"], "browser_device": row["browser_device"],
            "linkedin_url": row["url"] if row["source"].endswith("linkedin.com") else "", "notes": row["description"]}
    # Preserve distinct people at the same company when no email/domain is known.
    if not lead["domain"] and lead["person"] and not lead["email"]:
        existing = store.load_leads()
        previous = next((item for item in existing if item.get("source_url") == row["url"]), None)
        lead.update(id=previous["id"] if previous else "ld-" + hashlib.sha256(row["url"].encode()).hexdigest()[:12],
                    stage=(previous or {}).get("stage", "new"), created_at=(previous or {}).get("created_at", time.time()), updated_at=time.time())
        from navin.leads.store import merge_lead

        store.save_leads([item for item in existing if item["id"] != lead["id"]] + [merge_lead(previous, lead) if previous else lead])
        return {"status": "updated" if previous else "imported", "module": "leads", "id": lead["id"]}
    rows, added = store.upsert_leads([lead])
    saved = next(item for item in rows if lead_key(item) == lead_key(lead))
    return {"status": "imported" if added else "updated", "module": "leads", "id": saved["id"]}
