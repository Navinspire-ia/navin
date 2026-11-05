# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Peer agents awareness: cross-session registry, context block and write guard."""

from __future__ import annotations

import asyncio
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest
from loguru import logger

from navin.agent import peer_activity
from navin.agent.tools.apply_patch import ApplyPatchTool
from navin.agent.tools.context import RequestContext
from navin.agent.tools.file_manage import ManageFilesTool
from navin.agent.tools.filesystem import EditFileTool, WriteFileTool
from navin.runtime_context import RUNTIME_CONTEXT_END, RUNTIME_CONTEXT_TAG


@pytest.fixture
def registry(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "home" / "activity"
    monkeypatch.setattr(peer_activity, "_activity_root", lambda: root)
    yield root
    peer_activity._remove_own_entries()


@pytest.fixture
def project(tmp_path) -> Path:
    path = tmp_path / "project"
    (path / "src").mkdir(parents=True)
    return path.resolve()


def _project_dir(project: Path) -> Path:
    return peer_activity._project_dir(str(project))


def _fake_entry(project: Path, *, pid: int, start: float, family: str,
                files: dict[str, float] | None = None, title: str = "Fix login") -> Path:
    """An entry as another process would have written it."""
    key = f"{family}#fake"
    now = time.time()
    path = _project_dir(project) / peer_activity._entry_name(pid, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "pid": pid,
        "create_time": now - 3600,
        "start": start,
        "session_key": key,
        "family": family,
        "title": title,
        "surface": "navin-cli",
        "project": str(project),
        "task": "fix the login form",
        "started_at": now - 360,
        "updated_at": now,
        "files": files or {},
    }), encoding="utf-8")
    return path


def _own_create_time() -> float:
    return psutil.Process(os.getpid()).create_time()


def _own_start() -> float:
    return peer_activity._start_marker(os.getpid())


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def test_registry_round_trip(registry, project):
    turn = peer_activity.begin_turn(
        project=project, session_key="cli:direct", title="Fix login",
        surface=peer_activity.surface_for("cli"),
        task=f"fix the login\n\n{RUNTIME_CONTEXT_TAG}\nbranch: main\n{RUNTIME_CONTEXT_END}",
    )
    assert turn is not None
    assert turn.path.parent.parent == registry
    assert turn.path.name.startswith(f"{os.getpid()}-")
    assert stat.S_IMODE(turn.path.stat().st_mode) == 0o600
    peer_activity.note_file_edit(project / "src" / "a.py", turn)
    peer_activity.note_file_edit(project.parent / "outside.py", turn)

    data = json.loads(turn.path.read_text(encoding="utf-8"))
    assert data["pid"] == os.getpid()
    assert data["create_time"] == pytest.approx(_own_create_time())
    assert data["start"] == _own_start()
    assert data["family"] == "cli:direct"
    assert data["surface"] == "navin-cli"
    assert data["task"] == "fix the login"
    assert list(data["files"]) == ["src/a.py"]

    entries = peer_activity.live_entries(project)
    assert [entry.session_key for entry in entries] == ["cli:direct"]
    assert entries[0].files["src/a.py"] == pytest.approx(data["files"]["src/a.py"])

    peer_activity.end_turn(turn)
    assert not turn.path.exists()
    assert peer_activity.live_entries(project) == []
    # A turn already ended takes no more notes and writes nothing.
    peer_activity.note_file_edit(project / "src" / "b.py", turn)
    assert not turn.path.exists()


def test_dead_pid_and_reused_pid_entries_are_deleted(registry, project):
    dead = _fake_entry(project, pid=_dead_pid(), start=0.0, family="web:dead")
    reused = _fake_entry(
        project, pid=os.getpid(), start=_own_start() - 100, family="web:reused",
    )
    live = _fake_entry(project, pid=os.getpid(), start=_own_start(), family="web:live")

    found = peer_activity.peers(project, "cli:direct")

    assert [entry.family for entry in found] == ["web:live"]
    assert not dead.exists()
    assert not reused.exists()
    assert live.exists()


