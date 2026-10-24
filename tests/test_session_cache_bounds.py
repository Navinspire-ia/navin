# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""SessionManager keeps a bounded number of sessions pinned in memory, safely."""

import asyncio
import gc

from navin.session.manager import SessionManager


def _manager(tmp_path, limit: int = 3) -> SessionManager:
    return SessionManager(tmp_path, max_cached_sessions=limit)


def _saved(manager: SessionManager, key: str) -> None:
    session = manager.get_or_create(key)
    session.add_message("user", f"hello {key}")
    manager.save(session)


def test_eviction_bounds_strong_cache(tmp_path) -> None:
    manager = _manager(tmp_path)
    for i in range(10):
        _saved(manager, f"cli:{i}")
    gc.collect()
    assert manager._cache.strong_count == 3
    assert len(manager._cache) == 3  # nothing else references the others
    assert manager._cache.get("cli:0") is None
    assert manager._cache.get("cli:9") is not None


def test_idle_reads_do_not_pin_every_session(tmp_path) -> None:
    manager = _manager(tmp_path)
    for i in range(10):
        _saved(manager, f"cli:{i}")
    for i in range(10):
        manager.get_or_create(f"cli:{i}")  # autocompact idle probe pattern
    gc.collect()
    assert manager._cache.strong_count == 3


def test_referenced_session_returns_same_object_after_lru(tmp_path) -> None:
    manager = _manager(tmp_path)
    held = manager.get_or_create("cli:held")
    held.add_message("user", "first")
    manager.save(held)
    for i in range(10):
        _saved(manager, f"cli:{i}")
    gc.collect()
    assert "cli:held" not in manager._cache._strong
    again = manager.get_or_create("cli:held")
    assert again is held
    assert "cli:held" in manager._cache._strong


def test_dirty_session_is_not_evicted(tmp_path) -> None:
    manager = _manager(tmp_path)
    dirty = manager.get_or_create("cli:dirty")
    dirty.add_message("user", "never saved")
    dirty_id = id(dirty)
    del dirty
    for i in range(10):
        _saved(manager, f"cli:{i}")
    gc.collect()
    session = manager.get_or_create("cli:dirty")
    assert id(session) == dirty_id
    assert [m["content"] for m in session.messages] == ["never saved"]


def test_metadata_edit_counts_as_dirty(tmp_path) -> None:
    manager = _manager(tmp_path)
    _saved(manager, "cli:meta")
    manager.get_or_create("cli:meta").metadata["title"] = "Edited"
    for i in range(10):
        _saved(manager, f"cli:{i}")
    gc.collect()
    assert manager.get_or_create("cli:meta").metadata["title"] == "Edited"


def test_released_session_edited_later_is_pinned_again(tmp_path) -> None:
    manager = _manager(tmp_path)
    held = manager.get_or_create("cli:held")
    held.add_message("user", "first")
    manager.save(held)
    for i in range(5):
        _saved(manager, f"cli:{i}")
    assert "cli:held" not in manager._cache._strong
    held.add_message("assistant", "edited while released")
    _saved(manager, "cli:trigger")
    assert manager._cache._strong.get("cli:held") is held
    del held
    gc.collect()
    session = manager.get_or_create("cli:held")
    assert session.messages[-1]["content"] == "edited while released"


def test_in_flight_background_save_is_not_evicted(tmp_path) -> None:
    async def run() -> None:
        manager = _manager(tmp_path, limit=1)
        session = manager.get_or_create("cli:bg")
        session.add_message("user", "checkpoint")
        task = manager.save_in_background(session)
        assert manager._save_in_flight("cli:bg")
        _saved(manager, "cli:other")
        assert "cli:bg" in manager._cache._strong
        await task
        assert not manager._save_in_flight("cli:bg")

    asyncio.run(run())


def test_saved_unreferenced_session_reloads_equal(tmp_path) -> None:
    manager = _manager(tmp_path)
    original = manager.get_or_create("cli:disk")
    original.add_message("user", "persist me")
    original.metadata["title"] = "Disk"
    manager.save(original)
    snapshot = (list(original.messages), dict(original.metadata), original.last_consolidated)
    del original
    for i in range(10):
        _saved(manager, f"cli:{i}")
    gc.collect()
    assert manager._cache.get("cli:disk") is None
    reloaded = manager.get_or_create("cli:disk")
    assert reloaded.messages == snapshot[0]
    assert reloaded.metadata == snapshot[1]
    assert reloaded.last_consolidated == snapshot[2]


def test_invalidate_and_flush_all_cover_released_sessions(tmp_path) -> None:
    manager = _manager(tmp_path)
    held = manager.get_or_create("cli:held")
    held.add_message("user", "x")
    manager.save(held)
    for i in range(10):
        _saved(manager, f"cli:{i}")
    assert "cli:held" in manager._cache
    live = len(manager._cache)
    assert manager.flush_all() == live == 4
    manager.invalidate("cli:held")
    assert "cli:held" not in manager._cache
    assert manager.get_or_create("cli:held") is not held
