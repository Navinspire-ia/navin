# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Career desk snapshots and mutations shared by Studio and the career tool."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from navin.career.ai import polish_pack
from navin.career.collect import collect
from navin.career.dossier import format_agent_status, local_paths, write_local_index
from navin.career.errors import CareerError
from navin.career.export import attach_exports
from navin.career.matching import score_opportunity
from navin.career.sources import (
    INBOX_CLASSES,
    STAGES,
    catalog,
    html_to_text,
    infer_country_iso,
    is_closed_job_url,
    is_linkedin_url,
    is_listing_hit,
    stable_job_id,
)
from navin.career.stack import api_key_flags, module_stack
from navin.career.store import CareerStore, normalize_profile
from navin.career.writer import build_pack


def _loop_payload(store: CareerStore) -> dict[str, Any]:
    from navin.career.loop import peek_loop

    return peek_loop(store)


def _kpis(rows: list[dict[str, Any]], apps: list[dict[str, Any]], inbox: list[dict[str, Any]]) -> dict[str, Any]:
    strong = [row for row in rows if float(row.get("match_score") or 0) >= 80]
    applied = [row for row in rows if row.get("stage") in {"applied", "replied", "interview", "offer", "won"}]
    replies = [row for row in inbox if row.get("classification") not in {None, "", "waiting"}]
    interviews = [row for row in rows if row.get("stage") == "interview"]
    offers = [row for row in rows if row.get("stage") in {"offer", "won"}]
    scores = [float(row.get("match_score") or 0) for row in rows if row.get("match_score") is not None]
    avg = round(sum(scores) / len(scores), 1) if scores else 0
    response_rate = round(100.0 * len(replies) / len(applied), 1) if applied else 0
    return {
        "opportunities": len(rows),
        "strong": len(strong),
        "applications": len(apps) or len(applied),
        "replies": len(replies),
        "interviews": len(interviews),
        "offers": len(offers),
        "avg_score": avg,
        "response_rate": response_rate,
        "headline": (
            f"{len(rows)} opportunities, {len(strong)} strong matches, "
            f"{len(apps) or len(applied)} applications, {len(interviews)} interviews."
        ),
    }


def _ensure_store_files(store: CareerStore, profile: dict[str, Any]) -> None:
    if not store.profile_path.is_file():
        store.save_profile(profile)
    if not store.jobs_path.is_file():
        store.save_opportunities([])
    if not store.apps_path.is_file():
        store.save_applications([])
    if not store.inbox_path.is_file():
        store.save_inbox([])
    if not store.journal_path.is_file():
        store.append_journal({"kind": "index", "text": "career store ready"})


def employers_payload(store: CareerStore, profile: dict[str, Any]) -> dict[str, Any]:
    """Watched ESN / consulting / agency houses for the selected markets, with feed status."""
    from navin.career.employers import directory, employer_summary, user_employers

    markets = [str(iso).upper() for iso in list(profile.get("countries_primary") or []) + list(profile.get("countries_secondary") or [])]
    state = store.load_employer_state()
    hidden = {str(item).lower() for item in (profile.get("employers_hidden") or [])}
    rows: list[dict[str, Any]] = []
    for row in directory(markets or None) + user_employers(profile):
        entry = state.get(row["id"]) if isinstance(state.get(row["id"]), dict) else {}
        rows.append(
            {
                "id": row["id"],
                "name": row["name"],
                "kind": row["kind"],
                "markets": row["markets"],
                "user": bool(row.get("user")),
                "hidden": row["id"] in hidden,
                "ats": str(entry.get("ats") or ("seed" if row.get("seed") else "")),
                "careers_url": str(entry.get("careers_url") or row.get("careers_url") or (f"https://{row['site']}" if row.get("site") else "")),
                "last_count": int(entry.get("last_count") or 0),
                "last_error": str(entry.get("last_error") or entry.get("error") or ""),
                "checked_at": entry.get("checked_at"),
            }
        )
    summary = employer_summary(state, markets or None)
    return {
        "enabled": profile.get("employer_watch", True) is not False,
        "markets": markets,
        "summary": {**summary, "user": sum(1 for row in rows if row["user"]), "hidden": len(hidden)},
        "rows": rows,
    }


