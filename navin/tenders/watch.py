"""Deadline watch for the whole book, pushed as one digest instead of one ping per notice."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from filelock import FileLock

from navin.tenders.desk import in_play
from navin.tenders.normalize import looks_like_notice
from navin.tenders.notify import deliver_alert
from navin.tenders.store import TenderStore
from navin.utils.atomic_io import atomic_write_text

# Tightest first: a notice due in 2 days must fire the 3-day mark, not the 7-day one.
DEADLINE_MARKS = (1, 3, 7)
SUBMITTED_SILENCE_DAYS = 21
# Once the bid is filed the submission deadline is history and the GO is old news.
PRE_SUBMIT_STAGES = frozenset(
    {"discovered", "matched", "scored", "analysed", "go", "drafting", "validating", "clarification"}
)


def _days_left(row: dict[str, Any]) -> int | None:
    deadline = str(row.get("deadline") or "").strip()
    try:
        due = dt.date.fromisoformat(deadline[:10])
    except ValueError:
        raw = (row.get("score_breakdown") or {}).get("days_left")
        return int(raw) if isinstance(raw, (int, float)) else None
    return (due - dt.date.today()).days


def _deadline_mark(days: int | None) -> int | None:
    if days is None or days < 0:
        return None
    for mark in DEADLINE_MARKS:
        if days <= mark:
            return mark
    return None


def _days_since(value: Any) -> int | None:
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None
    return int((dt.datetime.now().timestamp() - stamp) / 86400)


def pending_alerts(store: TenderStore) -> list[dict[str, Any]]:
    """Events the company has not been told about yet. Empty means stay silent."""
    rows = [row for row in store.load_tenders() if looks_like_notice(row)]
    events: list[dict[str, Any]] = []
    for row in rows:
        sent = {str(key) for key in (row.get("alerts_sent") or [])}
        stage = str(row.get("stage") or "")
        tid = str(row.get("id") or "")
        title = str(row.get("title") or "")
        if not tid or not title:
            continue
        if in_play(row) and stage in PRE_SUBMIT_STAGES:
            if stage == "go" and row.get("go") is True and "go" not in sent:
                events.append({"id": tid, "key": "go", "kind": "go", "title": title, "row": row})
            mark = _deadline_mark(_days_left(row))
            if mark is not None and f"d{mark}" not in sent:
                events.append(
                    {
                        "id": tid,
                        "key": f"d{mark}",
                        "kind": "deadline",
                        "days": _days_left(row),
                        "title": title,
                        "row": row,
                    }
                )
        if stage == "submitted" and "relance" not in sent:
            quiet = _days_since(row.get("submitted_at") or row.get("updated_at"))
            if quiet is not None and quiet >= SUBMITTED_SILENCE_DAYS:
                events.append(
                    {
                        "id": tid,
                        "key": "relance",
                        "kind": "relance",
                        "days": quiet,
                        "title": title,
                        "row": row,
                    }
                )
    return events


def _line(event: dict[str, Any], fr: bool) -> str:
    row = event.get("row") or {}
    score = row.get("score")
    tail = f" ({row.get('country') or '?'}, {round(float(score))}/100)" if score is not None else ""
    title = str(event.get("title") or "")[:90]
    if event["kind"] == "go":
        head = "GO"
    elif event["kind"] == "deadline":
        days = event.get("days")
        head = f"J-{days}" if fr else f"D-{days}"
    else:
        head = "Relance" if fr else "Follow up"
    link = str(row.get("source_url") or "")
    return f"{head}: {title}{tail}" + (f"\n{link}" if link.startswith(("https://", "http://")) else "")


def digest_text(events: list[dict[str, Any]], profile: dict[str, Any]) -> tuple[str, str]:
    fr = str(profile.get("locale") or "").lower().startswith("fr")
    company = str(profile.get("name") or "").strip()
    title = (
        f"Navin Tenders - {len(events)} action(s)"
        if not fr
        else f"Navin Tenders - {len(events)} action(s) a traiter"
    )
    if company:
        title = f"{title} - {company}"
    lines = [_line(event, fr) for event in events[:20]]
    if len(events) > 20:
        extra = len(events) - 20
        lines.append(f"... +{extra}")
    tail = (
        "Ouvrez le desk pour valider chaque envoi."
        if fr
        else "Open the desk. You still approve every send."
    )
    return title, "\n".join([*lines, "", tail])


def mark_sent(store: TenderStore, events: list[dict[str, Any]]) -> None:
    by_id: dict[str, set[str]] = {}
    for event in events:
        by_id.setdefault(event["id"], set()).add(event["key"])
    for tid, keys in by_id.items():
        row = store.get(tid)
        sent = {str(key) for key in (row.get("alerts_sent") or [])}
        for key in keys:
            sent.add(key)
            # A tighter deadline mark makes the looser ones pointless.
            if key.startswith("d"):
                try:
                    mark = int(key[1:])
                except ValueError:
                    continue
                sent.update(f"d{other}" for other in DEADLINE_MARKS if other >= mark)
        store.patch(tid, {"alerts_sent": sorted(sent)})


def run_watch(store: TenderStore, *, send: bool = True) -> dict[str, Any]:
    """One pass. Silent when there is nothing new, so it can run on a heartbeat."""
    events = pending_alerts(store)
    payload: dict[str, Any] = {
        "events": [
            {key: value for key, value in event.items() if key != "row"} for event in events
        ],
        "count": len(events),
        "sent": {},
    }
    if not send:
        return payload
    from navin.bus.alerts import resume_alerts

    resume_alerts(store, module="tenders")
    if not events:
        path = store.root / "watch-pending.json"
        if path.exists():
            from navin.bus.alerts import cancel_alert

            with FileLock(str(path) + ".lock", timeout=5):
                batches = json.loads(path.read_text(encoding="utf-8"))
                for batch in batches:
                    cancel_alert(store, module="tenders", event_id=batch["event_id"])
                atomic_write_text(path, "[]", mode=0o600)
        return payload
    profile = store.load_profile()
    path = store.root / "watch-pending.json"
    current_keys = {f"{event['id']}:{event['key']}" for event in events}
    with FileLock(str(path) + ".lock", timeout=5):
        batches = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(batches, list):
            raise ValueError("invalid pending watch batches")
        relevant = []
        for batch in batches:
            if any(f"{event['id']}:{event['key']}" in current_keys for event in batch["events"]):
                relevant.append(batch)
            else:
                from navin.bus.alerts import cancel_alert

                cancel_alert(store, module="tenders", event_id=batch["event_id"])
        batches = relevant
        covered = {f"{event['id']}:{event['key']}" for batch in batches for event in batch["events"]}
        fresh = [event for event in events if f"{event['id']}:{event['key']}" not in covered]
        if fresh:
            title, detail = digest_text(fresh, profile)
            identifiers = sorted(f"{event['id']}:{event['key']}" for event in fresh)
            event_id = "watch:" + hashlib.sha256("\n".join(identifiers).encode()).hexdigest()[:24]
            batches.append({"event_id": event_id, "title": title, "detail": detail,
                            "events": [{key: value for key, value in event.items() if key != "row"} for event in fresh]})
        # Persist before queueing so a crash cannot reshuffle a partially sent digest.
        atomic_write_text(path, json.dumps(batches, ensure_ascii=False, indent=2), mode=0o600)
        waiting, deliveries, digests = [], [], []
        for batch in batches:
            result = deliver_alert(store, title=batch["title"], detail=batch["detail"], level="warn",
                                   event_id=batch["event_id"], event_type="watch_digest")
            deliveries.append(result)
            digests.append(f"{batch['title']}\n\n{batch['detail']}")
            if digest_was_delivered(result):
                mark_sent(store, [event for event in batch["events"] if f"{event['id']}:{event['key']}" in current_keys])
                store.append_journal({"kind": "watch", "event_id": batch["event_id"],
                                      "text": f"{len(batch['events'])} alertes confirmees par les transports actives"})
            else:
                waiting.append(batch)
        atomic_write_text(path, json.dumps(waiting, ensure_ascii=False, indent=2), mode=0o600)
    payload["deliveries"] = deliveries
    payload["sent"] = deliveries[0] if len(deliveries) == 1 else {"complete": not waiting, "batches": deliveries}
    payload["delivered"] = not waiting
    payload["digest"] = "\n\n".join(digests)
    return payload


def digest_was_delivered(sent: dict[str, Any] | None) -> bool:
    """A real receipt must confirm every enabled target before forgetting the event."""
    if not isinstance(sent, dict):
        return False
    if "complete" in sent:
        return sent["complete"] is True
    return any(
        bool(sent.get(name))
        for name in ("webui", "telegram", "whatsapp", "email", "teams", "slack")
    )
