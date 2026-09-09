"""Desk snapshot, KPIs, collect+score+analyse pipeline."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from loguru import logger

from navin.tenders.ai import (
    polish_mail,
    polish_response,
    qualify_note,
    revise_response,
    routing_snapshot,
)
from navin.tenders.collect import collect
from navin.tenders.enrich import enrich_notice
from navin.tenders.errors import TenderError
from navin.tenders.export import attach_exports
from navin.tenders.needs import public_needs_catalog
from navin.tenders.normalize import STAGES, looks_like_notice
from navin.tenders.profile import group_catalog_by_zone, public_profile, wizard_ready
from navin.tenders.score import score_tender
from navin.tenders.sources import catalog, web_search_queries
from navin.tenders.stack import module_stack
from navin.tenders.store import TenderStore
from navin.tenders.writer import analyse_tender, build_response, commercial_draft, go_nogo

WORKING_STAGES = frozenset(
    {"drafting", "validating", "submitted", "clarification", "shortlisted", "negotiation"}
)
CLOSED_STAGES = frozenset({"won", "lost"})


def in_play(row: dict[str, Any]) -> bool:
    """Same rule as the desk pipeline. Once you draft, your call beats the reco."""
    if row.get("archived"):
        return False
    stage = str(row.get("stage") or "")
    if stage in CLOSED_STAGES or stage == "no-go":
        return False
    if stage in WORKING_STAGES:
        return True
    return row.get("go") is not False


def _kpis(tenders: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    min_score = float(profile.get("min_score") or 70)
    open_rows = [t for t in tenders if in_play(t)]
    qualified = [t for t in open_rows if float(t.get("score") or 0) >= min_score]
    deadline_week = []
    for row in open_rows:
        days = (row.get("score_breakdown") or {}).get("days_left")
        if days is not None and 0 <= int(days) < 7:
            deadline_week.append(row)
    value = sum(float(t["budget"]) for t in qualified if isinstance(t.get("budget"), (int, float)))
    weighted = sum(
        float(t["budget"]) * (float(t.get("score") or 0) / 100.0)
        for t in qualified
        if isinstance(t.get("budget"), (int, float))
    )
    by_stage: dict[str, int] = {stage: 0 for stage in STAGES}
    for row in tenders:
        stage = str(row.get("stage") or "discovered")
        by_stage[stage] = by_stage.get(stage, 0) + 1
    won = by_stage.get("won") or 0
    lost = by_stage.get("lost") or 0
    decided = won + lost
    win_rate = (100.0 * won / decided) if decided else None
    country_hits: dict[str, dict[str, float]] = {}
    for row in tenders:
        iso = str(row.get("country") or "INTL")
        bucket = country_hits.setdefault(iso, {"count": 0, "won": 0, "value": 0})
        bucket["count"] += 1
        if row.get("stage") == "won":
            bucket["won"] += 1
        if isinstance(row.get("budget"), (int, float)):
            bucket["value"] += float(row["budget"])
    return {
        "open": len(open_rows),
        "new": sum(1 for t in tenders if t.get("stage") == "discovered"),
        "qualified": len(qualified),
        "value": round(value, 2),
        "weighted_value": round(weighted, 2),
        "deadline_7d": len(deadline_week),
        "drafting": by_stage.get("drafting") or 0,
        "submitted": by_stage.get("submitted") or 0,
        "won": won,
        "lost": lost,
        "win_rate": win_rate,
        "by_stage": by_stage,
        "countries": country_hits,
        "headline": (
            f"You have {len(open_rows)} open notices"
            + (f" representing {value:,.0f} {profile.get('currency') or 'EUR'}" if value else "")
            + f", but only {len(qualified)} meet the {min_score:.0f} bar. "
            + (f"Weighted pipeline: {weighted:,.0f}." if weighted else "Budget is unpublished on most notices.")
        ),
    }


def _loop_payload(store: TenderStore) -> dict[str, Any]:
    from navin.tenders.loop import peek_loop

    return peek_loop(store)


def snapshot(store: TenderStore | None = None) -> dict[str, Any]:
    store = store or TenderStore()
    from navin.tenders.retention import apply_retention, retention_days

    apply_retention(store)
    profile = store.load_profile()
    tenders = [row for row in store.load_tenders() if looks_like_notice(row)]
    live = [row for row in tenders if not row.get("archived")]
    from navin.bus.alerts import delivery_snapshot
    from navin.tenders.index import format_agent_status, local_paths, write_local_index
    from navin.tenders.notify import channel_readiness

    catalog_rows = catalog()
    secrets_map = store.load_secrets()
    custom_keys = {
        key.split(":", 1)[1]
        for key in secrets_map
        if str(key).startswith("custom:") and ":" in str(key)
    }
    try:
        files = write_local_index(store, profile)
    except OSError:
        files = local_paths(store)
    public = public_profile(
        profile,
        has_sam_key=bool(store.get_secret("sam_gov") or secrets_map.get("sam_gov")),
        custom_keys=custom_keys,
    )
    kpis = _kpis(live, profile)
    archive_after, delete_after = retention_days(profile)
    book = format_agent_status(
        {
            "profile": public,
            "kpis": kpis,
            "files": files or local_paths(store),
            "tenders": tenders,
            "wizard_ready": wizard_ready(profile),
            "journal": store.load_journal()[-40:],
            "loop": _loop_payload(store),
        },
        store,
    )
    return {
        "profile": public,
        "wizard_ready": wizard_ready(profile),
        "tenders": tenders,
        "kpis": kpis,
        "sources": store.sources(),
        "catalog": catalog_rows,
        "catalog_by_zone": group_catalog_by_zone(catalog_rows),
        "queries": web_search_queries(
            countries=list(profile.get("countries") or []),
            crafts=list(profile.get("crafts") or []),
            tender_types=list(profile.get("tender_types") or []),
            project_types=list(profile.get("project_types") or []),
        ),
        "needs_catalog": public_needs_catalog(),
        "discoveries": store.load_discoveries(),
        "journal": store.load_journal()[-40:],
        "files": files,
        "book": book,
        "stages": list(STAGES),
        "send_modes": ["draft", "approval", "autonomous"],
        "channels": channel_readiness(),
        "alert_deliveries": delivery_snapshot(store, module="tenders"),
        "models": routing_snapshot(profile),
        "follow_up": _follow_up(store),
        "loop": _loop_payload(store),
        "stack": module_stack(),
        "retention": {
            "archive_after_days": archive_after,
            "delete_after_days": delete_after,
            "favorites": sum(1 for row in live if row.get("favorite")),
            "archived": sum(1 for row in tenders if row.get("archived")),
        },
    }


def _follow_up(store: TenderStore) -> dict[str, Any]:
    """What the watch would push right now, without pushing it."""
    from navin.tenders.watch import pending_alerts

    try:
        events = pending_alerts(store)
    except Exception:
        return {"pending": 0, "events": []}
    return {
        "pending": len(events),
        "events": [
            {key: value for key, value in event.items() if key != "row"} for event in events[:20]
        ],
    }


# Autonomous send mode drafts the reply for the best new GO notices of a
# cycle. Capped so one noisy portal cannot burn a whole model budget.
AUTO_DRAFT_MAX = 3


def _auto_draft(store: TenderStore, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Draft the reply for the top new GO notices. Sending stays a human click."""
    ranked = sorted(
        (row for row in rows if row.get("go") and row.get("id")),
        key=lambda r: (int(r.get("score") or 0), int(r.get("go_pct") or 0)),
        reverse=True,
    )
    drafted: list[dict[str, Any]] = []
    for row in ranked[:AUTO_DRAFT_MAX]:
        tender_id = str(row["id"])
        try:
            write_one(store, tender_id)
        except Exception as exc:  # noqa: BLE001 - one bad notice must not stop the cycle
            logger.warning("Tenders auto-draft failed for {}: {}", tender_id, exc)
            store.append_journal(
                {"kind": "write", "id": tender_id, "text": f"auto-draft failed - {exc}"}
            )
            continue
        drafted.append({"id": tender_id, "title": row.get("title") or tender_id, "score": row.get("score")})
    if drafted:
        store.append_journal(
            {
                "kind": "loop",
                "text": f"autonomous mode: {len(drafted)} reply drafted, waiting for your review",
                "ids": [item["id"] for item in drafted],
            }
        )
    return drafted


