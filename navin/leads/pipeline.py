"""The leads chain as one function per stage, shared by desk, loop and CLI.

``run_hunt``       discover (rotating) -> budgeted fill-ins -> enrich -> qualify -> upsert
``run_sequences``  draft due steps, send them when the profile is autonomous (daily cap,
                   opt-out list, channel readiness), or leave them ready for a human
``auto_enroll``    put fresh A-tier rows with an inbox into the sequence

Everything takes injectable functions so the whole chain runs offline in tests.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from loguru import logger

from navin.leads.discover import bulk_enrich
from navin.leads.draft import draft_sequence
from navin.leads.qualify import apply_qualification
from navin.leads.sequence import due_steps, mark_step_sent, start_sequence
from navin.leads.store import LeadsStore
from navin.leads.waterfall import enrich_row, hunt_companies, is_public_body

SendFn = Callable[..., bool]
ENROLL_PER_CYCLE = 10


def _now() -> float:
    return time.time()


def _is_live(row: dict[str, Any]) -> bool:
    return not row.get("archived") and str(row.get("stage") or "") not in {"lost", "won"}


def run_hunt(
    store: LeadsStore,
    *,
    profile: dict[str, Any] | None = None,
    limit: int | None = None,
    source: str = "manual",
    search_fn: Any = None,
    http_get: Any = None,
    jobs_fn: Any = None,
    fetch_fn: Any = None,
    resolver: Any = None,
    rotate: bool = True,
    hunt_fn: Any = None,
    fill_fn: Any = None,
    enrich_fn: Any = None,
) -> dict[str, Any]:
    """One full pass. Returns counters; rows are already in the store.

    ``hunt_fn`` / ``fill_fn`` / ``enrich_fn`` default to the waterfall; callers pass
    their own module-level names so tests can patch them where they are used.
    """
    icp = profile if isinstance(profile, dict) else store.load_profile()
    secrets = store.load_secrets()
    wanted = int(limit or icp.get("count") or 50)
    cursor = store.load_cursor()
    hunt = hunt_fn or hunt_companies
    fill = fill_fn or bulk_enrich
    enrich = enrich_fn or enrich_row
    found = hunt(
        icp, secrets, wanted, cursor=cursor, search_fn=search_fn, http_get=http_get, jobs_fn=jobs_fn
    )
    spent = (
        fill(found, icp, search_fn=search_fn, fetch_fn=fetch_fn, resolver=resolver) if found else {}
    )
    enriched: list[dict[str, Any]] = []
    skipped_optout = 0
    for row in found:
        if store.is_opted_out(row):
            skipped_optout += 1
            continue
        try:
            enriched.append(enrich(row, icp, secrets))
        except Exception as exc:  # one bad provider answer must not lose the batch
            logger.warning("leads enrich_row failed for {}: {}", row.get("company"), exc)
            enriched.append(apply_qualification(row, icp))
    rows, added = store.upsert_leads(enriched) if enriched else (store.load_leads(), 0)
    if rotate and found:
        store.bump_cursor()
    by_source: dict[str, int] = {}
    for row in found:
        key = str(row.get("source") or "unknown")
        by_source[key] = by_source.get(key, 0) + 1
    store.append_journal(
        {
            "kind": "hunt",
            "added": added,
            "count": len(found),
            "source": source,
            "cursor": cursor,
            "by_source": by_source,
            "fill": spent,
            "optout": skipped_optout,
        }
    )
    return {
        "found": len(found),
        "added": added,
        "kept": len(rows),
        "scanned": len(found),
        "cursor": cursor,
        "by_source": by_source,
        "fill": spent,
        "optout": skipped_optout,
    }


def auto_enroll(
    store: LeadsStore,
    *,
    profile: dict[str, Any] | None = None,
    now: float | None = None,
    cap: int = ENROLL_PER_CYCLE,
) -> int:
    """Autonomous mode only: A-tier, live, with an email, not yet in a sequence."""
    icp = profile if isinstance(profile, dict) else store.load_profile()
    if str(icp.get("execution_mode") or "approval") != "autonomous":
        return 0
    stamp = now if now is not None else _now()
    rows = store.load_leads()
    enrolled = 0
    for row in rows:
        if enrolled >= cap:
            break
        if not _is_live(row) or row.get("sequence") or str(row.get("tier") or "") != "A":
            continue
        if not str(row.get("email") or "").strip() or row.get("email_status") == "guessed":
            continue
        if store.is_opted_out(row) or is_public_body(str(row.get("company") or "")):
            continue
        row["sequence"] = start_sequence(row, now=stamp)
        row["stage"] = "qualified" if row.get("stage") == "new" else row.get("stage")
        enrolled += 1
    if enrolled:
        store.save_leads(rows)
        store.append_journal({"kind": "sequence", "auto": True, "count": enrolled})
    return enrolled


def run_sequences(
    store: LeadsStore,
    *,
    profile: dict[str, Any] | None = None,
    now: float | None = None,
    send_fn: SendFn | None = None,
    ready_fn: Callable[[], dict[str, Any]] | None = None,
    ask_fn: Any = None,
) -> dict[str, Any]:
    """Draft every due step; send them only in autonomous mode within the daily cap."""
    icp = profile if isinstance(profile, dict) else store.load_profile()
    stamp = now if now is not None else _now()
    autonomous = str(icp.get("execution_mode") or "approval") == "autonomous"
    cap = int(icp.get("daily_send_cap") or 0)
    rows = store.load_leads()
    drafted = sent = ready = stopped = 0
    blocked_reason = ""
    email_ready: bool | None = None
    changed = False
    for index, row in enumerate(rows):
        if not _is_live(row) or not isinstance(row.get("sequence"), dict):
            continue
        if store.is_opted_out(row):
            seq = {**row["sequence"], "stopped": "opt-out", "stopped_at": stamp}
            rows[index] = {
                **row,
                "sequence": seq,
                "stage": "lost" if row.get("stage") != "won" else row.get("stage"),
            }
            stopped += 1
            changed = True
            continue
        due = due_steps(row, now=stamp)
        if not due:
            continue
        step = due[0]
        next_row = row
        if not (isinstance(step.get("draft"), dict) and step["draft"].get("body")):
            next_row = draft_sequence(row, icp, ask_fn=ask_fn)
            drafted += 1
            changed = True
        current = next(
            (item for item in next_row["sequence"]["steps"] if item.get("n") == step.get("n")), step
        )
        draft = current.get("draft") if isinstance(current.get("draft"), dict) else {}
        dest = str(next_row.get("email") or "").strip()
        can_send = autonomous and bool(dest) and next_row.get("email_status") != "guessed"
        if can_send and cap and store.sends_today(now=stamp) >= cap:
            can_send = False
            blocked_reason = blocked_reason or "daily cap reached"
        if can_send and email_ready is None:
            if ready_fn is not None:
                email_ready = bool((ready_fn().get("email") or {}).get("ready"))
            else:
                from navin.leads.notify import channel_readiness

                email_ready = bool((channel_readiness().get("email") or {}).get("ready"))
            if not email_ready:
                blocked_reason = blocked_reason or "email channel not connected"
        if can_send and email_ready:
            sender = send_fn
            if sender is None:
                from navin.leads.notify import send_channel

                sender = send_channel
            ok = False
            try:
                ok = bool(
                    sender(
                        "email",
                        dest,
                        subject=str(draft.get("subject") or next_row.get("company") or "Lead"),
                        body=str(draft.get("body") or ""),
                    )
                )
            except Exception as exc:
                logger.warning(
                    "leads sequence send failed for {}: {}", next_row.get("company"), exc
                )
            if ok:
                next_row = mark_step_sent(next_row, channel="email", now=stamp)
                next_row["stage"] = "contacted"
                store.record_send(now=stamp)
                store.append_journal(
                    {
                        "kind": "outreach",
                        "id": next_row.get("id"),
                        "channel": "email",
                        "to": dest,
                        "step": step.get("n"),
                        "auto": True,
                    }
                )
                sent += 1
                changed = True
            else:
                ready += 1
        else:
            ready += 1
        rows[index] = next_row
    if changed:
        store.save_leads(rows)
    return {
        "drafted": drafted,
        "sent": sent,
        "ready": ready,
        "stopped": stopped,
        "blocked": blocked_reason,
        "autonomous": autonomous,
    }
