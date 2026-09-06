"""HTTP payloads for the Navin Tenders desk."""

from __future__ import annotations

from typing import Any

from navin.tenders.desk import (
    advance,
    download_pack,
    mail_draft,
    qualify_one,
    rescore_all,
    revise_one,
    run_collect,
    scoring_fingerprint,
    send_mail,
    snapshot,
    write_one,
)
from navin.tenders.errors import TenderError
from navin.tenders.heartbeat import HEARTBEAT_TENDERS_ACTIONS
from navin.tenders.loop import maybe_tick, start_loop, stop_loop, update_loop_schedule
from navin.tenders.store import TenderStore
from navin.loop_schedule import LoopScheduleError

__all__ = ["HEARTBEAT_TENDERS_ACTIONS", "handle_tenders_action", "normalize_tenders_action"]

# Same map the agent tool uses so heartbeat checks and desk writes stay aligned.
_ACTION_ALIASES = {
    "draft": "write",
    "score": "qualify",
    "analyse": "qualify",
    "gonogo": "qualify",
    "follow-up": "follow",
    "crm": "crm-sync",
    "crm_sync": "crm-sync",
    "read-file": "file",
    "read_file": "file",
    "review": "revise",
    "remark": "revise",
    "remarks": "revise",
    "export": "download",
    "pack": "download",
    "add_reference": "add-reference",
    "custom_source": "custom-source",
    "remove_file": "remove-file",
    "star": "favorite",
    "favourite": "favorite",
    "unstar": "unfavorite",
    "unfavourite": "unfavorite",
    "remove-notice": "delete",
    "remove_notice": "delete",
    "purge": "delete",
    "unarchive": "unarchive",
}


def normalize_tenders_action(raw: Any, default: str = "") -> str:
    action = str(raw or default).strip().lower() or default
    return _ACTION_ALIASES.get(action, action)


def _store() -> TenderStore:
    return TenderStore()


def _watch_should_send(value: Any) -> bool:
    """True unless the caller asked for a silent follow (send=false)."""
    if value is False or value == 0:
        return False
    text = str(value or "").strip().lower()
    return text not in {"0", "false", "no", "off"}