def run_collect(store: TenderStore | None = None) -> dict[str, Any]:
    store = store or TenderStore()
    profile = store.load_profile()
    try:
        collect_days = int(profile.get("collect_days") or 30)
    except (TypeError, ValueError):
        collect_days = 30
    result = collect(
        countries=list(profile.get("countries") or []),
        extra_enabled=list(profile.get("enabled_sources") or []),
        crafts=list(profile.get("crafts") or []),
        tender_types=list(profile.get("tender_types") or []),
        project_types=list(profile.get("project_types") or []),
        source_ids=list(profile.get("source_ids") or []),
        custom_sources=list(profile.get("custom_sources") or []),
        sam_key=store.get_secret("sam_gov"),
        collect_days=collect_days,
    )
    incoming = result["tenders"]
    known_ids = {str(row.get("id")) for row in store.load_tenders() if row.get("id")}
    for row in incoming:
        scored = score_tender(row, profile)
        row["score"] = scored["score"]
        row["score_breakdown"] = scored["breakdown"]
        row["stage"] = "scored"
        row["analysis"] = analyse_tender(row, profile)
        row["stage"] = "analysed"
        decision = go_nogo(row, profile)
        row["go"] = decision["go"]
        row["go_reason"] = decision["reason"]
        row["go_pct"] = decision["go_pct"]
        row["effort_days"] = decision["effort_days"]
        row["stage"] = "go" if decision["go"] else "no-go"
        row["matched"] = True
    store.upsert_tenders(incoming)
    if result.get("discovered_sources"):
        store.merge_discoveries(result["discovered_sources"])
    fresh = [row for row in incoming if str(row.get("id")) not in known_ids]
    store.append_journal(
        {
            "kind": "collect",
            "text": f"{len(incoming)} notices ingested, {len(fresh)} new",
            "reports": result.get("reports"),
            "brief": result.get("brief"),
        }
    )
    drafted: list[dict[str, Any]] = []
    if fresh and str(profile.get("send_mode") or "approval") == "autonomous":
        drafted = _auto_draft(store, fresh)
    snap = snapshot(store)
    snap["collect"] = result["reports"]
    snap["brief"] = result.get("brief")
    snap["new"] = len(fresh)
    snap["auto_drafted"] = drafted
    if fresh:
        from navin.tenders.notify import deliver_alert

        go_n = sum(1 for row in incoming if row.get("go"))
        auto = f" {len(drafted)} reply drafted for review." if drafted else ""
        snap["notify"] = deliver_alert(
            store,
            title="Navin Tenders",
            detail=(
                f"{len(incoming)} notices ingested, {len(fresh)} new. {go_n} GO.{auto} "
                f"{snap['kpis'].get('deadline_7d') or 0} deadline under 7 days. "
                "Open #/tenders."
            ),
            level="warning" if (snap["kpis"].get("deadline_7d") or 0) else "info",
            event_id="new_notices:" + hashlib.sha256("\n".join(sorted(str(row["id"]) for row in fresh)).encode()).hexdigest()[:24],
            event_type="new_notices",
        )
        from navin.bus.alerts import delivery_snapshot

        snap["alert_deliveries"] = delivery_snapshot(store, module="tenders")
    return snap


