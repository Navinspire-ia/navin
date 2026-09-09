# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Silent Leads watch for heartbeat and loops. No paid hunt, no LinkedIn scrape, no send."""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from navin.leads.qualify import apply_qualification
from navin.leads.sequence import due_steps
from navin.leads.store import LeadsStore
from navin.leads.waterfall import is_public_body

STRONG_SCORE = 80
OPEN_STAGES = frozenset({"new", "qualified"})
BUYING = frozenset({"funding", "hiring", "news"})


def pending_alerts(store: LeadsStore) -> list[dict[str, Any]]:
    """Strong leads, buying signals and due sequence steps that were not alerted yet."""
    profile = store.load_profile()
    if not profile.get("wizard_ready"):
        return []
    events: list[dict[str, Any]] = []
    for row in store.load_leads():
        lid = str(row.get("id") or "")
        company = str(row.get("company") or "").strip()
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        if not lid or not company or row.get("archived") or extra.get("archived"):
            continue
        if is_public_body(company):
            continue
        sent = {str(key) for key in (row.get("alerts_sent") or [])}
        stage = str(row.get("stage") or "new")
        try:
            score = int(row.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        if score >= STRONG_SCORE and stage in OPEN_STAGES and "strong" not in sent:
            events.append(
                {
                    "id": lid,
                    "key": "strong",
                    "kind": "strong",
                    "score": score,
                    "company": company,
                    "person": str(row.get("person") or ""),
                    "role": str(row.get("role") or ""),
                    "country": str(row.get("country") or ""),
                    "email": str(row.get("email") or ""),
                    "next_action": str(row.get("next_action") or "contact now"),
                }
            )
        for item in row.get("signals") or []:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "")
            if kind not in BUYING:
                continue
            key = f"signal-{kind}"
            if key in sent:
                continue
            events.append(
                {
                    "id": lid,
                    "key": key,
                    "kind": "signal",
                    "signal_kind": kind,
                    "score": score,
                    "company": company,
                    "person": str(row.get("person") or ""),
                    "text": str(item.get("text") or kind),
                }
            )
        for step in due_steps(row):
            key = f"seq-{step.get('n')}"
            if key in sent:
                continue
            events.append(
                {
                    "id": lid,
                    "key": key,
                    "kind": "sequence",
                    "label": str(step.get("label") or f"j{step.get('day')}"),
                    "score": score,
                    "company": company,
                    "person": str(row.get("person") or ""),
                    "channel": str(step.get("channel") or "email"),
                }
            )
    return events


def digest_text(events: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for event in events[:20]:
        company = str(event.get("company") or "")[:90]
        kind = str(event.get("kind") or "strong")
        if kind == "sequence":
            lines.append(f"Follow-up {event.get('label')}: {company}")
            continue
        if kind == "signal":
            lines.append(f"Signal {event.get('signal_kind')}: {company}")
            continue
        person = str(event.get("person") or "")
        role = str(event.get("role") or "")
        who = " · ".join(part for part in (person, role) if part)
        tail = f" ({who}, {event.get('score')})" if who else f" ({event.get('score')})"
        lines.append(f"Lead: {company}{tail}")
    if len(events) > 20:
        lines.append(f"... +{len(events) - 20}")
    return "\n".join(lines)


def mark_sent(store: LeadsStore, events: list[dict[str, Any]]) -> None:
    by_id: dict[str, set[str]] = {}
    for event in events:
        by_id.setdefault(str(event["id"]), set()).add(str(event["key"]))
    for lead_id, keys in by_id.items():
        try:
            row = store.get_lead(lead_id)
        except Exception:
            continue
        sent = {str(item) for item in (row.get("alerts_sent") or [])}
        sent.update(keys)
        store.patch_lead(lead_id, {"alerts_sent": sorted(sent)})


def run_watch(store: LeadsStore, *, already_locked: bool = False) -> dict[str, Any]:
    """Rescore the local book. Silent when nothing new is actionable."""
    if not already_locked:
        from navin.leads.lock import leads_desk_lock

        with leads_desk_lock(store, wait_s=0) as got:
            if not got:
                return {
                    "events": [],
                    "count": 0,
                    "digest": "",
                    "sent": {},
                    "skipped": "busy",
                }
            return _watch_pass(store)
    return _watch_pass(store)


def _watch_pass(store: LeadsStore) -> dict[str, Any]:
    profile = store.load_profile()
    rows = []
    for row in store.load_leads():
        next_row = apply_qualification(row, profile)
        if is_public_body(str(next_row.get("company") or "")):
            next_row["archived"] = True
        rows.append(next_row)
    store.save_leads(rows)
    events = pending_alerts(store)
    payload: dict[str, Any] = {"events": events, "count": len(events), "digest": "", "sent": {}}
    if not events:
        return payload
    payload["digest"] = digest_text(events)
    from navin.leads.notify import deliver_alert

    title = f"{len(events)} lead alert{'s' if len(events) != 1 else ''}"
    try:
        payload["sent"] = deliver_alert(store, title=title, detail=payload["digest"])
    except Exception:
        logger.exception("leads watch notify failed")
        payload["sent"] = {}
    # Always mark. The gateway already appended this digest to the LLM turn.
    # A down bus must not replay the same 80+ lead on every heartbeat.
    mark_sent(store, events)
    try:
        store.save_profile({**profile, "last_watch": time.time()})
    except Exception:
        logger.exception("leads watch could not stamp last_watch")
    store.append_journal({"kind": "watch", "text": f"{len(events)} lead alerts"})
    return payload
