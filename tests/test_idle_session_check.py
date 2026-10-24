# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The idle-session check stays off the event loop and does not reload sessions in vain."""

from __future__ import annotations

from types import SimpleNamespace

from navin.agent.autocompact import AutoCompact


class _Sessions:
    def __init__(self) -> None:
        self.rows = [{"key": "websocket:a", "updated_at": "2026-01-01T10:00:00"}]
        self.listed = 0

    def list_sessions(self):
        self.listed += 1
        return list(self.rows)


def test_disabled_ttl_lists_nothing() -> None:
    sessions = _Sessions()
    compact = AutoCompact(sessions, consolidator=SimpleNamespace(), session_ttl_minutes=0)
    assert compact.expired_candidates() == []
    assert sessions.listed == 0


def test_not_compactable_session_is_not_reloaded_until_it_changes(monkeypatch) -> None:
    sessions = _Sessions()
    compact = AutoCompact(sessions, consolidator=SimpleNamespace(), session_ttl_minutes=15)
    loads = []
    monkeypatch.setattr(compact, "_has_compactable_idle_tail", lambda key: loads.append(key) or False)
    for _ in range(3):
        for key in compact.expired_candidates():
            compact.archive_if_compactable(key, lambda coro: None, lambda: None)
    assert loads == ["websocket:a"]  # loaded once, then remembered
    sessions.rows[0]["updated_at"] = "2026-01-01T11:00:00"  # new activity
    for key in compact.expired_candidates():
        compact.archive_if_compactable(key, lambda coro: None, lambda: None)
    assert loads == ["websocket:a", "websocket:a"]
