"""Archive after 45 days, delete after 60. Same clock on every OS."""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

from navin.tenders.errors import TenderError
from navin.tenders.store import TenderStore

ARCHIVE_AFTER_DAYS = 45
DELETE_AFTER_DAYS = 60
_DAY_S = 86400.0


def retention_days(profile: dict[str, Any] | None) -> tuple[int, int]:
    """(archive_after, delete_after). Defaults 45 / 60. Delete is never earlier than archive."""
    raw = profile if isinstance(profile, dict) else {}
    archive = _positive_days(raw.get("archive_after_days"), ARCHIVE_AFTER_DAYS)
    delete = _positive_days(raw.get("delete_after_days"), DELETE_AFTER_DAYS)
    if delete < archive:
        delete = archive
    return archive, delete


def notice_origin_ts(row: dict[str, Any]) -> float:
    """When the notice entered the book. Missing stamps count as now (age 0)."""
    for key in ("fetched_at", "created_at"):
        stamp = _as_ts(row.get(key))
        if stamp:
            return stamp
    published = _date_ts(row.get("publication_date"))
    if published:
        return published
    stamp = _as_ts(row.get("updated_at"))
    if stamp:
        return stamp
    return time.time()


def notice_age_days(row: dict[str, Any], *, now: float | None = None) -> float:
    clock = now if now is not None else time.time()
    return max(0.0, (clock - notice_origin_ts(row)) / _DAY_S)


def is_archived(row: dict[str, Any] | None) -> bool:
    return bool(isinstance(row, dict) and row.get("archived"))


def is_favorite(row: dict[str, Any] | None) -> bool:
    return bool(isinstance(row, dict) and row.get("favorite") and not row.get("archived"))


def apply_retention(store: TenderStore, *, now: float | None = None) -> dict[str, int]:
    """Move stale notices to archive, then drop those past the delete age."""
    clock = now if now is not None else time.time()
    archive_after, delete_after = retention_days(store.load_profile())
    rows = store.load_tenders()
    kept: list[dict[str, Any]] = []
    archived = 0
    deleted = 0
    changed = False
    for row in rows:
        age = notice_age_days(row, now=clock)
        if age >= delete_after:
            deleted += 1
            changed = True
            continue
        if not row.get("archived") and age >= archive_after:
            row = dict(row)
            row["archived"] = True
            row["archived_at"] = float(row.get("archived_at") or clock)
            row["updated_at"] = clock
            archived += 1
            changed = True
        kept.append(row)
    if changed:
        store.save_tenders(kept)
        store.append_journal(
            {
                "kind": "retention",
                "text": f"archived {archived}, deleted {deleted} (archive {archive_after}d / delete {delete_after}d)",
            }
        )
    return {
        "archived": archived,
        "deleted": deleted,
        "archive_after_days": archive_after,
        "delete_after_days": delete_after,
    }


def set_favorite(store: TenderStore, tender_id: str, favorite: bool) -> dict[str, Any]:
    tid = str(tender_id or "").strip()
    if not tid:
        raise TenderError("id is required")
    row = store.patch(tid, {"favorite": bool(favorite)})
    store.append_journal(
        {"kind": "favorite" if favorite else "unfavorite", "text": str(row.get("title") or tid)}
    )
    return row


def set_archived(store: TenderStore, tender_id: str, archived: bool) -> dict[str, Any]:
    tid = str(tender_id or "").strip()
    if not tid:
        raise TenderError("id is required")
    updates: dict[str, Any] = {"archived": bool(archived)}
    updates["archived_at"] = time.time() if archived else None
    row = store.patch(tid, updates)
    store.append_journal(
        {"kind": "archive" if archived else "unarchive", "text": str(row.get("title") or tid)}
    )
    return row


def delete_notice(store: TenderStore, tender_id: str) -> dict[str, Any]:
    tid = str(tender_id or "").strip()
    if not tid:
        raise TenderError("id is required")
    rows = store.load_tenders()
    removed: dict[str, Any] | None = None
    kept: list[dict[str, Any]] = []
    for row in rows:
        if row.get("id") == tid and removed is None:
            removed = dict(row)
            continue
        kept.append(row)
    if removed is None:
        raise TenderError(f"tender {tid} not found", status=404)
    store.save_tenders(kept)
    store.append_journal({"kind": "delete", "text": str(removed.get("title") or tid)})
    return removed


def _positive_days(value: Any, default: int) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        return default
    if days <= 0:
        return default
    return min(days, 3650)


def _as_ts(value: Any) -> float:
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return 0.0
    return stamp if stamp > 0 else 0.0


def _date_ts(value: Any) -> float:
    text = str(value or "").strip()[:10]
    if len(text) < 10:
        return 0.0
    try:
        return dt.datetime.fromisoformat(text).replace(tzinfo=dt.UTC).timestamp()
    except ValueError:
        return 0.0
