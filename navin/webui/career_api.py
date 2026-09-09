# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""HTTP payloads for the Navin Career desk (Freelance + Jobs)."""

from __future__ import annotations

from typing import Any

from navin.career.desk import (
    apply_one,
    classify_inbox,
    download_pack,
    find_mission,
    followup_draft,
    import_offer,
    ingest_hits,
    prepare_application,
    read_local,
    rescore,
    run_search,
    save_profile,
    set_stage,
    snapshot,
)
from navin.career.errors import CareerError
from navin.career.heartbeat import HEARTBEAT_CAREER_ACTIONS
from navin.career.loop import maybe_tick, start_loop, stop_loop, update_loop_schedule
from navin.career.store import CareerStore
from navin.loop_schedule import LoopScheduleError


def _store() -> CareerStore:
    return CareerStore()


def handle_career_action(action: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    store = _store()
    act = (action or "snapshot").strip().lower()
    from navin.agent.tools.context import is_heartbeat_turn

    if is_heartbeat_turn() and act not in HEARTBEAT_CAREER_ACTIONS:
        raise CareerError(
            "Refused on heartbeat. Career silent checks may only run "
            "status/dossier/snapshot/read/book/watch. "
            "Collect, search and apply stay on the desk or a user chat."
        )
    if act in {"snapshot", "status"}:
        return snapshot(store)
    if act in {"mail_config", "mail_test", "mail_draft", "send_email", "sync_mail"}:
        from navin.career.mail import configure_mailbox, mail_draft, send_application, test_mailbox
        from navin.career.mailbox import sync_mailbox

        if act == "mail_config":
            result = configure_mailbox(store, body)
            key = "mailbox_status"
        elif act == "mail_test":
            result, key = test_mailbox(store), "mail_checks"
        elif act == "sync_mail":
            result, key = sync_mailbox(store), "mail_sync"
        else:
            oid = str(body.get("id") or "").strip()
            if not oid:
                raise CareerError("id is required")
            if act == "mail_draft":
                result = mail_draft(store, oid, recipient=str(body.get("recipient") or ""))
                key = "mail_draft"
            else:
                result = send_application(
                    store, oid, recipient=str(body.get("recipient") or ""),
                    revision=str(body.get("revision") or ""), reviewed=body.get("reviewed") is True,
                    retry=body.get("retry") is True,
                )
                key = "mail_receipt"
        return {**snapshot(store), key: result}
    if act in {"read", "book"}:
        return read_local(store, str(body.get("file") or body.get("id") or "book"))
    if act == "profile":
        incoming = body.get("profile") if isinstance(body.get("profile"), dict) else body
        return save_profile(store, incoming)
    if act == "secret":
        store.save_secret(str(body.get("name") or ""), str(body.get("value") or ""))
        store.append_journal({"kind": "secret", "text": str(body.get("name") or "key")})
        return snapshot(store)
    if act in {"import", "paste"}:
        return import_offer(store, body)
    if act in {"ingest", "hits"}:
        return ingest_hits(store, body)
    if act in {"search", "collect"}:
        return run_search(store, body)
    if act in {"match", "rescore"}:
        return rescore(store)
    if act == "stage":
        oid = str(body.get("id") or "").strip()
        stage = str(body.get("stage") or "").strip()
        if not oid or not stage:
            raise CareerError("id and stage are required")
        return set_stage(store, oid, stage)
    if act in {"prepare", "cv", "write", "tailor"}:
        oid = str(body.get("id") or "").strip()
        if not oid:
            raise CareerError("id is required")
        return prepare_application(store, oid)
    if act in {"download", "export"}:
        oid = str(body.get("id") or "").strip()
        if not oid:
            raise CareerError("id is required")
        pack = download_pack(store, oid, str(body.get("kind") or "docx"))
        snap = snapshot(store)
        snap["download"] = pack
        return snap
    if act == "apply":
        oid = str(body.get("id") or "").strip()
        if not oid:
            raise CareerError("id is required")
        return apply_one(store, oid)
    if act in {"inbox", "classify"}:
        return classify_inbox(store, body)
    if act == "followup":
        oid = str(body.get("id") or "").strip()
        if not oid:
            raise CareerError("id is required")
        return followup_draft(store, oid, str(body.get("wave") or "j3"))
    if act in {"find", "mission"}:
        return find_mission(store, str(body.get("brief") or body.get("query") or ""))
    if act == "watch":
        from navin.career.watch import run_watch

        result = run_watch(store)
        snap = snapshot(store)
        snap["watch"] = result
        return snap
    if act == "start":
        try:
            started = start_loop(
                store,
                schedule=body.get("schedule") if isinstance(body.get("schedule"), dict) else None,
                run_now=bool(body.get("run_now")),
                tz=str(body.get("tz") or "") or None,
            )
        except LoopScheduleError as exc:
            raise CareerError(str(exc), status=400) from exc
        snap = snapshot(store)
        snap["loop_tick"] = started
        return snap
    if act == "schedule":
        raw = body.get("schedule")
        if not isinstance(raw, dict):
            raise CareerError("schedule is required", status=400)
        try:
            update_loop_schedule(store, schedule=raw, tz=str(body.get("tz") or "") or None)
        except LoopScheduleError as exc:
            raise CareerError(str(exc), status=400) from exc
        return snapshot(store)
    if act == "stop":
        stop_loop(store)
        return snapshot(store)
    if act == "tick":
        ticked = maybe_tick(store, force=bool(body.get("force", True)))
        snap = snapshot(store)
        snap["loop_tick"] = ticked
        return snap
    if act in {"favorite", "unfavorite"}:
        from navin.career.retention import set_favorite

        set_favorite(store, str(body.get("id") or ""), favorite=act == "favorite")
        return snapshot(store)
    if act == "archive":
        from navin.career.retention import set_archived

        set_archived(store, str(body.get("id") or ""), archived=True)
        return snapshot(store)
    if act == "unarchive":
        from navin.career.retention import set_archived

        set_archived(store, str(body.get("id") or ""), archived=False)
        return snapshot(store)
    if act == "delete":
        from navin.career.retention import delete_offer

        removed = delete_offer(store, str(body.get("id") or ""))
        snap = snapshot(store)
        snap["deleted"] = {"id": removed.get("id"), "title": removed.get("title")}
        return snap
    raise CareerError(f"unknown career action {action}", status=400)