def snapshot(store: CareerStore) -> dict[str, Any]:
    from navin.career.mail import load_mail_state, mailbox_status, public_receipt
    from navin.career.retention import apply_retention, retention_days

    apply_retention(store)
    profile = store.load_profile()
    secrets = store.load_secrets()
    public_profile = {**profile, "api_keys": api_key_flags(secrets)}
    _ensure_store_files(store, profile)
    write_local_index(store, profile)
    rows = store.load_opportunities()
    live = [row for row in rows if not row.get("archived")]
    apps = store.load_applications()
    # The durable SMTP receipt survives a later preparation or stale scrape write.
    receipts = load_mail_state(store)["outbox"]
    for collection, id_key in ((rows, "id"), (apps, "opportunity_id")):
        for row in collection:
            receipt = receipts.get(str(row.get(id_key) or ""))
            if receipt:
                row["mail_receipt"] = public_receipt(receipt)
                if receipt.get("status") == "accepted":
                    row["applied_at"] = receipt.get("accepted_at")
                    if row.get("stage") in {"discovered", "matched", "ready"}:
                        row["stage"] = "applied"
    inbox = store.load_inbox()
    archive_after, delete_after = retention_days(profile)
    kpis = _kpis(live, apps, inbox)
    return {
        "profile": public_profile,
        "mailbox_status": mailbox_status(store),
        "opportunities": rows,
        "applications": apps,
        "inbox": inbox,
        "catalog": catalog(),
        "stack": module_stack(secrets),
        "employers": employers_payload(store, profile),
        "kpis": kpis,
        "stages": list(STAGES),
        "tagline": "Search. Match. Tailor. Apply. Track. Follow up.",
        "files": local_paths(store),
        "loop": _loop_payload(store),
        "journal": store.load_journal()[-20:],
        "retention": {
            "archive_after_days": archive_after,
            "delete_after_days": delete_after,
            "favorites": sum(1 for row in live if row.get("favorite")),
            "archived": sum(1 for row in rows if row.get("archived")),
        },
        "book": format_agent_status(
            {
                "profile": public_profile,
                "kpis": kpis,
                "files": local_paths(store),
                "opportunities": rows,
                "applications": apps,
                "inbox": inbox,
            }
        ),
    }


_READ_ALIASES = {
    "cv": "cv",
    "cv.md": "cv",
    "dossier": "dossier",
    "dossier.md": "dossier",
    "index": "index",
    "index.md": "index",
    "book": "book",
    "book.md": "book",
    "profile": "profile",
    "profile.json": "profile",
    "opportunities": "opportunities",
    "opportunities.json": "opportunities",
    "applications": "applications",
    "applications.json": "applications",
    "inbox": "inbox",
    "inbox.json": "inbox",
    "journal": "journal",
    "journal.json": "journal",
    "applications.md": "applications_md",
    "apps.md": "applications_md",
}


def read_local(store: CareerStore, name: str = "book") -> dict[str, Any]:
    """Return one local career file. Always refresh the markdown index first."""
    key = _READ_ALIASES.get(str(name or "book").strip().lower(), "")
    if not key:
        raise CareerError(
            f"unknown career file {name}. Use cv, dossier, index, book, profile, "
            "opportunities, applications, applications.md, inbox, journal."
        )
    write_local_index(store)
    paths = local_paths(store)
    path = Path(paths[key])
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    return {"file": key, "path": str(path), "text": text, "files": paths}


