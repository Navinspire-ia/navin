"""Mid-turn checkpoints are written off the event loop, and a later save always wins."""

from __future__ import annotations

import asyncio
import json

from navin.session.manager import SessionManager


def _lines(manager: SessionManager, key: str) -> list[dict]:
    return [json.loads(line) for line in manager._get_session_path(key).read_text().splitlines()]


def test_background_save_writes_the_state_of_the_moment(tmp_path):
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("websocket:a")
    session.messages = [{"role": "user", "content": "hello"}]

    async def scenario():
        task = manager.save_in_background(session)
        session.messages.append({"role": "assistant", "content": "added after the copy"})
        await task

    asyncio.run(scenario())
    lines = _lines(manager, "websocket:a")
    assert [m["content"] for m in lines[1:]] == ["hello"]


def test_an_older_write_never_replaces_a_newer_save(tmp_path):
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("websocket:b")
    session.messages = [{"role": "user", "content": "one"}]
    older = manager._save_snapshot(session, copy=True)  # a checkpoint still on its way
    session.messages.append({"role": "assistant", "content": "final answer"})
    manager.save(session)  # end of turn
    manager._write_snapshot(older, fsync=False)  # the old checkpoint lands late
    assert [m["content"] for m in _lines(manager, "websocket:b")[1:]] == ["one", "final answer"]


def test_writes_in_order_all_land(tmp_path):
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("websocket:c")

    async def scenario():
        tasks = []
        for index in range(5):
            session.messages.append({"role": "user", "content": f"m{index}"})
            tasks.append(manager.save_in_background(session))
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    assert [m["content"] for m in _lines(manager, "websocket:c")[1:]] == [f"m{i}" for i in range(5)]


def test_conversations_live_under_dot_navin_and_legacy_ones_move(tmp_path):
    legacy = tmp_path / "sessions"
    legacy.mkdir()
    (legacy / "Y2xpOjE.jsonl").write_text('{"_type": "metadata"}\n')
    (legacy / ".webui_session_index.json").write_text("{}")

    manager = SessionManager(tmp_path)

    assert manager.sessions_dir == tmp_path / ".navin" / "sessions"
    assert (manager.sessions_dir / "Y2xpOjE.jsonl").is_file()
    assert (manager.sessions_dir / ".webui_session_index.json").is_file()
    assert not legacy.exists()
    manager.save(manager.get_or_create("cli:x"))
    assert not (tmp_path / "sessions").exists()


def test_a_project_sessions_folder_that_is_not_navin_is_left_alone(tmp_path):
    app = tmp_path / "sessions"
    app.mkdir()
    (app / "store.py").write_text("x = 1\n")
    (app / "a.jsonl").write_text("{}\n")

    manager = SessionManager(tmp_path)

    assert (app / "store.py").is_file() and (app / "a.jsonl").is_file()
    assert not (manager.sessions_dir / "a.jsonl").exists()


def test_save_async_writes_the_state_of_the_moment(tmp_path):
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("websocket:d")
    session.messages = [{"role": "user", "content": "hello"}]

    async def scenario():
        await manager.save_async(session)

    asyncio.run(scenario())
    assert [m["content"] for m in _lines(manager, "websocket:d")[1:]] == ["hello"]


def test_save_async_beats_an_older_checkpoint_that_lands_late(tmp_path):
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("websocket:e")
    session.messages = [{"role": "user", "content": "one"}]
    older = manager._save_snapshot(session, copy=True)
    session.messages.append({"role": "assistant", "content": "final"})

    async def scenario():
        await manager.save_async(session)

    asyncio.run(scenario())
    manager._write_snapshot(older, fsync=False)
    assert [m["content"] for m in _lines(manager, "websocket:e")[1:]] == ["one", "final"]


def test_save_async_falls_back_to_a_sync_save_when_the_thread_fails(tmp_path, monkeypatch):
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("websocket:f")
    session.messages = [{"role": "user", "content": "kept"}]
    real_write = manager._write_snapshot
    calls = {"n": 0}

    def flaky(snapshot, *, fsync):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("dictionary changed size during iteration")
        real_write(snapshot, fsync=fsync)

    monkeypatch.setattr(manager, "_write_snapshot", flaky)
    asyncio.run(manager.save_async(session))
    assert calls["n"] == 2
    assert [m["content"] for m in _lines(manager, "websocket:f")[1:]] == ["kept"]


def test_save_async_skips_empty_new_sessions(tmp_path):
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("websocket:g")
    asyncio.run(manager.save_async(session))
    assert not manager._get_session_path("websocket:g").exists()
