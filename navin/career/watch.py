"""Silent Career watch for heartbeat and loops. No LinkedIn fetch."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from loguru import logger

from navin.career.store import CareerStore

STRONG_SCORE = 80.0
FOLLOW_MARKS = (("j3", 3), ("j7", 7))
OPEN_STAGES = frozenset({"discovered", "matched", "ready"})


def _days_since(value: Any) -> int | None:
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None
    return int((dt.datetime.now().timestamp() - stamp) / 86400)


def pending_alerts(store: CareerStore) -> list[dict[str, Any]]:
    """New Perfect/Good matches and due follow-ups that were not alerted yet."""
    profile = store.load_profile()
    titles = [str(item).strip() for item in (profile.get("titles") or []) if str(item).strip()]
    if not titles and not profile.get("wizard_complete"):
        return []
    events: list[dict[str, Any]] = []
    for row in store.load_opportunities():
        oid = str(row.get("id") or "")
        title = str(row.get("title") or "").strip()
        if not oid or not title or row.get("archived"):
            continue
        sent = {str(key) for key in (row.get("alerts_sent") or [])}
        stage = str(row.get("stage") or "discovered")
        try:
            score = float(row.get("match_score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        if score >= STRONG_SCORE and stage in OPEN_STAGES and "match" not in sent:
            events.append(
                {
                    "id": oid,
                    "key": "match",
                    "kind": "match",
                    "score": round(score, 1),
                    "title": title,
                    "company": str(row.get("company") or ""),
                    "country": str(row.get("country") or ""),
                    "url": str(row.get("url") or ""),
                }
            )
        if stage != "applied":
            continue
        quiet = _days_since(row.get("applied_at") or row.get("updated_at"))
        if quiet is None:
            continue
        for key, days in FOLLOW_MARKS:
            if quiet >= days and key not in sent:
                events.append(
                    {
                        "id": oid,
                        "key": key,
                        "kind": "followup",
                        "days": quiet,
                        "title": title,
                        "company": str(row.get("company") or ""),
                    }
                )
                break
    return events


def digest_text(events: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for event in events[:20]:
        title = str(event.get("title") or "")[:90]
        if event["kind"] == "match":
            country = event.get("country") or "?"
            company = event.get("company") or ""
            tail = f" ({company}, {country}, {event.get('score')})" if company else f" ({country}, {event.get('score')})"
            lines.append(f"Match: {title}{tail}")
        else:
            lines.append(f"Follow-up {event.get('key')}: {title}")
    if len(events) > 20:
        lines.append(f"... +{len(events) - 20}")
    return "\n".join(lines)


def mark_sent(store: CareerStore, events: list[dict[str, Any]]) -> None:
    by_id: dict[str, set[str]] = {}
    for event in events:
        by_id.setdefault(str(event["id"]), set()).add(str(event["key"]))
    for oid, keys in by_id.items():
        try:
            row = store.get_opportunity(oid)
        except Exception:
            continue
        sent = {str(item) for item in (row.get("alerts_sent") or [])}
        sent.update(keys)
        store.update_opportunity(oid, {"alerts_sent": sorted(sent)})


def digest_was_delivered(sent: dict[str, Any] | None) -> bool:
    """New receipts require every enabled channel; preserve old mock contracts."""
    if not isinstance(sent, dict):
        return False
    if "complete" in sent:
        return sent["complete"] is True
    return any(
        bool(sent.get(name))
        for name in ("webui", "telegram", "whatsapp", "email", "teams", "slack")
    )


def run_watch(store: CareerStore, *, already_locked: bool = False) -> dict[str, Any]:
    """One pass over the local book. Silent when nothing new is actionable."""
    if not already_locked:
        from navin.career.lock import career_desk_lock

        with career_desk_lock(store, wait_s=0) as got:
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


def _watch_pass(store: CareerStore) -> dict[str, Any]:
    events = pending_alerts(store)
    payload: dict[str, Any] = {"events": events, "count": len(events), "digest": "", "sent": {}}
    if not events:
        return payload
    payload["digest"] = digest_text(events)
    from navin.career.notify import deliver_alert

    title = f"{len(events)} career alert{'s' if len(events) != 1 else ''}"
    # Keep each pending batch stable while asynchronous channels acknowledge it.
    pending_path = store.root / "watch-pending.json"
    from navin.career.store import _atomic_write, _read_json

    pending = _read_json(pending_path, {})
    if isinstance(pending, dict) and pending.get("events"):
        wanted = {(row["id"], row["key"]) for row in pending["events"]}
        current = [row for row in events if (row["id"], row["key"]) in wanted]
        if current:
            events = current
            payload.update({"events": events, "count": len(events), "digest": digest_text(events)})
    event_id = "career_watch:" + hashlib.sha256(json.dumps(sorted((row["id"], row["key"]) for row in events)).encode()).hexdigest()[:24]
    _atomic_write(pending_path, {"event_id": event_id, "events": events})
    title = f"{len(events)} career alert{'s' if len(events) != 1 else ''}"
    try:
        payload["sent"] = deliver_alert(store, title=title, detail=payload["digest"], event_id=event_id, event_type="career_watch")
    except Exception:
        logger.exception("career watch notify failed")
        payload["sent"] = {}
    if not digest_was_delivered(payload["sent"]):
        payload["delivered"] = False
        return payload
    payload["delivered"] = True
    mark_sent(store, events)
    _atomic_write(pending_path, {})
    store.append_journal({"kind": "watch", "text": f"{len(events)} career alerts"})
    return payload