def _first_text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _normalize_ingest_row(row: dict[str, Any], body: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any] | None:
    """Map UI hits and LinkedIn MCP job objects. Never fetches the URL."""
    title = _first_text(row, "title", "job_title", "name", "position")
    url = _first_text(row, "url", "job_url", "link", "linkedin_url", "jobUrl")
    if not title and not url:
        return None
    if is_listing_hit(title, url):
        return None
    via = str(body.get("via") or body.get("source") or "").strip().lower()
    from_mcp = via in {"mcp", "linkedin-mcp", "linkedin_mcp"}
    source = _first_text(row, "source")
    if not source:
        source = "linkedin" if is_linkedin_url(url) or from_mcp else "web-search"
    ingest = _first_text(row, "ingest")
    if not ingest:
        ingest = "linkedin_mcp" if from_mcp or is_linkedin_url(url) else "ui_search"
    location = _first_text(row, "location", "location_name", "formattedLocation")
    description = html_to_text(_first_text(row, "description", "details", "snippet", "text"))[:4000]
    remote = str(row.get("remote") or "").strip().lower()
    if not remote:
        hay = f"{title} {location} {description}".lower()
        if "hybrid" in hay:
            remote = "hybrid"
        elif re.search(r"remote|teletravail|t[eé]l[eé]travail|work from home", hay):
            remote = "remote"
    return {
        "id": str(row.get("id") or stable_job_id(source, url or title, title or "Offer")),
        "source": source,
        "title": title or "Offer",
        "company": _first_text(row, "company", "company_name", "companyName"),
        "location": location,
        "country": infer_country_iso(_first_text(row, "country"), location),
        "description": description,
        "url": url,
        "track": str(row.get("track") or body.get("track") or profile.get("track") or "freelance"),
        "stage": str(row.get("stage") or "discovered"),
        "remote": remote,
        "stack": row.get("stack") if isinstance(row.get("stack"), list) else [],
        "compensation": row.get("compensation"),
        "currency": str(row.get("currency") or ""),
        "ingest": ingest,
        "application_email": _first_text(row, "application_email", "recruiter_email"),
        "application_email_source": "provided" if _first_text(row, "application_email", "recruiter_email") else "",
    }


def ingest_hits(store: CareerStore, body: dict[str, Any]) -> dict[str, Any]:
    """Store offers already listed (UI or LinkedIn MCP). Never fetch those URLs."""
    raw = body.get("jobs") if isinstance(body.get("jobs"), list) else body.get("opportunities")
    if not isinstance(raw, list):
        raw = body.get("hits") if isinstance(body.get("hits"), list) else []
    profile = store.load_profile()
    incoming: list[dict[str, Any]] = []
    for row in raw[:80]:
        if not isinstance(row, dict):
            continue
        job = _normalize_ingest_row(row, body, profile)
        if not job:
            continue
        incoming.append(score_opportunity(job, profile))
    if incoming:
        store.upsert_opportunities(incoming)
        store.append_journal({"kind": "ingest", "text": f"{len(incoming)} hits"})
    snap = snapshot(store)
    snap["ingested"] = len(incoming)
    return snap


def save_profile(store: CareerStore, incoming: dict[str, Any]) -> dict[str, Any]:
    saved = store.save_profile(normalize_profile({**store.load_profile(), **incoming}))
    write_local_index(store, saved)
    store.append_journal({"kind": "profile", "text": "career profile updated"})
    return snapshot(store)


def import_offer(store: CareerStore, body: dict[str, Any]) -> dict[str, Any]:
    """Store an offer the user copied from a closed site. Never fetch that URL."""
    url = str(body.get("url") or "").strip()
    title = str(body.get("title") or "").strip()
    company = str(body.get("company") or "").strip()
    description = html_to_text(str(body.get("description") or body.get("body") or "")).strip()
    if not title:
        for line in description.splitlines():
            if line.strip():
                title = line.strip()
                break
    if not title and not url:
        raise CareerError("title or url is required")
    if body.get("fetch") or body.get("scrape"):
        raise CareerError("Closed boards are never fetched. Paste title and description from the official page.")
    profile = store.load_profile()
    source = "linkedin" if is_linkedin_url(url) else "import"
    row = {
        "id": str(body.get("id") or stable_job_id(source, url or title, title or "Imported offer")),
        "source": source,
        "title": title or "Imported offer",
        "company": company,
        "location": str(body.get("location") or ""),
        "country": infer_country_iso(
            str(body.get("country") or ""),
            str(body.get("location") or title),
        ),
        "description": description[:4000],
        "url": url,
        "track": str(body.get("track") or profile.get("track") or "freelance"),
        "stage": "discovered",
        "remote": "remote" if "remote" in f"{title} {description}".lower() else "",
        "ingest": "open_manual" if is_closed_job_url(url) else "paste",
        "application_email": str(body.get("application_email") or "").strip(),
        "application_email_source": "provided" if body.get("application_email") else "",
    }
    scored = score_opportunity(row, profile)
    store.upsert_opportunities([scored])
    store.append_journal({"kind": "import", "text": f"{source} {title[:80]}"})
    snap = snapshot(store)
    snap["imported"] = scored
    return snap


