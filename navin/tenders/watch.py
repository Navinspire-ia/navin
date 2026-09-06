"""Deadline watch for the whole book, pushed as one digest instead of one ping per notice."""

from __future__ import annotations

import datetime as dt
from typing import Any

from navin.tenders.desk import in_play
from navin.tenders.normalize import looks_like_notice
from navin.tenders.notify import deliver_alert
from navin.tenders.store import TenderStore

# Tightest first: a notice due in 2 days must fire the 3-day mark, not the 7-day one.
DEADLINE_MARKS = (1, 3, 7)
SUBMITTED_SILENCE_DAYS = 21
# Once the bid is filed the submission deadline is history and the GO is old news.
PRE_SUBMIT_STAGES = frozenset(
    {"discovered", "matched", "scored", "analysed", "go", "drafting", "validating", "clarification"}
)


def _days_left(row: dict[str, Any]) -> int | None:
    raw = (row.get("score_breakdown") or {}).get("days_left")
    if isinstance(raw, (int, float)):
        return int(raw)
    deadline = str(row.get("deadline") or "").strip()
    if not deadline:
        return None
    try:
        due = dt.date.fromisoformat(deadline[:10])
    except ValueError:
        return None
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
    return f"{head}: {title}{tail}"


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
    if not events or not send:
        return payload
    profile = store.load_profile()
    title, detail = digest_text(events, profile)
    payload["sent"] = deliver_alert(store, title=title, detail=detail, level="warn")
    payload["digest"] = f"{title}\n\n{detail}"
    if not digest_was_delivered(payload["sent"]):
        payload["delivered"] = False
        return payload
    payload["delivered"] = True
    mark_sent(store, events)
    store.append_journal({"kind": "watch", "text": f"{len(events)} alerts pushed as one digest"})
    return payload


def digest_was_delivered(sent: dict[str, Any] | None) -> bool:
    """True when at least one company channel accepted the digest."""
    if not isinstance(sent, dict):
        return False
    return any(
        bool(sent.get(name))
        for name in ("webui", "telegram", "whatsapp", "email", "teams", "slack")
    )