def test_corrupt_entries_are_deleted_with_a_warning(registry, project):
    directory = _project_dir(project)
    directory.mkdir(parents=True)
    broken = directory / "123-abcdefabcdef.json"
    broken.write_text("{not json", encoding="utf-8")
    wrong_shape = directory / "124-abcdefabcdef.json"
    wrong_shape.write_text(json.dumps({"pid": "x"}), encoding="utf-8")
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    try:
        assert peer_activity.peers(project, "cli:direct") == []
    finally:
        logger.remove(sink)

    assert not broken.exists()
    assert not wrong_shape.exists()
    assert sum("corrupt entry" in message for message in messages) == 2


def test_peers_excludes_the_own_family(registry, project):
    chat = peer_activity.begin_turn(project=project, session_key="websocket:a", title="Chat A")
    child = peer_activity.begin_turn(
        project=project, session_key="websocket:a:subagent:t1", family="websocket:a",
        title="worker (subagent)",
    )
    other = peer_activity.begin_turn(project=project, session_key="cli:b", title="Chat B")

    seen_by_a = peer_activity.peers(project, "websocket:a")
    seen_by_b = peer_activity.peers(project, "cli:b")

    assert [entry.title for entry in seen_by_a] == ["Chat B"]
    assert sorted(entry.title for entry in seen_by_b) == ["Chat A", "worker (subagent)"]
    for turn in (chat, child, other):
        peer_activity.end_turn(turn)
    assert peer_activity.peers(project, "cli:b") == []


def test_a_clock_correction_does_not_make_a_live_peer_look_dead(registry, project, monkeypatch):
    """Linux derives psutil's create_time from the wall clock, which WSL corrects
    after every sleep of the host: a live peer must stay alive, claims and all."""
    turn = peer_activity.begin_turn(project=project, session_key="cli:b", title="Fix login")
    real = psutil.Process

    class ClockMoved:
        """The same process seen after the wall clock jumped by an hour."""

        def __init__(self, pid: int) -> None:
            self._process = real(pid)

        def create_time(self) -> float:
            return self._process.create_time() + 3600

    monkeypatch.setattr(psutil, "Process", ClockMoved)
    seen = [entry.session_key for entry in peer_activity.peers(project, "websocket:a")]

    # Outside Linux the kernel keeps the start time: a moved one is another process.
    assert seen == (["cli:b"] if sys.platform.startswith("linux") else [])
    peer_activity.end_turn(turn)


def test_context_block_lists_peers_and_is_absent_when_alone(registry, project):
    request = RequestContext(channel="cli", chat_id="direct", session_key="cli:direct",
                             workspace=project)
    own = peer_activity.begin_turn(project=project, session_key="cli:direct", title="Mine")
    peer_activity.note_file_edit(project / "src" / "mine.py", own)

    assert asyncio.run(peer_activity.peer_activity_context_provider(request)) is None

    now = time.time()
    files = {f"src/f{index}.py": now - index for index in range(9)}
    _fake_entry(project, pid=os.getpid(), start=_own_start(),
                family="web:other", files=files)
    block = asyncio.run(peer_activity.peer_activity_context_provider(request))

    assert block is not None
    assert block.source == "peers"
    assert block.content.startswith(RUNTIME_CONTEXT_TAG)
    assert "Other agents are working in this project right now (not your work):" in block.content
    assert (
        f"- 'Fix login' (navin-cli pid {os.getpid()}, 6 min): fix the login form; "
        "editing: src/f0.py, src/f1.py, src/f2.py, src/f3.py, src/f4.py, src/f5.py (+3 more)"
    ) in block.content
    assert "Their files are theirs: do not edit, revert, format or fix them." in block.content
    assert "mine.py" not in block.content
    peer_activity.end_turn(own)


def test_context_block_shows_at_most_five_peers(registry, project):
    for index in range(7):
        _fake_entry(project, pid=os.getpid(), start=_own_start(),
                    family=f"web:peer{index}", title=f"Peer {index}")
    lines = peer_activity.peer_lines(peer_activity.peers(project, "cli:direct"))
    assert sum(line.startswith("- '") for line in lines) == 5
    assert "- and 2 more agent(s)" in lines