def run_search(store: CareerStore, body: dict[str, Any]) -> dict[str, Any]:
    extra = body.get("countries")
    countries = [str(item) for item in extra] if isinstance(extra, list) else None
    result = collect(
        store,
        query=str(body.get("query") or body.get("brief") or ""),
        track=str(body.get("track") or ""),
        countries=countries,
    )
    snap = snapshot(store)
    snap["search"] = result
    return snap


def rescore(store: CareerStore) -> dict[str, Any]:
    profile = store.load_profile()
    scored = []
    for row in store.load_opportunities():
        row = dict(row)
        row["description"] = html_to_text(str(row.get("description") or ""))[:4000]
        scored.append(score_opportunity(row, profile))
    store.save_opportunities(scored)
    store.append_journal({"kind": "match", "text": "rescored opportunities"})
    return snapshot(store)


_TRACKED_APP_STAGES = {"applied", "replied", "interview", "offer", "won", "rejected"}


def _sync_application(
    store: CareerStore,
    job: dict[str, Any],
    extra: dict[str, Any],
    *,
    create: bool,
) -> None:
    oid = str(job.get("id") or extra.get("opportunity_id") or "")
    if not oid:
        return
    existing = next((row for row in store.load_applications() if row.get("opportunity_id") == oid), None)
    if not existing and not create:
        return
    payload = {
        **(existing or {}),
        "opportunity_id": oid,
        "title": extra.get("title") or job.get("title") or (existing or {}).get("title") or "",
        "company": extra.get("company") or job.get("company") or (existing or {}).get("company") or "",
        "source": extra.get("source") or job.get("source") or (existing or {}).get("source") or "",
        **extra,
    }
    store.upsert_application(payload)


def set_stage(store: CareerStore, oid: str, stage: str) -> dict[str, Any]:
    if stage not in STAGES:
        raise CareerError(f"unknown stage {stage}")
    patch: dict[str, Any] = {"stage": stage}
    if stage == "applied":
        current = store.get_opportunity(oid)
        if not current.get("applied_at"):
            patch["applied_at"] = time.time()
    job = store.update_opportunity(oid, patch)
    _sync_application(
        store,
        job,
        {
            "stage": stage,
            "pack_ready": bool(job.get("pack_ready")),
            "cv_name": job.get("cv_name") or "",
            "cv_text": job.get("cv_text") or "",
            "cover": job.get("cover") or "",
            "applied_at": job.get("applied_at"),
            "next_action": job.get("next_action")
            or ("Confirm the file was submitted on the employer page." if stage == "applied" else ""),
        },
        create=stage in _TRACKED_APP_STAGES,
    )
    store.append_journal({"kind": "stage", "text": f"{oid} -> {stage}"})
    return snapshot(store)


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    return cleaned[:80] or "Navin_CV"


