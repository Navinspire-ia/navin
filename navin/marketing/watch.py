# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Silent marketing watch: performance winners and competitor changes."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from loguru import logger

from navin.marketing.growth import find_winners, scoreboard
from navin.marketing.lock import marketing_desk_lock
from navin.marketing.store import MarketingStore


def _fingerprint(store: MarketingStore) -> str:
    analytics = store.load_analytics()
    competitors = store.load_competitors()
    parts = [
        str(analytics.get("signups") or 0),
        str(analytics.get("leads") or 0),
        str(len(analytics.get("by_content") or {})),
    ]
    for row in competitors:
        parts.append(
            f"{row.get('name')}|{row.get('pricing')}|{row.get('release') or row.get('note')}"
        )
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def _sent(row: dict[str, Any]) -> set[str]:
    return {str(item) for item in (row.get("alerts_sent") or []) if str(item).strip()}


def digest_was_delivered(sent: dict[str, Any] | None) -> bool:
    if not isinstance(sent, dict):
        return False
    return any(bool(sent.get(name)) for name in ("webui", "telegram", "whatsapp", "email"))


def mark_sent(store: MarketingStore, events: list[dict[str, Any]]) -> None:
    content = store.load_content()
    by_content = {str(row.get("id") or ""): row for row in content if isinstance(row, dict)}
    competitors = store.load_competitors()
    by_comp = {str(row.get("id") or ""): row for row in competitors if isinstance(row, dict)}
    content_changed = False
    comp_changed = False
    for event in events:
        eid = str(event.get("id") or "")
        key = str(event.get("key") or event.get("kind") or "")
        if not eid or not key:
            continue
        if event.get("kind") == "winner" and eid in by_content:
            sent = _sent(by_content[eid])
            sent.add(key)
            by_content[eid]["alerts_sent"] = sorted(sent)
            content_changed = True
        elif event.get("kind") == "competitor" and eid in by_comp:
            sent = _sent(by_comp[eid])
            sent.add(key)
            by_comp[eid]["alerts_sent"] = sorted(sent)
            comp_changed = True
    if content_changed:
        store.save_content([by_content.get(str(row.get("id") or ""), row) for row in content])
    if comp_changed:
        store.save_competitors([by_comp.get(str(row.get("id") or ""), row) for row in competitors])


def run_watch(store: MarketingStore, *, already_locked: bool = False) -> dict[str, Any]:
    """Compare the last fingerprint. Empty events when nothing useful changed."""
    if not already_locked:
        with marketing_desk_lock(store, wait_s=0) as got:
            if not got:
                return {
                    "events": [],
                    "count": 0,
                    "digest": "",
                    "winners": [],
                    "scoreboard": [],
                    "sent": {},
                    "skipped": "busy",
                }
            return _watch_pass(store)
    return _watch_pass(store)


def _watch_pass(store: MarketingStore) -> dict[str, Any]:
    winners = find_winners(store)
    board = scoreboard(store)
    events: list[dict[str, Any]] = []
    content_by_id = {str(row.get("id") or ""): row for row in store.load_content()}
    for winner in winners:
        cid = str(winner.get("id") or "")
        if cid and "winner" in _sent(content_by_id.get(cid) or {}):
            continue
        events.append(
            {
                "kind": "winner",
                "key": "winner",
                "id": cid,
                "text": (
                    f"{winner.get('channel') or 'content'} {cid} converts "
                    f"{winner.get('conversions')} vs the pack"
                ),
            }
        )
    loop = store.load_loop()
    fp = _fingerprint(store)
    if fp != loop.get("last_fingerprint"):
        competitors = store.load_competitors()
        if competitors:
            latest = competitors[0]
            cid = str(latest.get("id") or "")
            key = f"fp:{fp}"
            if key not in _sent(latest):
                events.append(
                    {
                        "kind": "competitor",
                        "key": key,
                        "id": cid,
                        "text": f"competitor update: {latest.get('name')}",
                    }
                )
    digest_lines = [str(item.get("text") or "") for item in events if item.get("text")]
    digest = "\n".join(digest_lines)
    payload: dict[str, Any] = {
        "events": events,
        "count": len(events),
        "digest": digest,
        "winners": winners,
        "scoreboard": board[:8],
        "sent": {},
    }
    if events:
        from navin.marketing.notify import deliver_alert

        title = f"{len(events)} marketing alert{'s' if len(events) != 1 else ''}"
        try:
            payload["sent"] = deliver_alert(store, title=title, detail=digest)
        except Exception:
            logger.exception("marketing watch notify failed")
            payload["sent"] = {}
        if not digest_was_delivered(payload["sent"]):
            payload["delivered"] = False
            return payload
        payload["delivered"] = True
        mark_sent(store, events)
        store.append_journal({"kind": "watch", "text": digest or f"{len(events)} marketing alerts"})
    loop = store.load_loop()
    loop["last_fingerprint"] = fp
    loop["last_watch"] = time.time()
    store.save_loop(loop)
    return payload