def test_guard_refuses_a_peer_file_allows_own_family_and_after_end(registry, project):
    peer = peer_activity.begin_turn(project=project, session_key="cli:b",
                                    title="Fix login", surface="navin-cli")
    peer_activity.note_file_edit(project / "src" / "a.py", peer)
    own = peer_activity.begin_turn(project=project, session_key="websocket:a")
    sibling = peer_activity.begin_turn(project=project, session_key="websocket:a:subagent:t1",
                                       family="websocket:a")
    peer_activity.note_file_edit(project / "src" / "b.py", sibling)
    token = peer_activity.bind_turn(own)
    try:
        refusal = peer_activity.refusal_for(project / "src" / "a.py")
        assert refusal is not None
        assert refusal.startswith(
            f"Not changed: src/a.py is being edited by another agent right now "
            f"(session 'Fix login', navin-cli pid {os.getpid()}, last change "
        )
        assert refusal.endswith(
            "Leave it to them: finish your part and add one line at the end of your "
            "answer about what you needed in this file."
        )
        claim = peer_activity.claim_of(project, "websocket:a", project / "src" / "a.py")
        assert claim is not None and claim.session_key == "cli:b"
        # The chat's own subagent edited b.py: same family, never blocked.
        assert peer_activity.refusal_for(project / "src" / "b.py") is None
        assert peer_activity.refusal_for(project / "src" / "free.py") is None

        peer_activity.end_turn(peer)
        assert peer_activity.refusal_for(project / "src" / "a.py") is None
    finally:
        peer_activity.reset_turn(token)
        peer_activity.end_turn(sibling)
        peer_activity.end_turn(own)


def test_own_entries_are_removed_at_process_exit(registry, project):
    turn = peer_activity.begin_turn(project=project, session_key="cli:direct")
    assert turn is not None and turn.path.exists()
    peer_activity._remove_own_entries()
    assert not turn.path.exists()
    assert turn.ended


def test_write_tools_refuse_a_live_peer_file(registry, project):
    """Through the real tools: the peer's file is untouched, the rest is written."""
    claimed = project / "src" / "a.py"
    claimed.write_text("peer = 1\n", encoding="utf-8")
    _fake_entry(project, pid=os.getpid(), start=_own_start(),
                family="cli:other", files={"src/a.py": time.time() - 40})

    async def scenario() -> dict[str, str]:
        with peer_activity.active_turn(project=project, session_key="websocket:me") as own:
            write = WriteFileTool(workspace=project, allowed_dir=project)
            edit = EditFileTool(workspace=project, allowed_dir=project)
            patch = ApplyPatchTool(workspace=project, allowed_dir=project)
            manage = ManageFilesTool(workspace=project, allowed_dir=project)
            results = {
                "write": str(await write.execute(path="src/a.py", content="mine = 2\n")),
                "edit": str(await edit.execute(path="src/a.py", old_text="peer = 1",
                                               new_text="mine = 2")),
                "patch": str(await patch.execute(edits=[
                    {"path": "src/c.py", "action": "add", "new_text": "c = 1\n"},
                    {"path": "src/a.py", "action": "add", "new_text": "mine = 2\n"},
                ])),
                "delete": str(await manage.execute(action="delete", paths=["src/a.py"])),
                "free": str(await write.execute(path="src/b.py", content="b = 1\n")),
            }
            assert own is not None
            results["own_files"] = ",".join(own.entry["files"])
            return results

    results = asyncio.run(scenario())

    for name in ("write", "edit", "patch", "delete"):
        assert "Not changed: src/a.py is being edited by another agent right now" in results[name], name
        assert "session 'Fix login', navin-cli pid" in results[name], name
    assert re.search(r"last change 4\d s ago", results["write"])
    assert claimed.read_text(encoding="utf-8") == "peer = 1\n"
    # The patch is all or nothing: its other file was not created either.
    assert not (project / "src" / "c.py").exists()
    assert (project / "src" / "b.py").read_text(encoding="utf-8") == "b = 1\n"
    assert results["own_files"] == "src/b.py"
