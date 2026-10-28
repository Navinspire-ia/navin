# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Search answers in milliseconds, on this repository and at scale.

The user's complaint: a grep or a code search that takes seconds or minutes
while a code index and a file graph exist. This bench measures every search
path the agent uses, the way the agent uses it, on two workspaces:

- this repository (about 5 600 files, the real thing), warm and cold;
- a generated 3 000-file workspace with a 20 MB log, for repeatable numbers.

Each call has a wall budget and a loop-stall budget. Run with ``-s`` for the
table. The budgets are what a user feels: under 100 ms is instant, under 300 ms
is fine, past a second the agent looks stuck.
"""

from __future__ import annotations

import asyncio
import gc
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import pytest

from navin.agent.tools.code_index import CodeIndexTool
from navin.agent.tools.metagraph import MetagraphTool
from navin.agent.tools.search import FindFilesTool, GrepTool

ROOT = Path(__file__).resolve().parents[1]
FILES = 3000
BIG_LINES = 400_000

INSTANT_S = 0.100   # index lookups, warm
FAST_S = 0.300      # a grep for an identifier on a 5k-file repo, warm
COLD_S = 1.500      # the very first grep of a process (file list not cached yet)
INDEX_COLD_S = 60.0  # a full index build of this repo
LOOP_BUDGET_S = 0.150


def _grep_budget_s() -> float:
    """What one grep may take here: 300 ms with the Rust scanner or ripgrep.

    A bare install (CI runs with NAVIN_SKIP_NATIVE=1 and no ripgrep) greps in
    pure Python: the file list comes from git and files without the needle are
    dropped on their bytes, 200 ms on this machine, up to twice that on a shared
    runner. That path has its own 1 s bar in TestGrepWithoutFastBackends; the
    fast-backend bar is not the right ruler for it.
    """
    import navin.agent.tools.search as search

    if search._native_module() is not None or search._rg_binary():
        return FAST_S
    return 1.0

RESULTS: list[tuple[str, float, float]] = []


class _Ticker:
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


async def _measure(
    label: str, call: Callable[[], Awaitable[Any]], budget_s: float, *,
    loop_budget_s: float = LOOP_BUDGET_S, tries: int = 2,
) -> str:
    """Run ``call`` and bound its wall time and the loop stall it caused.

    Warm calls are measured ``tries`` times and judged on their fastest run:
    a shared CI runner or a developer machine with three CLIs open adds
    hundreds of milliseconds of noise to a 60 ms call, and a bench that fails
    on one noisy sample teaches nothing. The loop-stall bound applies to
    every run. Cold and first-time calls pass ``tries=1``: there is only one.
    """
    best: float | None = None
    worst_gap = 0.0
    out: Any = None
    for _ in range(max(1, tries)):
        # What earlier tests built (the code index of this repository: millions
        # of objects) must not be walked by a gen-2 collection in the middle of
        # a measurement: that is the test process's heap, not the tool's cost.
        # The engine applies the same policy to itself at startup.
        gc.freeze()
        async with _Ticker() as ticker:
            started = time.perf_counter()
            out = await call()
            elapsed = time.perf_counter() - started
        worst_gap = max(worst_gap, ticker.max_gap)
        best = elapsed if best is None else min(best, elapsed)
        if best < budget_s / 2:
            break  # comfortably inside: no need for a second sample
    assert best is not None
    RESULTS.append((label, worst_gap, best))
    text = str(out)
    assert not getattr(out, "is_error", False), f"{label} failed: {text[:400]}"
    assert best < budget_s, f"{label}: took {best * 1000:.0f} ms (budget {budget_s * 1000:.0f} ms)"
    assert worst_gap < loop_budget_s, f"{label}: event loop stalled {worst_gap * 1000:.0f} ms"
    return text


def _run(coro: Awaitable[Any]) -> Any:
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def loaded_repo_index() -> None:
    """The code index of this repository, built or loaded once.

    The graph bench measures the graph, not a first index build: run on its
    own (``-k metagraph``) it would otherwise pay the 6 to 12 s build inside a
    300 ms budget. The code_index bench deliberately does not use this fixture,
    its first call is the cold build it measures.
    """
    from navin.index import get_index

    get_index(ROOT).ensure()


@pytest.fixture(scope="module")
def big_workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("search_ws")
    pkg = root / "pkg"
    for i in range(FILES):
        folder = pkg / f"mod{i % 60:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"unit_{i:04d}.py").write_text(
            "\n".join([
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
            ]),
            encoding="utf-8",
        )
    (root / "big.log").write_text(
        "\n".join(f"line {n:07d} some words here to make it realistic" for n in range(BIG_LINES)), encoding="utf-8",
    )
    (root / ".navin" / "metadata").mkdir(parents=True)
    (root / ".navin" / "metadata" / "index.json").write_text(json.dumps({
        "version": 1,
        "files": {
            f"pkg/mod{i % 60:02d}/unit_{i:04d}.py": {
                "fingerprint": "1:1", "kind": "back", "role": f"unit {i} of the search workspace", "tags": ["stress"],
            }
            for i in range(FILES)
        },
    }), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


class TestGrepOnThisRepository:
    """The real thing: the agent greps this repo all day."""

    def test_identifier_grep_is_fast_warm_and_acceptable_cold(self) -> None:
        grep = GrepTool(workspace=ROOT)
        fast = _grep_budget_s()

        async def scenario() -> None:
            out = await _measure("grep identifier (cold)", lambda: grep.execute(pattern="SubagentManager"), COLD_S, tries=1)
            assert "navin/agent/subagent.py" in out
            out = await _measure("grep identifier (warm)", lambda: grep.execute(pattern="SubagentManager"), fast)
            assert "navin/agent/subagent.py" in out
            out = await _measure(
                "grep identifier content", lambda: grep.execute(pattern="def _outcomes_dir", output_mode="content"), fast,
            )
            assert "_outcomes_dir" in out
            out = await _measure(
                "grep regex scoped", lambda: grep.execute(pattern=r"async def (spawn|retry)\(", path="navin/agent", output_mode="content"), fast,
            )
            assert "subagent.py" in out
            out = await _measure(
                "grep literal glob", lambda: grep.execute(pattern="blocking-ok", fixed_strings=True, glob="**/*.py"), fast,
            )
            assert "builtin.py" in out
            out = await _measure(
                "grep 4 patterns", lambda: grep.execute(patterns=["_PREPARE_WORKERS", "COLLAB_TOOLS_VERSION", "def _line_window", "forget_agents_cache"]), fast * 2,
            )
            assert "subagent.py" in out and "schema.py" in out
            out = await _measure("grep no match", lambda: grep.execute(pattern="zzqx_never_" + "in_repo_7731"), fast)
            assert "No matches" in out

        _run(scenario())

    def test_find_files_is_instant(self) -> None:
        find = FindFilesTool(workspace=ROOT)
        fast = _grep_budget_s()

        async def scenario() -> None:
            out = await _measure("find_files glob", lambda: find.execute(glob="tests/test_tool_*.py"), fast)
            assert "test_tool_latency.py" in out
            out = await _measure("find_files query", lambda: find.execute(query="subagent journal"), fast)
            assert "subagent_journal" in out
            out = await _measure("find_files type", lambda: find.execute(type="md", path="docs", head_limit=20), fast)
            assert ".md" in out

        _run(scenario())


class TestIndexOnThisRepository:
    def test_code_index_answers_in_milliseconds_once_warm(self) -> None:
        tool = CodeIndexTool(workspace=ROOT)

        async def scenario() -> None:
            # A first-ever build of this repo (3 600 parsed files): once per
            # project, in worker processes. The fork of a large engine process
            # is the one moment the loop may pause longer than a lookup.
            await _measure("code_index warm-up (ensure)", lambda: tool.execute(action="overview"), INDEX_COLD_S, loop_budget_s=0.400, tries=1)
            out = await _measure("code_index definition", lambda: tool.execute(action="definition", name="SubagentManager"), INSTANT_S)
            assert "navin/agent/subagent.py" in out
            # References are the second one-time cost of a project (millions of
            # recorded uses loaded once); the warm started with the first call.
            out = await _measure("code_index references (first)", lambda: tool.execute(action="references", name="allowed_tools_for", limit=20), 5.0, loop_budget_s=0.600, tries=1)
            assert "team.py" in out
            out = await _measure("code_index references", lambda: tool.execute(action="references", name="allowed_tools_for", limit=20), INSTANT_S)
            assert "team.py" in out
            out = await _measure("code_index callers", lambda: tool.execute(action="callers", name="_outcomes_dir"), INSTANT_S)
            assert "subagent" in out
            out = await _measure("code_index search", lambda: tool.execute(action="search", query="ExecSessionMan", limit=10), INSTANT_S)
            assert "ExecSessionManager" in out
            out = await _measure("code_index outline", lambda: tool.execute(action="outline", path="navin/agent/tools/search.py"), INSTANT_S)
            assert "GrepTool" in out
            # Full text (tantivy) is the third one-time index of a project; the
            # background warm started with the first call builds it meanwhile.
            out = await _measure("code_index text (first)", lambda: tool.execute(action="text", query="blocking call event loop", limit=10), 5.0, loop_budget_s=0.400, tries=1)
            assert out
            out = await _measure("code_index text", lambda: tool.execute(action="text", query="exec session deadline", limit=10), FAST_S)
            assert out

        _run(scenario())

    def test_metagraph_answers_in_milliseconds(self, loaded_repo_index: None) -> None:
        tool = MetagraphTool(workspace=ROOT)

        async def scenario() -> None:
            # The first call builds the file graph of the whole repository
            # (5 600 nodes, 12 000 edges) once; the ones after answer from it.
            out = await _measure("metagraph overview (first)", lambda: tool.execute(action="overview"), COLD_S, tries=1)
            assert "back" in out
            out = await _measure("metagraph overview", lambda: tool.execute(action="overview"), INSTANT_S)
            assert "back" in out
            out = await _measure("metagraph find", lambda: tool.execute(action="find", query="subagent"), INSTANT_S)
            assert "subagent" in out
            out = await _measure("metagraph file", lambda: tool.execute(action="file", path="navin/agent/subagent.py"), FAST_S)
            assert "subagent" in out

        _run(scenario())


class TestGrepWithoutFastBackends:
    """No Rust scanner, no ripgrep (a bare install, every CI runner): grep stays fast.

    ``git grep`` takes over: C speed over the files git sees. Below it, the
    pure-Python scan used to walk the whole tree (137 000 entries here, of
    which 131 000 build output), ask git about every one of them and loop over
    each line of each file: 14 s per grep on this repository. It now takes the
    file list from git and scans whole files, so it stays under a second.
    """

    def test_git_grep_backend_answers_like_the_fast_ones(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import navin.agent.tools.search as search

        monkeypatch.setattr(search, "_native_module", lambda: None)
        monkeypatch.setattr(search, "_rg_binary", lambda: None)
        grep = GrepTool(workspace=ROOT)

        async def scenario() -> None:
            out = await _measure("git-grep identifier (cold)", lambda: grep.execute(pattern="SubagentManager"), COLD_S, tries=1)
            assert "navin/agent/subagent.py" in out
            out = await _measure("git-grep identifier (warm)", lambda: grep.execute(pattern="SubagentManager"), FAST_S)
            assert "navin/agent/subagent.py" in out
            out = await _measure(
                "git-grep regex scoped", lambda: grep.execute(pattern=r"async def (spawn|retry)\(", path="navin/agent", output_mode="content"), FAST_S,
            )
            assert "subagent.py" in out and "async def spawn(" in out
            out = await _measure(
                "git-grep literal glob ci", lambda: grep.execute(pattern="BLOCKING-OK", fixed_strings=True, case_insensitive=True, glob="**/*.py"), FAST_S,
            )
            assert "builtin.py" in out
            out = await _measure("git-grep no match", lambda: grep.execute(pattern="zzqx_never_" + "in_repo_7731"), FAST_S)
            assert "No matches" in out

        _run(scenario())

    def test_git_grep_and_pure_python_report_the_same_hits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A faster backend is only acceptable if it finds exactly what the reference finds."""
        import navin.agent.tools.search as search

        monkeypatch.setattr(search, "_native_module", lambda: None)
        monkeypatch.setattr(search, "_rg_binary", lambda: None)
        grep = GrepTool(workspace=ROOT)

        async def both(**kw: Any) -> tuple[str, str]:
            monkeypatch.setattr(search, "_git_grep_available", lambda: True)
            fast = str(await grep.execute(**kw))
            monkeypatch.setattr(search, "_git_grep_available", lambda: False)
            slow = str(await grep.execute(**kw))
            return fast, slow

        def hits(text: str) -> set[str]:
            return {line.strip() for line in text.split("\n") if line.strip().startswith(("navin/", "tests/", "scripts/"))}

        async def scenario() -> None:
            for kw in (
                {"pattern": "COLLAB_TOOLS_VERSION", "path": "navin"},
                {"pattern": r"def _line_window\(", "output_mode": "content"},
                {"pattern": "blocking-ok", "fixed_strings": True, "glob": "**/*.py"},
                {"pattern": "forget_agents_cache", "case_insensitive": True, "path": "navin/agent"},
            ):
                fast, slow = await both(**kw)
                assert hits(fast) == hits(slow), f"{kw}: git grep and Python disagree\n{fast[:500]}\n---\n{slow[:500]}"

        _run(scenario())

    def test_pure_python_grep_on_this_repository_stays_fast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import navin.agent.tools.search as search

        monkeypatch.setattr(search, "_native_module", lambda: None)
        monkeypatch.setattr(search, "_rg_binary", lambda: None)
        monkeypatch.setattr(search, "_git_grep_available", lambda: False)
        grep = GrepTool(workspace=ROOT)
        find = FindFilesTool(workspace=ROOT)

        async def scenario() -> None:
            out = await _measure("fallback grep identifier", lambda: grep.execute(pattern="SubagentManager"), 1.0)
            assert "navin/agent/subagent.py" in out
            out = await _measure("fallback grep glob", lambda: grep.execute(pattern="SubagentManager", glob="**/*.py"), 1.0)
            assert "navin/agent/subagent.py" in out
            out = await _measure(
                "fallback grep lookahead", lambda: grep.execute(pattern=r"def (?=_outcomes)\w+", output_mode="content"), 1.0,
            )
            assert "_outcomes_dir" in out
            # No byte prefilter for a case-insensitive search (Unicode folding
            # is not a byte operation): every file is decoded, still bounded.
            out = await _measure(
                "fallback grep case-insensitive", lambda: grep.execute(pattern="subagentmanager", case_insensitive=True), 3.0,
            )
            assert "navin/agent/subagent.py" in out
            out = await _measure("fallback grep no match", lambda: grep.execute(pattern="zzqx_never_" + "in_repo_7731"), 1.0)
            assert "No matches" in out
            out = await _measure("fallback find_files glob", lambda: find.execute(glob="tests/test_tool_*.py"), FAST_S)
            assert "test_tool_latency.py" in out
            out = await _measure("fallback find_files query", lambda: find.execute(query="subagent journal"), FAST_S)
            assert "subagent_journal" in out

        _run(scenario())

    def test_required_literal_never_loses_a_match(self) -> None:
        """The byte prefilter is a pure speed-up: every literal it returns is in every match."""
        from navin.agent.tools.search import _required_literal as literal

        assert literal("SubagentManager", fixed_strings=False, case_insensitive=False) == b"SubagentManager"
        assert literal("blocking-ok", fixed_strings=True, case_insensitive=False) == b"blocking-ok"
        assert literal(r"async def (spawn|retry)\(", fixed_strings=False, case_insensitive=False) == b"async def "
        assert literal("^from navin", fixed_strings=False, case_insensitive=False) == b"from navin"
        # A quantifier applies to the character before it: that character is not required.
        assert literal("abc*", fixed_strings=False, case_insensitive=False) == b"ab"
        assert literal("ab?c", fixed_strings=False, case_insensitive=False) is None  # "a" alone is too short
        # No certainty, no filter: escapes, classes, groups, single chars, case folding.
        assert literal(r"\bfoo", fixed_strings=False, case_insensitive=False) is None
        assert literal("[Ss]ubagent", fixed_strings=False, case_insensitive=False) is None
        assert literal("(a|b)", fixed_strings=False, case_insensitive=False) is None
        assert literal("a", fixed_strings=False, case_insensitive=False) is None
        # Case-insensitive: the lowercase ASCII literal, compared against lowercased
        # bytes; a non-ASCII letter has case pairs with other bytes, so no filter.
        assert literal("Kelvin", fixed_strings=False, case_insensitive=True) == b"kelvin"
        assert literal("École", fixed_strings=True, case_insensitive=True) is None
        assert literal("", fixed_strings=True, case_insensitive=False) is None

    def test_case_insensitive_prefilter_never_drops_a_unicode_fold(self, tmp_path: Path) -> None:
        """Python's regex folds the long s (U+017F) to ``s`` and the Kelvin sign
        (U+212A) to ``k``: a file matching only through them keeps the full pass."""
        import re

        from navin.agent.tools.search import _read_text_file, _required_literal

        needle = _required_literal("SubagentManager", fixed_strings=False, case_insensitive=True)
        assert needle == b"subagentmanager"
        plain = tmp_path / "plain.txt"
        plain.write_text("x\nSUBAGENTMANAGER here\n", encoding="utf-8")
        miss = tmp_path / "miss.txt"
        miss.write_text("nothing\n", encoding="utf-8")
        long_s = tmp_path / "long_s.txt"
        long_s.write_text("\u017fubagentmanager via long s\n", encoding="utf-8")
        kelvin = tmp_path / "kelvin.txt"
        kelvin.write_text("\u212aelvin\n", encoding="utf-8")
        def read(p: Path) -> tuple[str | None, str | None]:
            return _read_text_file(p, 10**6, must_contain=needle, fold_case=True)

        assert read(plain)[0] is not None
        assert read(miss) == (None, "miss")
        assert read(long_s)[0] is not None, "the long s file must reach the regex"
        assert re.search("subagentmanager", long_s.read_text(encoding="utf-8"), re.I)
        assert read(kelvin)[0] is not None