def handle_tenders_action(action: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    store = _store()
    act = normalize_tenders_action(action, default="snapshot")
    from navin.agent.tools.context import is_heartbeat_turn

    if is_heartbeat_turn() and act not in HEARTBEAT_TENDERS_ACTIONS:
        raise TenderError(
            "Refused on heartbeat. Tenders silent checks may only run "
            "status/snapshot/get/search/list/index/file/follow/watch. "
            "Collect, write, mail and send stay on the desk or a user chat.",
            status=403,
        )
    if act in {"snapshot", "status"}:
        return snapshot(store)
    if act == "get":
        tid = str(body.get("id") or "").strip()
        if not tid:
            raise TenderError("id is required")
        from navin.tenders.index import local_paths, notice_card

        row = store.get(tid)
        return {"notice": row, "card": notice_card(row), "files": local_paths(store)}
    if act in {"search", "list"}:
        from navin.tenders.index import _as_bool_or_none, local_paths, search_notices

        query = str(body.get("query") or body.get("brief") or "").strip()
        stage = str(body.get("stage") or "").strip()
        country = str(body.get("country") or "").strip()
        go = _as_bool_or_none(body.get("go"))
        favorite = _as_bool_or_none(body.get("favorite"))
        include_raw = body.get("include_archived", True)
        include_archived = _as_bool_or_none(include_raw)
        if include_archived is None:
            include_archived = str(include_raw).strip().lower() not in {"false", "0", "no", "live"}
        limit_raw = body.get("limit")
        if limit_raw in (None, "", "all"):
            limit = 0
        else:
            try:
                limit = int(limit_raw)
            except (TypeError, ValueError):
                limit = 0
        rows = store.load_tenders()
        hits = search_notices(
            rows,
            query,
            stage=stage,
            country=country,
            countries=body.get("countries"),
            sector=str(body.get("sector") or ""),
            source_id=str(body.get("source_id") or body.get("source") or ""),
            buyer=str(body.get("buyer") or ""),
            go=go,
            favorite=favorite,
            include_archived=bool(include_archived),
            deadline_from=str(body.get("deadline_from") or ""),
            deadline_to=str(body.get("deadline_to") or ""),
            published_from=str(body.get("published_from") or ""),
            published_to=str(body.get("published_to") or ""),
            arrived_from=str(body.get("arrived_from") or ""),
            arrived_to=str(body.get("arrived_to") or ""),
            min_score=body.get("min_score"),
            max_score=body.get("max_score"),
            min_budget=body.get("min_budget"),
            max_budget=body.get("max_budget"),
            limit=limit,
        )
        return {
            "query": query,
            "stage": stage,
            "country": country,
            "go": go,
            "count": len(hits),
            "hits": hits,
            "include_archived": bool(include_archived),
            "files": local_paths(store),
        }
    if act == "index":
        from navin.tenders.index import build_index_payload, write_local_index

        files = write_local_index(store)
        payload = build_index_payload(store)
        return {"files": files, "count": payload.get("count") or 0, "index": payload}
    if act == "profile":
        incoming = body.get("profile") if isinstance(body.get("profile"), dict) else body
        before = scoring_fingerprint(store.load_profile())
        saved = store.save_profile(incoming)
        store.append_journal({"kind": "profile", "text": "company profile updated"})
        if scoring_fingerprint(saved) != before:
            count = rescore_all(store)
            if count:
                store.append_journal({"kind": "rescore", "text": f"{count} notices re-scored"})
        return snapshot(store)
    if act == "rescore":
        count = rescore_all(store)
        store.append_journal({"kind": "rescore", "text": f"{count} notices re-scored"})
        return snapshot(store)
    if act == "collect":
        return run_collect(store)
    if act == "start":
        try:
            started = start_loop(
                store,
                schedule=body.get("schedule") if isinstance(body.get("schedule"), dict) else None,
                run_now=bool(body.get("run_now")),
                tz=str(body.get("tz") or "") or None,
            )
        except LoopScheduleError as exc:
            raise TenderError(str(exc), status=400) from exc
        snap = snapshot(store)
        snap["loop_tick"] = started
        return snap
    if act == "schedule":
        raw = body.get("schedule")
        if not isinstance(raw, dict):
            raise TenderError("schedule is required", status=400)
        try:
            update_loop_schedule(store, schedule=raw, tz=str(body.get("tz") or "") or None)
        except LoopScheduleError as exc:
            raise TenderError(str(exc), status=400) from exc
        return snapshot(store)
    if act == "stop":
        stop_loop(store)
        return snapshot(store)
    if act == "tick":
        ticked = maybe_tick(store, force=bool(body.get("force", True)))
        snap = snapshot(store)
        snap["loop_tick"] = ticked
        return snap
    if act in {"qualify", "score", "analyse", "gonogo"}:
        tid = str(body.get("id") or "").strip()
        if not tid:
            raise TenderError("id is required")
        return qualify_one(store, tid)
    if act in {"write", "draft"}:
        tid = str(body.get("id") or "").strip()
        if not tid:
            raise TenderError("id is required")
        return write_one(store, tid)
    if act in {"revise", "review", "remark", "remarks"}:
        tid = str(body.get("id") or "").strip()
        if not tid:
            raise TenderError("id is required")
        return revise_one(
            store,
            tid,
            str(body.get("remarks") or body.get("remark") or body.get("notes") or body.get("text") or ""),
        )
    if act in {"download", "export", "pack"}:
        tid = str(body.get("id") or "").strip()
        if not tid:
            raise TenderError("id is required")
        pack = download_pack(store, tid, str(body.get("kind") or "docx"))
        snap = snapshot(store)
        snap["download"] = pack
        return snap
    if act == "stage":
        tid = str(body.get("id") or "").strip()
        stage = str(body.get("stage") or "").strip()
        if not tid or not stage:
            raise TenderError("id and stage are required")
        return advance(store, tid, stage)
    if act == "mail":
        tid = str(body.get("id") or "").strip()
        kind = str(body.get("kind") or "relance").strip()
        if not tid:
            raise TenderError("id is required")
        return mail_draft(store, tid, kind)
    if act == "send":
        tid = str(body.get("id") or "").strip()
        if not tid:
            raise TenderError("id is required")
        return send_mail(
            store,
            tid,
            to=str(body.get("to") or ""),
            approved=bool(body.get("approved")),
            kind=str(body.get("kind") or ""),
        )
    if act in {"follow", "watch", "follow-up"}:
        from navin.tenders.watch import run_watch

        result = run_watch(store, send=_watch_should_send(body.get("send")))
        snap = snapshot(store)
        snap["watch"] = result
        return snap
    if act in {"crm-sync", "crm_sync", "crm"}:
        from navin.tenders.crm_sync import sync_crm

        ids = body.get("ids") if isinstance(body.get("ids"), list) else None
        if not ids and body.get("id"):
            ids = [str(body.get("id"))]
        result = sync_crm(store, ids, actor=str(body.get("actor") or ""))
        snap = snapshot(store)
        snap["crm"] = result
        return snap
    if act == "discover-accept":
        host = str(body.get("host") or "").strip()
        if not host:
            raise TenderError("host is required")
        store.accept_discovery(host)
        return snapshot(store)
    if act == "notify":
        from navin.tenders.notify import deliver_alert

        title = str(body.get("title") or "Navin Tenders").strip() or "Navin Tenders"
        detail = str(body.get("detail") or "Test from Config. Channels that are on will receive this.").strip()
        sent = deliver_alert(store, title=title, detail=detail, level="info")
        snap = snapshot(store)
        snap["notify"] = sent
        return snap
    if act == "knowledge":
        profile = store.load_profile()
        for key in ("references", "documents", "team", "price_book"):
            if key in body and isinstance(body[key], list):
                profile[key] = body[key]
        for key in ("methodology", "legal_clauses", "name"):
            if key in body:
                profile[key] = body[key]
        store.save_profile(profile)
        return snapshot(store)
    if act == "upload":
        record = store.save_upload(
            kind=str(body.get("kind") or ""),
            name=str(body.get("name") or body.get("filename") or "file"),
            data=str(body.get("data") or body.get("content") or ""),
            meta={
                "title": str(body.get("title") or ""),
                "client": str(body.get("client") or ""),
                "year": str(body.get("year") or ""),
                "country": str(body.get("country") or ""),
                "amount": body.get("amount"),
            },
        )
        store.append_journal({"kind": "upload", "text": record.get("name") or record.get("kind")})
        snap = snapshot(store)
        snap["upload"] = record
        return snap
    if act in {"file", "read-file", "read_file"}:
        record = store.read_upload(str(body.get("id") or body.get("file_id") or ""))
        return {"file": record, **snapshot(store)}
    if act in {"remove-file", "remove_file"}:
        record = store.remove_file(
            file_id=str(body.get("id") or body.get("file_id") or ""),
            title=str(body.get("title") or ""),
        )
        store.append_journal({"kind": "remove-file", "text": record.get("name") or record.get("title") or record.get("file_id")})
        snap = snapshot(store)
        snap["removed"] = record
        return snap
    if act in {"add-reference", "add_reference", "reference"}:
        record = store.add_reference(body)
        store.append_journal({"kind": "reference", "text": record.get("title")})
        snap = snapshot(store)
        snap["reference"] = record
        return snap
    if act in {"custom-source", "custom_source"}:
        if body.get("remove") or str(body.get("op") or "") == "remove":
            store.remove_custom_source(str(body.get("id") or ""))
            store.append_journal({"kind": "custom-source", "text": "removed"})
            return snapshot(store)
        item = store.add_custom_source(body)
        store.append_journal({"kind": "custom-source", "text": item.get("name") or item.get("id")})
        snap = snapshot(store)
        snap["custom_source"] = item
        return snap
    if act == "secret":
        store.save_secret(str(body.get("name") or ""), str(body.get("value") or ""))
        store.append_journal({"kind": "secret", "text": str(body.get("name") or "key")})
        return snapshot(store)
    if act in {"favorite", "unfavorite"}:
        from navin.tenders.retention import set_favorite

        set_favorite(store, str(body.get("id") or ""), favorite=act == "favorite")
        return snapshot(store)
    if act == "archive":
        from navin.tenders.retention import set_archived

        set_archived(store, str(body.get("id") or ""), archived=True)
        return snapshot(store)
    if act == "unarchive":
        from navin.tenders.retention import set_archived

        set_archived(store, str(body.get("id") or ""), archived=False)
        return snapshot(store)
    if act == "delete":
        from navin.tenders.retention import delete_notice

        removed = delete_notice(store, str(body.get("id") or ""))
        snap = snapshot(store)
        snap["deleted"] = {"id": removed.get("id"), "title": removed.get("title")}
        return snap
    raise TenderError(f"unknown tenders action {action}", status=400)
