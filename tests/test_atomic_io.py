# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Cross-platform tests for durable atomic file I/O."""

from __future__ import annotations

import multiprocessing
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from navin.utils.atomic_io import (
    InterProcessLock,
    LockTimeoutError,
    atomic_write_bytes,
    atomic_write_text,
)


def _hold_lock(path: str, ready, release) -> None:
    with InterProcessLock(path, timeout=5):
        ready.send(True)
        release.recv()


def test_atomic_write_replaces_content_and_creates_parents(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "state.json"

    atomic_write_bytes(destination, b'{"version": 1}')
    atomic_write_bytes(destination, b'{"version": 2}')

    assert destination.read_bytes() == b'{"version": 2}'
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []


def test_atomic_write_uses_unique_temporary_files(tmp_path: Path) -> None:
    destination = tmp_path / "state"
    sources: list[Path] = []
    real_replace = os.replace

    def recording_replace(source, target) -> None:
        sources.append(Path(source))
        real_replace(source, target)

    with patch("navin.utils.atomic_io.os.replace", side_effect=recording_replace):
        atomic_write_bytes(destination, b"first")
        atomic_write_bytes(destination, b"second")

    assert len(sources) == 2
    assert sources[0] != sources[1]
    assert all(source.parent == destination.parent for source in sources)


def test_atomic_write_flushes_before_replace_and_syncs_directory(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "state"
    events: list[str] = []
    real_replace = os.replace

    def recording_fsync(descriptor: int) -> None:
        events.append("fsync")

    def recording_replace(source, target) -> None:
        events.append("replace")
        real_replace(source, target)

    with (
        patch("navin.utils.atomic_io.os.fsync", side_effect=recording_fsync),
        patch("navin.utils.atomic_io.os.replace", side_effect=recording_replace),
    ):
        atomic_write_bytes(destination, b"durable")

    assert events[:2] == ["fsync", "replace"]
    if os.name == "nt":
        assert events == ["fsync", "replace"]
    else:
        assert events == ["fsync", "replace", "fsync"]


def test_atomic_write_cleans_only_its_temporary_file_on_failure(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "state"
    destination.write_bytes(b"old")
    unrelated = tmp_path / f".{destination.name}.unrelated.tmp"
    unrelated.write_bytes(b"keep")

    with (
        patch(
            "navin.utils.atomic_io.os.replace",
            side_effect=OSError("replacement failed"),
        ),
        pytest.raises(OSError, match="replacement failed"),
    ):
        atomic_write_bytes(destination, b"new")

    assert destination.read_bytes() == b"old"
    assert unrelated.read_bytes() == b"keep"
    assert list(tmp_path.glob(f".{destination.name}.*.tmp")) == [unrelated]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
def test_atomic_write_preserves_existing_permissions(tmp_path: Path) -> None:
    destination = tmp_path / "state"
    destination.write_bytes(b"old")
    destination.chmod(0o640)

    atomic_write_bytes(destination, b"new")

    assert stat.S_IMODE(destination.stat().st_mode) == 0o640


def test_atomic_write_text_supports_encoding_and_newline_policy(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "message.txt"

    atomic_write_text(destination, "été\nprêt\n", encoding="utf-16-le", newline="\r\n")

    assert destination.read_bytes().decode("utf-16-le") == "été\r\nprêt\r\n"


def test_atomic_write_can_require_an_existing_parent(tmp_path: Path) -> None:
    destination = tmp_path / "missing" / "state"

    with pytest.raises(FileNotFoundError):
        atomic_write_bytes(destination, b"data", create_parents=False)


def test_interprocess_lock_times_out_then_can_be_reacquired(tmp_path: Path) -> None:
    lock_path = tmp_path / "state.lock"
    context = multiprocessing.get_context("spawn" if os.name == "nt" else "fork")
    ready_parent, ready_child = context.Pipe(duplex=False)
    release_child, release_parent = context.Pipe(duplex=False)
    process = context.Process(
        target=_hold_lock,
        args=(str(lock_path), ready_child, release_child),
    )
    process.start()
    try:
        assert ready_parent.poll(5), "child did not acquire the lock"
        assert ready_parent.recv() is True
        with pytest.raises(LockTimeoutError):
            with InterProcessLock(lock_path, timeout=0.1, poll_interval=0.01):
                pytest.fail("contended lock was acquired")
    finally:
        if process.is_alive():
            release_parent.send(True)
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join()

    assert process.exitcode == 0
    with InterProcessLock(lock_path, timeout=1) as lock:
        assert lock.acquired
    assert not lock.acquired


def test_lock_rejects_invalid_state_and_configuration(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        InterProcessLock(tmp_path / "lock", timeout=-1)
    with pytest.raises(ValueError):
        InterProcessLock(tmp_path / "lock", poll_interval=0)

    lock = InterProcessLock(tmp_path / "lock", timeout=1)
    with pytest.raises(RuntimeError, match="not acquired"):
        lock.release()
    lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="already acquired"):
            lock.acquire()
    finally:
        lock.release()