SCORING_KEYS = (
    "crafts",
    "tender_types",
    "project_types",
    "countries",
    "min_budget",
    "max_budget",
    "min_deadline_days",
    "min_score",
    "turnover",
    "references",
)


def scoring_fingerprint(profile: dict[str, Any]) -> str:
    return repr([profile.get(key) for key in SCORING_KEYS])


def rescore_all(store: TenderStore) -> int:
    """Re-score the whole book against the current company file."""
    profile = store.load_profile()
    rows = store.load_tenders()
    for row in rows:
        scored = score_tender(row, profile)
        updates = {
            "score": scored["score"],
            "score_breakdown": scored["breakdown"],
            "analysis": analyse_tender(row, profile),
        }
        decision = go_nogo({**row, **updates}, profile)
        stage = str(row.get("stage") or "discovered")
        if stage not in WORKING_STAGES and stage not in CLOSED_STAGES:
            updates["stage"] = "go" if decision["go"] else "no-go"
        updates.update(
            {
                "go": decision["go"],
                "go_reason": decision["reason"],
                "go_pct": decision["go_pct"],
                "effort_days": decision["effort_days"],
            }
        )
        store.patch(str(row.get("id")), updates)
    return len(rows)


def qualify_one(store: TenderStore, tender_id: str) -> dict[str, Any]:
    profile = store.load_profile()
    row = store.get(tender_id)
    scored = score_tender(row, profile)
    updates = {
        "score": scored["score"],
        "score_breakdown": scored["breakdown"],
        "analysis": analyse_tender(row, profile),
        "stage": "analysed",
    }
    decision = go_nogo({**row, **updates}, profile)
    updates.update(
        {
            "go": decision["go"],
            "go_reason": decision["reason"],
            "go_pct": decision["go_pct"],
            "effort_days": decision["effort_days"],
            "stage": "go" if decision["go"] else "no-go",
            # The rules own the verdict; the model only puts it in words.
            "go_note": qualify_note({**row, **updates}, profile, decision),
        }
    )
    store.patch(tender_id, updates)
    store.append_journal({"kind": "qualify", "id": tender_id, "text": decision["reason"]})
    return snapshot(store)