def prepare_application(store: CareerStore, oid: str) -> dict[str, Any]:
    profile = store.load_profile()
    job = store.get_opportunity(oid)
    pack = attach_exports(store, job, profile, polish_pack(job, profile, build_pack(job, profile)))
    # Both views persist the same canonical document and its generation outcome.
    document = {key: pack.get(key) for key in (
        "cv_name", "cv_text", "cover", "summary", "ats_notes", "ats_score",
        "ats_requirements", "keywords_matched", "keywords_missing", "cv",
        "exports", "language", "generation",
    )}
    document.update({
        "pack_ready": bool(pack.get("pack_ready")),
        "model": pack.get("model") or "",
        "route": pack.get("route") or "",
        "stage": "ready" if pack.get("pack_ready") else "matched",
        "next_action": (
            "Review the tailored CV, then open the original URL to submit."
            if pack.get("pack_ready") else
            "Add your master CV or career facts in Profile, then prepare the CV."
        ),
    })
    if job.get("stage") in {"applied", "replied", "interview", "offer", "won", "rejected"}:
        document["stage"] = job["stage"]
        document["next_action"] = job.get("next_action") or "Follow the existing application."
    app = store.upsert_application(
        {
            **document,
            "opportunity_id": oid,
            "title": job.get("title") or "",
            "company": job.get("company") or "",
            "source": job.get("source") or "",
            "apply_mode": profile.get("apply_mode") or "manual",
        }
    )
    store.update_opportunity(oid, document)
    store.append_journal({"kind": "prepare", "text": pack.get("cv_name")})
    snap = snapshot(store)
    snap["prepared"] = app
    return snap


def apply_one(store: CareerStore, oid: str) -> dict[str, Any]:
    profile = store.load_profile()
    job = store.get_opportunity(oid)
    mode = str(profile.get("apply_mode") or "manual")
    if not job.get("pack_ready") or not job.get("cv_text"):
        prepare_application(store, oid)
        job = store.get_opportunity(oid)
    if not job.get("cv_text") or not job.get("pack_ready"):
        raise CareerError("Paste a master CV in Profile, then prepare the pack for this mission.")
    next_action = "Open the original URL and submit the tailored CV manually. Mark applied after submission."
    next_stage = job["stage"] if job.get("stage") in {"applied", "replied", "interview", "offer", "won", "rejected"} else "ready"
    patch: dict[str, Any] = {
        "stage": next_stage,
        "next_action": next_action,
        "employer_opened": True,
    }
    job = store.update_opportunity(oid, patch)
    store.upsert_application(
        {
            "opportunity_id": oid,
            "title": job.get("title") or "",
            "company": job.get("company") or "",
            "source": job.get("source") or "",
            "stage": next_stage,
            "cv_name": job.get("cv_name"),
            "cv_text": job.get("cv_text"),
            "cover": job.get("cover"),
            "ats_notes": job.get("ats_notes"),
            "pack_ready": True,
            "apply_mode": mode,
            "employer_opened": True,
            "next_action": next_action,
        }
    )
    store.append_journal({"kind": "apply", "text": f"{oid} mode={mode}"})
    return snapshot(store)


