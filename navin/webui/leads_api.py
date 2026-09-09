# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""HTTP payloads for the Navin Leads desk."""

from __future__ import annotations

from typing import Any

from navin.leads.desk import (
    delete_lead,
    draft,
    enrich,
    export_csv,
    hunt,
    lookalike,
    optout,
    outreach,
    push_crm,
    rescore,
    save_keys,
    save_profile,
    sequence_start,
    sequences_run,
    set_stage,
    snapshot,
    watch,
)
from navin.leads.errors import LeadsError
from navin.leads.heartbeat import HEARTBEAT_LEADS_ACTIONS
from navin.leads.loop import maybe_tick, start_loop, stop_loop, update_loop_schedule
from navin.leads.store import LeadsStore
from navin.loop_schedule import LoopScheduleError


def _store() -> LeadsStore:
    return LeadsStore()


def handle_leads_action(action: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    store = _store()
    act = (action or "snapshot").strip().lower()
    from navin.agent.tools.context import is_heartbeat_turn

    if is_heartbeat_turn() and act not in HEARTBEAT_LEADS_ACTIONS:
        raise LeadsError(
            "Refused on heartbeat. Leads silent checks may only run "
            "status/snapshot/watch/follow/rescore. Hunt, start, schedule, tick, "
            "enrich, sequence, lookalike, keys, CRM and outreach stay on the desk.",
            status=400,
        )
    if act in {"snapshot", "status"}:
        return snapshot(store)
    if act == "probe":
        return snapshot(store, probe=True)
    if act in {"profile", "setup"}:
        incoming = body.get("profile") if isinstance(body.get("profile"), dict) else body
        return save_profile(store, incoming)
    if act in {"keys", "secrets"}:
        incoming = body.get("keys") if isinstance(body.get("keys"), dict) else body
        return save_keys(store, incoming)
    if act in {"hunt", "search", "discover"}:
        return hunt(store, body)
    if act == "enrich":
        lead_id = str(body.get("id") or "").strip()
        if not lead_id:
            raise LeadsError("id is required")
        return enrich(store, lead_id)
    if act == "start":
        try:
            started = start_loop(
                store,
                schedule=body.get("schedule") if isinstance(body.get("schedule"), dict) else None,
                run_now=bool(body.get("run_now")),
                tz=str(body.get("tz") or "") or None,
            )
        except LoopScheduleError as exc:
            raise LeadsError(str(exc), status=400) from exc
        snap = snapshot(store)
        snap["loop_tick"] = started
        return snap
    if act == "schedule":
        raw = body.get("schedule")
        if not isinstance(raw, dict):
            raise LeadsError("schedule is required", status=400)
        try:
            update_loop_schedule(store, schedule=raw, tz=str(body.get("tz") or "") or None)
        except LoopScheduleError as exc:
            raise LeadsError(str(exc), status=400) from exc
        return snapshot(store)
    if act == "stop":
        stop_loop(store)
        return snapshot(store)
    if act == "tick":
        ticked = maybe_tick(store, force=bool(body.get("force", True)))
        snap = snapshot(store)
        snap["loop_tick"] = ticked
        return snap
    if act in {"watch", "follow"}:
        return watch(store)
    if act in {"rescore", "score"}:
        return rescore(store)
    if act in {"sequence", "cadence"}:
        lead_id = str(body.get("id") or "").strip()
        if not lead_id:
            raise LeadsError("id is required")
        return sequence_start(store, lead_id)
    if act in {"draft", "redraft"}:
        lead_id = str(body.get("id") or "").strip()
        if not lead_id:
            raise LeadsError("id is required")
        try:
            step = int(body.get("step") or 0)
        except (TypeError, ValueError):
            step = 0
        return draft(
            store, lead_id, step=step, refresh=act == "redraft" or bool(body.get("refresh"))
        )
    if act in {"sequences", "run-sequences", "send-due"}:
        return sequences_run(store)
    if act in {"optout", "opt-out", "unsubscribe"}:
        value = str(body.get("value") or body.get("email") or body.get("domain") or "").strip()
        if not value:
            raise LeadsError("value (email or domain) is required")
        return optout(store, value)
    if act in {"export", "csv"}:
        csv_text = export_csv(
            store, tier=str(body.get("tier") or ""), stage=str(body.get("stage") or "")
        )
        return {
            "csv": csv_text,
            "rows": max(0, csv_text.count("\n") - 1),
            "filename": "navin-leads.csv",
        }
    if act in {"lookalike", "peers"}:
        lead_id = str(body.get("id") or "").strip()
        if not lead_id:
            raise LeadsError("id is required")
        return lookalike(store, lead_id)
    if act == "stage":
        lead_id = str(body.get("id") or "").strip()
        stage = str(body.get("stage") or "").strip()
        if not lead_id or not stage:
            raise LeadsError("id and stage are required")
        return set_stage(store, lead_id, stage)
    if act in {"delete", "remove"}:
        lead_id = str(body.get("id") or "").strip()
        if not lead_id:
            raise LeadsError("id is required")
        return delete_lead(store, lead_id)
    if act in {"crm", "push"}:
        lead_id = str(body.get("id") or "").strip()
        if not lead_id:
            raise LeadsError("id is required")
        project = body.get("project") or body.get("project_path")
        return push_crm(store, lead_id, project=str(project or "").strip() or None)
    if act in {"outreach", "send", "message"}:
        lead_id = str(body.get("id") or "").strip()
        if not lead_id:
            raise LeadsError("id is required")
        raw_send = body.get("send")
        send = raw_send is True or str(raw_send or "").strip().lower() in {"1", "true", "yes"}
        return outreach(
            store,
            lead_id,
            channel=str(body.get("channel") or "email"),
            to=str(body.get("to") or ""),
            subject=str(body.get("subject") or ""),
            body=str(body.get("body") or ""),
            send=send,
        )
    raise LeadsError(f"unknown leads action {action}", status=400)