def write_one(store: TenderStore, tender_id: str) -> dict[str, Any]:
    profile = store.load_profile()
    row = enrich_notice(store.get(tender_id))
    row["analysis"] = analyse_tender(row, profile)
    response = polish_response(row, profile, build_response(row, profile))
    response = attach_exports(store, row, profile, response)
    store.patch(
        tender_id,
        {
            "response": response,
            "stage": "drafting",
            "analysis": row["analysis"],
            "description": row.get("description"),
            "cdc_text": row.get("cdc_text"),
            "enriched": bool(row.get("enriched")),
        },
    )
    model = str(response.get("model") or "")
    pack = " + pack" if response.get("pack_ready") else ""
    store.append_journal(
        {
            "kind": "write",
            "id": tender_id,
            "text": f"response drafted with {model}{pack}" if model else f"response drafted{pack}",
        }
    )
    return snapshot(store)


def revise_one(store: TenderStore, tender_id: str, remarks: str) -> dict[str, Any]:
    profile = store.load_profile()
    row = store.get(tender_id)
    current = row.get("response") if isinstance(row.get("response"), dict) else None
    notes = str(remarks or "").strip()
    if not current:
        raise TenderError("write the reply before requesting changes")
    if not notes:
        raise TenderError("remarks are required")
    response = attach_exports(store, row, profile, revise_response(row, profile, current, notes))
    reviews = list(row.get("response_reviews") or [])
    reviews.append({"t": time.time(), "remarks": notes, "model": response.get("model") or ""})
    store.patch(
        tender_id,
        {"response": response, "response_reviews": reviews, "stage": "validating"},
    )
    store.append_journal({"kind": "revise", "id": tender_id, "text": notes[:240]})
    return snapshot(store)