def download_pack(store: CareerStore, oid: str, kind: str = "docx") -> dict[str, Any]:
    kind = str(kind or "docx")
    if kind not in {"docx", "cv_docx", "cover_docx"}:
        raise CareerError("Unknown Career document kind")
    job = store.get_opportunity(oid)
    apps = [row for row in store.load_applications() if row.get("opportunity_id") == oid]
    pack = apps[0] if apps else {}
    exports = pack.get("exports") if isinstance(pack.get("exports"), dict) else {}
    record = exports.get(kind) if isinstance(exports.get(kind), dict) else None
    if not record:
        profile = store.load_profile()
        if not pack.get("cv_text"):
            prepare_application(store, oid)
            job = store.get_opportunity(oid)
            apps = [row for row in store.load_applications() if row.get("opportunity_id") == oid]
            pack = apps[0] if apps else {}
        else:
            filled = attach_exports(store, job, profile, pack)
            store.upsert_application({**pack, **filled, "opportunity_id": oid})
            store.update_opportunity(oid, {key: filled.get(key) for key in (
                "cv_name", "cv_text", "cv", "summary", "exports", "generation",
                "language", "pack_ready", "model", "route",
            )})
            pack = filled
        exports = pack.get("exports") if isinstance(pack.get("exports"), dict) else {}
        record = exports.get(kind) if isinstance(exports.get(kind), dict) else None
    if not record or not record.get("file_id"):
        raise CareerError("prepare the tailored CV first")
    payload = store.read_bytes(str(record.get("file_id")))
    payload["kind"] = kind
    payload["mime"] = str(
        record.get("mime")
        or "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return payload


def classify_inbox(store: CareerStore, body: dict[str, Any]) -> dict[str, Any]:
    text = str(body.get("body") or body.get("text") or "").strip()
    if not text:
        raise CareerError("body is required")
    lower = text.lower()
    classification = "waiting"
    if any(token in lower for token in ("interview", "entretien", "call next", "technical interview")):
        classification = "interview"
    elif any(token in lower for token in ("offer", "offre", "package")):
        classification = "offer"
    elif any(token in lower for token in ("reject", "filled", "unfortunately", "refus")):
        classification = "rejected"
    elif any(token in lower for token in ("rate", "tjm", "daily", "salary", "please provide")):
        classification = "need_response"
    elif any(token in lower for token in ("pleased", "interested", "positive", "next step")):
        classification = "positive"
    if body.get("classification") in INBOX_CLASSES:
        classification = str(body["classification"])
    item = store.add_inbox(
        {
            "opportunity_id": str(body.get("opportunity_id") or ""),
            "sender": str(body.get("sender") or "Recruiter"),
            "subject": str(body.get("subject") or text[:80]),
            "body": text,
            "classification": classification,
        }
    )
    oid = str(body.get("opportunity_id") or "")
    if oid:
        stage = {
            "interview": "interview",
            "offer": "offer",
            "rejected": "rejected",
            "positive": "replied",
            "need_response": "replied",
        }.get(classification)
        if stage:
            try:
                job = store.update_opportunity(oid, {"stage": stage})
                _sync_application(store, job, {"stage": stage}, create=False)
            except CareerError:
                pass
    store.append_journal({"kind": "inbox", "text": classification})
    snap = snapshot(store)
    snap["classified"] = item
    return snap


def followup_draft(store: CareerStore, oid: str, wave: str = "j3") -> dict[str, Any]:
    job = store.get_opportunity(oid)
    title = job.get("title") or "the role"
    company = job.get("company") or "your team"
    if wave == "j7":
        body = (
            f"Hello,\n\nI am following up a second time on the {title} conversation with {company}. "
            "Happy to share availability or a clarified rate if useful.\n\nBest regards"
        )
    else:
        body = (
            f"Hello,\n\nI wanted to follow up on the {title} opportunity at {company}. "
            "I remain available and glad to share any extra material.\n\nBest regards"
        )
    store.append_journal({"kind": "followup", "text": f"{oid} {wave}"})
    snap = snapshot(store)
    snap["followup"] = {"opportunity_id": oid, "wave": wave, "body": body}
    return snap


def find_mission(store: CareerStore, brief: str) -> dict[str, Any]:
    text = (brief or "").strip()
    if not text:
        raise CareerError("brief is required")
    profile = store.load_profile()
    if "freelance" in text.lower() or "tjm" in text.lower() or "€" in text or "/day" in text.lower():
        profile["track"] = "freelance"
        profile["engagement"] = "freelance"
    if "cdi" in text.lower() or "full-time" in text.lower() or "permanent" in text.lower():
        profile["track"] = "jobs"
        profile["engagement"] = "permanent"
    rate = re.search(r"(\d{3,4})\s*(?:€|/j|/day|tjm)", text.lower())
    if rate:
        profile["min_rate"] = float(rate.group(1))
    store.save_profile(profile)
    write_local_index(store, store.load_profile())
    result = collect(store, query=text, track=str(profile.get("track") or ""))
    store.append_journal({"kind": "find", "text": text[:160]})
    snap = snapshot(store)
    snap["search"] = result
    return snap
