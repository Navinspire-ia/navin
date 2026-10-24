# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from navin.agent.memory import MemoryStore


def _store(tmp_path):
    store = MemoryStore(tmp_path)
    store.append_history("old mission: translate 8 locales", session_key="websocket:old")
    store.append_history("this chat: build a 3D app", session_key="websocket:new")
    return store


def _texts(entries):
    return " | ".join(e["content"] for e in entries)


def test_a_new_session_sees_only_its_own_history(tmp_path):
    entries = _store(tmp_path).read_recent_history_for_prompt(
        0, session_key="websocket:new", unified_session=True,
    )
    assert "3D app" in _texts(entries)
    assert "8 locales" not in _texts(entries)


def test_asking_for_history_brings_other_sessions_back(tmp_path):
    entries = _store(tmp_path).read_recent_history_for_prompt(
        0, session_key="websocket:new", unified_session=True, include_other_sessions=True,
    )
    assert "8 locales" in _texts(entries)


def test_a_non_unified_session_stays_isolated_even_when_asking(tmp_path):
    entries = _store(tmp_path).read_recent_history_for_prompt(
        0, session_key="websocket:new", include_other_sessions=True,
    )
    assert "8 locales" not in _texts(entries)