def download_pack(store: TenderStore, tender_id: str, kind: str = "docx") -> dict[str, Any]:
    row = store.get(tender_id)
    response = row.get("response") if isinstance(row.get("response"), dict) else {}
    exports = response.get("exports") if isinstance(response.get("exports"), dict) else {}
    key = str(kind or "docx").strip().lower()
    if key in {"word", "doc"}:
        key = "docx"
    if key in {"ppt", "powerpoint", "deck"}:
        key = "pptx"
    record = exports.get(key) if isinstance(exports.get(key), dict) else None
    if not record:
        profile = store.load_profile()
        response = attach_exports(store, row, profile, response or {})
        store.patch(tender_id, {"response": response})
        exports = response.get("exports") if isinstance(response.get("exports"), dict) else {}
        record = exports.get(key) if isinstance(exports.get(key), dict) else None
    if not record or not record.get("file_id"):
        raise TenderError("no generated pack yet - write the reply first")
    payload = store.read_bytes(str(record.get("file_id")))
    payload["kind"] = key
    payload["mime"] = str(
        record.get("mime")
        or (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if key == "docx"
            else "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
    )
    return payload


def advance(store: TenderStore, tender_id: str, stage: str) -> dict[str, Any]:
    if stage not in STAGES:
        raise TenderError(f"unknown stage {stage}")
    updates: dict[str, Any] = {"stage": stage}
    if stage == "go":
        updates["go"] = True
    elif stage == "no-go":
        updates["go"] = False
    if stage == "submitted":
        # The follow-up watch needs to know how long the buyer has been silent.
        updates["submitted_at"] = time.time()
    store.patch(tender_id, updates)
    store.append_journal({"kind": "stage", "id": tender_id, "text": stage})
    return snapshot(store)


def mail_draft(store: TenderStore, tender_id: str, kind: str) -> dict[str, Any]:
    row = store.get(tender_id)
    profile = store.load_profile()
    draft = polish_mail(row, profile, kind, commercial_draft(row, kind, profile))
    mail = list(row.get("mail") or [])
    mail.append({"kind": kind, "body": draft, "t": time.time()})
    store.patch(tender_id, {"mail": mail, "stage": "clarification" if kind == "clarification" else row.get("stage")})
    from navin.tenders.notify import deliver_alert

    title = f"Tenders {kind}: {row.get('title') or tender_id}"
    sent = deliver_alert(store, title=title, detail=draft, level="info")
    return {"draft": draft, "notify": sent, **snapshot(store)}


def mail_subject(row: dict[str, Any], body: str) -> str:
    first = (body or "").strip().splitlines()[0] if (body or "").strip() else ""
    low = first.lower()
    if low.startswith("objet :") or low.startswith("objet:") or low.startswith("subject:"):
        return first.split(":", 1)[1].strip()[:200]
    return str(row.get("title") or "Tender")[:200]


def send_mail(
    store: TenderStore,
    tender_id: str,
    *,
    to: str,
    approved: bool = False,
    kind: str = "",
) -> dict[str, Any]:
    """Real send. Approval mode needs an explicit click, never an agent decision."""
    row = store.get(tender_id)
    profile = store.load_profile()
    mode = str(profile.get("send_mode") or "approval")
    if mode == "draft":
        raise TenderError(
            "this desk is in draft mode. Switch to Approval in Settings to send.",
            status=409,
        )
    if mode == "approval" and not approved:
        raise TenderError("approval is required before this mail leaves the desk", status=409)
    dest = str(to or "").strip()
    if "@" not in dest:
        raise TenderError("a valid recipient email is required", status=400)
    mail = list(row.get("mail") or [])
    body = ""
    if kind:
        for item in reversed(mail):
            if str(item.get("kind")) == kind:
                body = str(item.get("body") or "")
                break
    if not body and mail:
        body = str(mail[-1].get("body") or "")
    if not body:
        raise TenderError("draft the mail before sending it", status=409)
    subject = mail_subject(row, body)
    sent_via = "smtp"
    try:
        from navin.crm.outreach import outreach
        from navin.tenders.crm_sync import project_root

        outreach(
            project_root(),
            channel="email",
            to=dest,
            subject=subject,
            body=body,
            send=True,
            opportunity_id=str(row.get("crm_opportunity_id") or ""),
            actor="tenders",
        )
        sent_via = "crm"
    except TenderError:
        # No project folder: still send, just without the CRM activity trail.
        from navin.crm.outreach import _send_email

        _send_email(dest, subject, body)
    mail.append(
        {
            "kind": kind or "sent",
            "body": body,
            "to": dest,
            "subject": subject,
            "sent": True,
            "via": sent_via,
            "t": time.time(),
        }
    )
    store.patch(tender_id, {"mail": mail, "last_sent_at": time.time()})
    store.append_journal({"kind": "send", "id": tender_id, "text": f"mail sent to {dest}"})
    snap = snapshot(store)
    snap["send"] = {"to": dest, "subject": subject, "via": sent_via}
    return snap