class TestAgentScenarioFindAndEdit:
    """The user's bar: the agent finds the file or the code in milliseconds, then edits it.

    One index-first grep for a symbol gives its file and line; code_index gives
    the callers to check; the edit lands; the index sees the new symbol at once.
    """

    def test_find_definition_callers_and_edit_under_budget(self, big_workspace: Path) -> None:
        grep = GrepTool(workspace=big_workspace)
        tool = CodeIndexTool(workspace=big_workspace)

        async def scenario() -> None:
            await tool.execute(action="overview")  # the index of this workspace is loaded
            started = time.perf_counter()
            found = await grep.execute(pattern="handler_777")
            assert found.startswith("Index: 1 definition(s) of 'handler_777'"), found[:200]
            first = found.split("\n")[1]
            assert "pkg/mod57/unit_0777.py" in first and ":4" in first, first
            callers = await tool.execute(action="callers", name="handler_777")
            assert "Service777" in callers or "unit_0777.py" in callers, callers[:300]
            located = time.perf_counter() - started
            assert located < FAST_S, f"locating the code took {located * 1000:.0f} ms"

            target = big_workspace / "pkg" / "mod57" / "unit_0777.py"
            target.write_text(target.read_text().replace("def handler_777(", "def handler_777_renamed("), encoding="utf-8")
            started = time.perf_counter()
            out = await tool.execute(action="definition", name="handler_777_renamed")
            assert "unit_0777.py" in out, out[:300]
            gone = await tool.execute(action="definition", name="handler_777")
            assert "No exact definition" in gone or "No definition found" in gone, gone[:300]
            seen = time.perf_counter() - started
            assert seen < FAST_S, f"the index took {seen * 1000:.0f} ms to see the edit"

        _run(scenario())


