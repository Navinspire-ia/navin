# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Every tool the user drives all day stays fast and never stalls the loop.

A workspace of thousands of files, a 20 MB source file and a git history are
built once; each tool is then called the way an agent calls it while a 5 ms
ticker runs on the same event loop. Two numbers come out per call: the longest
gap the ticker saw (a stall of the loop freezes every chat, terminal and
subagent of the engine) and the wall time the agent waited. Both are bounded.
Run with ``-s`` to print the table.

The static side of the same promise is ``test_no_blocking_call_in_any_async_body``:
it scans all of navin/ for a blocking call written directly in an async body.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import pytest

from navin.agent.tools.apply_patch import ApplyPatchTool
from navin.agent.tools.code_index import CodeIndexTool
from navin.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from navin.agent.tools.git import GitTool
from navin.agent.tools.metagraph import MetagraphTool
from navin.agent.tools.search import FindFilesTool, GrepTool
from navin.agent.tools.shell import ExecTool

ROOT = Path(__file__).resolve().parents[1]
FILES = 3000
BIG_LINES = 400_000  # about 20 MB
# The loop may not stall longer than this during one tool call. A human feels
# 100 ms; a typing cursor stutters at 50 ms. CI boxes are slow, so 150 ms.
LOOP_BUDGET_S = 0.150
# What the agent waits for one call, at this scale.
WALL_BUDGET_S = {"default": 3.0, "index": 60.0}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture(scope="module")
def big_workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("big_ws")
    pkg = root / "pkg"
    for i in range(FILES):
        folder = pkg / f"mod{i % 60:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"unit_{i:04d}.py").write_text(
            "\n".join(
                [
                    f'"""Unit {i}."""',
                    "from __future__ import annotations",
                    "",
                    f"def handler_{i}(payload: dict) -> dict:",
                    f"    needle_{i % 97} = payload.get('key', {i})",
                    f"    return {{'unit': {i}, 'value': needle_{i % 97}}}",
                    "",
                    f"class Service{i}:",
                    "    def run(self) -> int:",
                    f"        return handler_{i}({{'key': 1}})['unit']",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    (root / "big.log").write_text(
        "\n".join(f"line {n:07d} some words here to make it realistic" for n in range(BIG_LINES)),
        encoding="utf-8",
    )
    index = {
        "version": 1,
        "files": {
            f"pkg/mod{i % 60:02d}/unit_{i:04d}.py": {
                "fingerprint": "1:1", "kind": "back", "role": f"unit {i} of the stress workspace",
                "tags": ["stress"],
                "depends_on": [f"pkg/mod{(i + 1) % 60:02d}/unit_{(i + 1) % FILES:04d}.py"],
            }
            for i in range(FILES)
        },
    }
    (root / ".navin" / "metadata").mkdir(parents=True)
    (root / ".navin" / "metadata" / "index.json").write_text(json.dumps(index), encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "seed")
    return root


class _Ticker:
    """Measures the longest gap between 5 ms wakeups while a call runs."""

    def __init__(self) -> None:
        self.max_gap = 0.0
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        last = time.perf_counter()
        while not self._stop.is_set():
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            self.max_gap = max(self.max_gap, now - last)
            last = now

    async def __aenter__(self) -> "_Ticker":
        self._task = asyncio.create_task(self._run())
        await asyncio.sleep(0.02)
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._stop.set()
        assert self._task is not None
        await self._task


RESULTS: list[tuple[str, float, float]] = []


async def _measure(label: str, call: Callable[[], Awaitable[Any]], *, wall: str = "default") -> str:
    async with _Ticker() as ticker:
        started = time.perf_counter()
        out = await call()
        elapsed = time.perf_counter() - started
    RESULTS.append((label, ticker.max_gap, elapsed))
    text = str(out)
    assert not getattr(out, "is_error", False), f"{label} failed: {text[:400]}"
    assert ticker.max_gap < LOOP_BUDGET_S, f"{label}: event loop stalled {ticker.max_gap * 1000:.0f} ms"
    assert elapsed < WALL_BUDGET_S[wall], f"{label}: took {elapsed:.2f}s"
    return text


def _run(coro: Awaitable[Any]) -> Any:
    return asyncio.run(coro)


class TestFilesAtScale:
    def test_read_write_edit_patch_on_a_20mb_file(self, big_workspace: Path) -> None:
        read = ReadFileTool(workspace=big_workspace)
        write = WriteFileTool(workspace=big_workspace)
        edit = EditFileTool(workspace=big_workspace)
        patch = ApplyPatchTool(workspace=big_workspace)
        big = big_workspace / "big.log"
        copy = big_workspace / "copy.log"

        async def scenario() -> None:
            head = await _measure("read_file head", lambda: read.execute(path="big.log", limit=200))
            assert "line 0000000" in head
            tail = await _measure(
                "read_file deep offset", lambda: read.execute(path="big.log", offset=BIG_LINES - 50, limit=50)
            )
            assert f"line {BIG_LINES - 2:07d}" in tail
            await _measure("write_file 20MB", lambda: write.execute(path="copy.log", content=big.read_text()))
            assert copy.stat().st_size == big.stat().st_size
            await _measure(
                "edit_file in 20MB",
                lambda: edit.execute(
                    path="copy.log", old_text=f"line {BIG_LINES // 2:07d} some words",
                    new_text=f"line {BIG_LINES // 2:07d} EDITED words",
                ),
            )
            await _measure(
                "apply_patch in 20MB",
                lambda: patch.execute(edits=[{
                    "path": "copy.log", "action": "replace",
                    "old_text": f"line {BIG_LINES // 4:07d} some words",
                    "new_text": f"line {BIG_LINES // 4:07d} PATCHED words",
                }]),
            )
            text = copy.read_text()
            assert "EDITED" in text and "PATCHED" in text

        _run(scenario())

    def test_list_find_grep_over_thousands_of_files(self, big_workspace: Path) -> None:
        listing = ListDirTool(workspace=big_workspace)
        find = FindFilesTool(workspace=big_workspace)
        grep = GrepTool(workspace=big_workspace)

        async def scenario() -> None:
            out = await _measure("list_dir recursive", lambda: listing.execute(path="pkg", recursive=True, max_entries=500))
            assert "unit_" in out
            out = await _measure("find_files glob", lambda: find.execute(glob="pkg/**/unit_01*.py", head_limit=100))
            assert "unit_0100.py" in out
            out = await _measure("grep files_with_matches", lambda: grep.execute(pattern="needle_42", path="pkg"))
            assert "unit_" in out
            out = await _measure(
                "grep content", lambda: grep.execute(pattern=r"def handler_29\d\d", path="pkg", output_mode="content"),
            )
            assert "handler_29" in out
            out = await _measure("grep big file", lambda: grep.execute(pattern="line 0399999", path="big.log", output_mode="content"))
            assert "0399999" in out

        _run(scenario())


class TestIndexesAtScale:
    def test_code_index_builds_and_answers(self, big_workspace: Path) -> None:
        tool = CodeIndexTool(workspace=big_workspace)

        async def scenario() -> None:
            await _measure("code_index refresh", lambda: tool.execute(action="refresh"), wall="index")
            out = await _measure("code_index definition", lambda: tool.execute(action="definition", name="handler_1234"))
            assert "unit_1234.py" in out
            out = await _measure("code_index search", lambda: tool.execute(action="search", query="Service12", limit=20))
            assert "Service12" in out
            await _measure("code_index overview", lambda: tool.execute(action="overview"))

        _run(scenario())

    def test_metagraph_answers_from_a_big_index(self, big_workspace: Path) -> None:
        tool = MetagraphTool(workspace=big_workspace)

        async def scenario() -> None:
            out = await _measure("metagraph overview", lambda: tool.execute(action="overview"))
            assert "back" in out
            out = await _measure("metagraph find", lambda: tool.execute(action="find", query="unit 1234 of"))
            assert "unit_1234.py" in out
            out = await _measure(
                "metagraph file", lambda: tool.execute(action="file", path="pkg/mod00/unit_0000.py"),
            )
            assert "stress workspace" in out
            await _measure("metagraph hubs", lambda: tool.execute(action="hubs"))

        _run(scenario())


class TestGitAndExecAtScale:
    def test_git_status_log_diff_on_thousands_of_files(self, big_workspace: Path) -> None:
        tool = GitTool(workspace=big_workspace)
        (big_workspace / "pkg" / "mod00" / "unit_0000.py").write_text("# changed\n", encoding="utf-8")

        async def scenario() -> None:
            out = await _measure("git status", lambda: tool.execute(action="status"))
            assert "unit_0000.py" in out
            out = await _measure("git diff", lambda: tool.execute(action="diff"))
            assert "changed" in out
            out = await _measure("git log", lambda: tool.execute(action="log", limit=5))
            assert "seed" in out

        _run(scenario())

    def test_exec_round_trip_is_quick(self, big_workspace: Path) -> None:
        tool = ExecTool(working_dir=str(big_workspace))
        echo = "echo ready" if sys.platform != "win32" else "Write-Output ready"

        async def scenario() -> None:
            for n in range(3):
                out = await _measure(f"exec echo #{n + 1}", lambda: tool.execute(command=echo))
                assert "ready" in out

        _run(scenario())


def test_no_blocking_call_in_any_async_body() -> None:
    """The scanner in scripts/ is the law for the whole engine, not only tools."""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import scan_blocking_async
    finally:
        sys.path.pop(0)
    rows: list[str] = []
    for file in sorted((ROOT / "navin").rglob("*.py")):
        rows.extend(scan_blocking_async.scan_file(file))
    assert not rows, "blocking call on the event loop:\n" + "\n".join(rows)


def test_print_table(capsys: pytest.CaptureFixture[str]) -> None:
    """Last in file order: shows the measurements when run with -s."""
    with capsys.disabled():
        if RESULTS:
            print("\n{:<28} {:>12} {:>10}".format("tool call", "loop gap ms", "wall ms"))
            for label, gap, wall in RESULTS:
                print(f"{label:<28} {gap * 1000:>12.1f} {wall * 1000:>10.1f}")
