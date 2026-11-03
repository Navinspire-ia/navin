# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A timed-out linter or test runner must not leave its workers running.

``subprocess.run(timeout=...)`` kills only the launcher. The ``pyright`` pip
shim starts node; every timed-out ``pyright --project`` left that node at 100%
CPU and 2.7 GB of RAM, until the machine stalled and the web UI went black.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from navin.utils.proc import run_killing_tree

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers kill(0); it no longer uses CPU.
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stat:
            return stat.read().split(") ", 1)[1][0] != "Z"
    except OSError:
        return True


def _describe(pid: int) -> str:
    """What a surviving worker looks like, so a failure says why it lived."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stat:
            line = stat.read().strip()
    except OSError as exc:
        line = f"/proc unreadable: {exc}"
    try:
        groups = f"pgid={os.getpgid(pid)} sid={os.getsid(pid)}"
    except OSError as exc:
        groups = f"group unreadable: {exc}"
    return f"worker {pid} survived: {groups}; stat: {line}"


def _launch_and_time_out(pid_file, popen_extra: str = "") -> int:
    # A launcher that starts a long-running worker and waits on it, like the
    # pyright shim waiting on node.
    script = (
        "import subprocess, sys, pathlib\n"
        f"worker = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']{popen_extra})\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(worker.pid))\n"
        "worker.wait()\n"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        run_killing_tree([sys.executable, "-c", script], timeout=2, capture_output=True, text=True)
    return int(pid_file.read_text())


def test_timeout_kills_the_grandchild(tmp_path) -> None:
    worker = _launch_and_time_out(tmp_path / "worker.pid")
    deadline = time.monotonic() + 5
    while _alive(worker) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(worker), _describe(worker)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="descendants are read from /proc")
def test_timeout_kills_a_grandchild_that_left_the_group(tmp_path) -> None:
    # A worker in its own session is out of reach of the group kill; the
    # timeout must still find it through its parent link and wait for it.
    worker = _launch_and_time_out(tmp_path / "worker.pid", popen_extra=", start_new_session=True")
    assert not _alive(worker), _describe(worker)


def test_completed_run_returns_output_like_subprocess_run() -> None:
    done = run_killing_tree(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
        timeout=30,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 3
    assert done.stdout.strip() == "out"
    assert done.stderr.strip() == "err"