class TestFulltextWriterNeverBlocksAQuery:
    """A text query returns at once while the background warm holds the writer.

    Measured before the fix: a first `code_index action=text` took 2.97 s because
    it waited up to 3 s on the writer lock the warm was holding, then searched.
    """

    def test_query_refresh_returns_immediately_when_the_writer_is_busy(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import threading

        from navin.index import fulltext

        class _SlowCore:
            calls = 0

            def fulltext_update(self, index_dir: str, root: str, changed: list, removed: list) -> int:
                _SlowCore.calls += 1
                time.sleep(0.4)
                return len(changed)

            def fulltext_search(self, index_dir: str, query: str, limit: int) -> str:
                return "[]"

        core = _SlowCore()
        monkeypatch.setattr(fulltext, "_native", lambda: core)
        index = fulltext.FulltextIndex(tmp_path)
        files = {"a.py": 1, "b.py": 2}

        warm = threading.Thread(target=index.refresh, args=(files,), daemon=True)
        warm.start()
        time.sleep(0.05)  # the warm holds the writer now
        started = time.perf_counter()
        indexed = index.refresh(files, wait=False)
        elapsed = time.perf_counter() - started
        warm.join(timeout=5)
        assert indexed == 0, "a query never indexes while the warm is writing"
        assert elapsed < 0.05, f"query refresh waited {elapsed * 1000:.0f} ms on the writer"
        # The waiting form still serializes: the warm's update landed, once.
        assert _SlowCore.calls == 1
        assert index.refresh(files) == 0, "already current after the warm"
        assert _SlowCore.calls == 1


class TestSearchAtScale:
    def test_grep_and_find_on_three_thousand_files(self, big_workspace: Path) -> None:
        grep = GrepTool(workspace=big_workspace)
        find = FindFilesTool(workspace=big_workspace)

        async def scenario() -> None:
            out = await _measure("scale grep identifier (cold)", lambda: grep.execute(pattern="handler_1234"), COLD_S, tries=1)
            assert "unit_1234.py" in out
            out = await _measure("scale grep identifier (warm)", lambda: grep.execute(pattern="handler_2345"), FAST_S)
            assert "unit_2345.py" in out
            out = await _measure("scale grep common word", lambda: grep.execute(pattern="needle_42", path="pkg"), FAST_S)
            assert "unit_" in out
            out = await _measure("scale grep big file", lambda: grep.execute(pattern="line 0399999", path="big.log", output_mode="content"), FAST_S)
            assert "0399999" in out
            out = await _measure("scale find glob", lambda: find.execute(glob="pkg/**/unit_01*.py", head_limit=200), FAST_S)
            assert "unit_0100.py" in out

        _run(scenario())

    def test_code_index_at_scale(self, big_workspace: Path) -> None:
        tool = CodeIndexTool(workspace=big_workspace)

        async def scenario() -> None:
            await _measure("scale code_index build", lambda: tool.execute(action="refresh"), INDEX_COLD_S, loop_budget_s=0.400, tries=1)
            out = await _measure("scale code_index definition", lambda: tool.execute(action="definition", name="handler_1234"), INSTANT_S)
            assert "unit_1234.py" in out
            out = await _measure("scale code_index search", lambda: tool.execute(action="search", query="Service12", limit=20), INSTANT_S)
            assert "Service12" in out
            # Edit one file: the next lookup must see it without a full rebuild.
            target = big_workspace / "pkg" / "mod00" / "unit_0000.py"
            target.write_text(target.read_text() + "\n\ndef brand_new_symbol_xyz() -> None:\n    return None\n", encoding="utf-8")
            out = await _measure("scale code_index after one edit", lambda: tool.execute(action="definition", name="brand_new_symbol_xyz"), FAST_S * 3, tries=1)
            assert "unit_0000.py" in out
            # The derived maps were patched for that one file, not rebuilt from
            # all 3 000: the second edit answers in a few tens of milliseconds,
            # and a new file imported by an existing one gets its edge.
            other = big_workspace / "pkg" / "mod01" / "unit_0001.py"
            other.write_text(other.read_text() + "\n\ndef second_edit_symbol_qq() -> None:\n    return None\n", encoding="utf-8")
            out = await _measure("scale code_index after second edit", lambda: tool.execute(action="definition", name="second_edit_symbol_qq"), INSTANT_S, tries=1)
            assert "unit_0001.py" in out
            fresh = big_workspace / "pkg" / "mod02" / "brand_new_module.py"
            fresh.write_text("def in_new_module() -> int:\n    return 3\n", encoding="utf-8")
            importer = big_workspace / "pkg" / "mod02" / "unit_0002.py"
            importer.write_text("from pkg.mod02.brand_new_module import in_new_module\n" + importer.read_text(), encoding="utf-8")
            out = await _measure("scale code_index new file + import", lambda: tool.execute(action="file", path="pkg/mod02/unit_0002.py"), FAST_S, tries=1)
            assert "brand_new_module.py" in out, out[:600]
            # grep for that identifier now answers from the index first, text second.
            grep = GrepTool(workspace=big_workspace)
            out = await _measure("scale grep index-first", lambda: grep.execute(pattern="brand_new_symbol_xyz"), FAST_S)
            assert out.startswith("Index: 1 definition(s) of 'brand_new_symbol_xyz'")
            first_lines = out.split("\n")
            assert "pkg/mod00/unit_0000.py" in first_lines[1], first_lines[:3]
            assert first_lines.count("pkg/mod00/unit_0000.py") == 1, "the text match still follows the index header"

        _run(scenario())


def test_print_table(capsys: pytest.CaptureFixture[str]) -> None:
    with capsys.disabled():
        if RESULTS:
            print("\n{:<36} {:>12} {:>10}".format("search call", "loop gap ms", "wall ms"))
            for label, gap, wall in RESULTS:
                print(f"{label:<36} {gap * 1000:>12.1f} {wall * 1000:>10.1f}")
