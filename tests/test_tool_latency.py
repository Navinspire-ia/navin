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


class _LoopDiskCalls:
    """Counts the disk calls made on the event-loop thread, by kind and caller.

    On a WSL project opened from the Windows desktop each stat, open or listing
    is a 9P round trip; on the loop thread it also stops every other chat, the
    streaming and the UI routes (the desktop log of 2026-10-31: 114 stalls,
    605 s). Work handed to ``asyncio.to_thread`` is not counted.
    """

    _TARGETS = (
        ("os", "stat"), ("os", "lstat"), ("os", "scandir"), ("os", "listdir"),
        ("os", "mkdir"), ("os", "replace"), ("os", "rename"), ("os", "unlink"),
        ("io", "open"), ("builtins", "open"),
    )

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import builtins
        import io
        import os
        import threading

        self.loop_thread: int | None = None
        self.armed = False
        self.calls: list[str] = []
        modules = {"os": os, "io": io, "builtins": builtins}
        for module_name, attr in self._TARGETS:
            module = modules[module_name]
            monkeypatch.setattr(module, attr, self._wrap(getattr(module, attr), f"{module_name}.{attr}", threading))

    def _wrap(self, original: Callable[..., Any], label: str, threading: Any) -> Callable[..., Any]:
        def counted(*args: Any, **kwargs: Any) -> Any:
            if self.armed and threading.get_ident() == self.loop_thread:
                frame = sys._getframe(1)
                while frame is not None and "/navin/" not in frame.f_code.co_filename.replace("\\", "/"):
                    frame = frame.f_back
                where = (
                    f"{frame.f_code.co_filename.rsplit('/navin/', 1)[-1]}:{frame.f_code.co_name}:{frame.f_lineno}"
                    if frame is not None else "?"
                )
                self.calls.append(f"{label} {args[0] if args else ''} at {where}")
            return original(*args, **kwargs)

        return counted


def _agent_scenarios(ws: Path) -> list[tuple[str, Callable[[], Awaitable[Any]]]]:
    """Every way the agent explores and edits, as it calls the tools."""
    from navin.agent import user_hooks
    from navin.agent.tools.file_manage import ManageFilesTool

    grep = GrepTool(workspace=ws)
    find = FindFilesTool(workspace=ws)
    ls = ListDirTool(workspace=ws)
    read = ReadFileTool(workspace=ws)
    edit = EditFileTool(workspace=ws, lint_after_edit=False)
    patch = ApplyPatchTool(workspace=ws, lint_after_edit=False)
    manage = ManageFilesTool(workspace=ws)
    index = CodeIndexTool(workspace=ws)
    graph = MetagraphTool(workspace=ws)
    return [
        ("grep identifier", lambda: grep.execute(pattern="ProbeService")),
        ("grep content", lambda: grep.execute(pattern="def handle_probe", output_mode="content")),
        ("grep content with context", lambda: grep.execute(pattern="def handle_probe", output_mode="content", context_before=2, context_after=2)),
        ("grep count", lambda: grep.execute(pattern="return", output_mode="count")),
        ("grep no match in a file", lambda: grep.execute(pattern="zz_absent", path="pkg/service.py")),
        ("grep invented file", lambda: grep.execute(pattern="ProbeService", path="pkg/servise.py")),
        ("grep several patterns", lambda: grep.execute(patterns=["ProbeService", "handle_probe", "zz_absent"])),
        ("find_files query", lambda: find.execute(query="service")),
        ("find_files missing dir", lambda: find.execute(path="pkg/nope")),
        ("list_dir", lambda: ls.execute(path="pkg")),
        ("list_dir missing", lambda: ls.execute(path="pkx")),
        ("read_file", lambda: read.execute(path="pkg/service.py")),
        ("read_file batch", lambda: read.execute(files=[{"path": "pkg/service.py", "limit": 5}, {"path": "README.md"}])),
        ("read_file missing", lambda: read.execute(path="pkg/servis.py")),
        ("edit_file", lambda: edit.execute(path="pkg/service.py", old_text="return 1", new_text="return 2")),
        ("apply_patch", lambda: patch.execute(edits=[{"path": "pkg/service.py", "action": "replace", "old_text": "return 2", "new_text": "return 3"}])),
        ("manage_files mkdir", lambda: manage.execute(action="mkdir", path="pkg/new_dir")),
        ("manage_files copy", lambda: manage.execute(action="copy", path="README.md", destination="pkg/new_dir/README.md")),
        ("manage_files delete", lambda: manage.execute(action="delete", paths=["pkg/new_dir"], recursive=True)),
        ("code_index definition", lambda: index.execute(action="definition", name="ProbeService")),
        ("metagraph find", lambda: graph.execute(action="find", query="service")),
        ("hooks before a tool", lambda: user_hooks.pre_tool_use_block(ws, "cli:probe", "grep", {"pattern": "x"})),
        ("hooks after a tool", lambda: user_hooks.post_tool_use_feedback(ws, "cli:probe", "grep", {"pattern": "x"}, "ok")),
        ("hooks on a prompt", lambda: user_hooks.prompt_hooks(ws, "cli:probe", "fix pkg/service.py", new_session=False)),
    ]


def test_agent_tools_never_touch_the_disk_on_the_loop_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = tmp_path / "ws"
    (ws / "pkg").mkdir(parents=True)
    (ws / "README.md").write_text("# probe\n", encoding="utf-8")
    (ws / "pkg" / "service.py").write_text(
        "class ProbeService:\n    def handle_probe(self):\n        return 1\n", encoding="utf-8",
    )
    _git(ws, "init", "-q")
    _git(ws, "add", "-A")
    _git(ws, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "init")
    monkeypatch.setenv("NAVIN_HOME", str(tmp_path / "home"))
    counter = _LoopDiskCalls(monkeypatch)
    import threading

    async def scenario() -> dict[str, list[str]]:
        counter.loop_thread = threading.get_ident()
        # A first pass loads the modules each path imports (imports read .py
        # files) and warms the per-process caches; the second pass is what
        # every later call of the day costs.
        for _label, call in _agent_scenarios(ws):
            await call()
        (ws / "pkg" / "service.py").write_text(
            "class ProbeService:\n    def handle_probe(self):\n        return 1\n", encoding="utf-8",
        )
        found: dict[str, list[str]] = {}
        # The counter itself sees a stat made on the loop thread.
        counter.armed = True
        (ws / "README.md").exists()
        counter.armed = False
        assert any("os.stat" in call for call in counter.calls), counter.calls
        for label, call in _agent_scenarios(ws):
            counter.calls.clear()
            counter.armed = True
            try:
                out = await call()
            finally:
                counter.armed = False
            assert not getattr(out, "is_error", False) or "missing" in label or "invented" in label, (
                f"{label} failed: {str(out)[:300]}"
            )
            if counter.calls:
                found[label] = list(counter.calls)
        return found

    found = _run(scenario())
    assert not found, "disk calls on the event-loop thread:\n" + "\n".join(
        f"{label}:\n  " + "\n  ".join(calls[:6]) for label, calls in found.items()
    )


def test_print_table(capsys: pytest.CaptureFixture[str]) -> None:
    """Last in file order: shows the measurements when run with -s."""
    with capsys.disabled():
        if RESULTS:
            print("\n{:<28} {:>12} {:>10}".format("tool call", "loop gap ms", "wall ms"))
            for label, gap, wall in RESULTS:
                print(f"{label:<28} {gap * 1000:>12.1f} {wall * 1000:>10.1f}")
