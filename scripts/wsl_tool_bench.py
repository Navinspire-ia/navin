"""Time Navin's agent tools and editor panels on a WSL project seen from Windows.

The Windows desktop opens a project that lives in a distribution through
``\\\\wsl.localhost\\<distro>\\...``. Every file call there crosses the 9P bridge,
so a tool that walks or reads the tree from Windows can take minutes while the
same work inside the distribution takes a fraction of a second. This script
runs each tool the way the desktop does and prints how long it took, so a slow
path shows up as a number instead of a frozen chat.

Run it on the Windows host, with this source tree on PYTHONPATH:

    python scripts/wsl_tool_bench.py \\\\wsl.localhost\\Ubuntu\\home\\me\\project
        [--scratch build/navin-wsl-bench] [--budget 2.0] [--json out.json]

Write tools only touch files under ``--scratch`` (relative to the project; use
a git-ignored folder). The exit code is 1 when a warm interactive call is over
budget, so the script can gate a release.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Calls an agent makes inside a turn, where a user is waiting on the answer.
# Heavy ones (first index build, a test run) are reported but not gated.
_HEAVY = {"code_index:overview", "metagraph:overview", "verify:check", "test_run:detect"}


def _isolate_home() -> str:
    """Point Navin's config and caches at a temporary home for the run."""
    home = tempfile.mkdtemp(prefix="navin-wsl-bench-home-")
    os.environ["USERPROFILE"] = home
    os.environ["HOME"] = home
    return home


def _cases(scratch: str) -> list[tuple[str, str, dict[str, Any]]]:
    note = f"{scratch}/note.txt"
    return [
        ("read_file", "readme", {"path": "README.md", "limit": 40}),
        ("list_dir", "root", {"path": "."}),
        ("list_dir", "recursive", {"path": ".", "recursive": True, "max_entries": 300}),
        ("find_files", "glob", {"glob": "*.md", "head_limit": 50}),
        ("find_files", "query", {"query": "config", "head_limit": 50}),
        ("grep", "literal", {"pattern": "import", "head_limit": 30}),
        ("grep", "two patterns", {"patterns": ["TODO", "FIXME"], "output_mode": "count"}),
        ("write_file", "scratch", {"path": note, "content": "alpha\nbeta\n"}),
        ("read_file", "scratch", {"path": note}),
        ("edit_file", "scratch", {"path": note, "old_text": "beta", "new_text": "gamma"}),
        ("apply_patch", "scratch", {"edits": [
            {"path": note, "action": "replace", "old_text": "gamma", "new_text": "delta"},
        ]}),
        ("manage_files", "copy", {"action": "copy", "path": note, "destination": f"{scratch}/copy.txt",
                                  "overwrite": True}),
        ("manage_files", "delete", {"action": "delete", "paths": [f"{scratch}/copy.txt"]}),
        ("exec", "echo", {"command": "echo ok"}),
        ("exec", "ls", {"command": "ls | head -5"}),
        ("git", "status", {"action": "status"}),
        ("git", "log", {"action": "log", "limit": 5}),
        ("git", "diff stat", {"action": "diff", "stat": True}),
        ("code_index", "overview", {"action": "overview"}),
        ("code_index", "search", {"action": "search", "query": "main"}),
        ("metagraph", "overview", {"action": "overview"}),
        ("lint", "available", {"action": "available"}),
        ("lint", "file", {"action": "file", "path": note}),
        ("lint", "python file", {"action": "file", "path": "navin/utils/wsl_bulk.py"}),
        ("verify", "check", {"action": "check", "paths": [note], "with_tests": False}),
        ("test_run", "detect", {"action": "detect"}),
        ("test_run", "one file", {"action": "run", "target": "tests/test_wsl.py"}),
        ("project", "detect", {"action": "detect"}),
        ("lsp", "servers", {"action": "servers"}),
    ]


async def _time(coro: Any, timeout: float, profile: bool = False) -> tuple[float, str, bool]:
    started = time.perf_counter()
    profiler = None
    if profile:
        import cProfile

        profiler = cProfile.Profile()
        profiler.enable()
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return time.perf_counter() - started, f"timed out after {timeout:.0f}s", False
    except Exception as exc:  # noqa: BLE001 - a benchmark reports, it does not stop
        return time.perf_counter() - started, f"{type(exc).__name__}: {exc}", False
    finally:
        if profiler is not None:
            import pstats

            profiler.disable()
            pstats.Stats(profiler).sort_stats("cumulative").print_stats(25)
    text = str(result)
    return time.perf_counter() - started, text, not text.startswith("Error")


def _panel_cases(workspace: Path) -> list[tuple[str, Any]]:
    """The editor side: file tree, search panel, change watcher, git panel."""
    from navin.security.workspace_access import build_workspace_scope
    from navin.webui import file_tree, fs_watch, project_search

    scope = build_workspace_scope(workspace, "restricted")
    return [
        ("panel:file_tree", lambda: file_tree.file_tree_payload(None, scope=scope)),
        ("panel:search", lambda: project_search.search_payload(scope, "import")),
        ("panel:git_changes", lambda: project_search.git_changes_payload(scope)),
        ("panel:git_log", lambda: project_search.git_log_payload(scope, 20)),
        ("panel:watch_signature", lambda: fs_watch.workspace_signature(workspace)),
    ]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("workspace")
    parser.add_argument("--scratch", default="build/navin-wsl-bench")
    parser.add_argument("--budget", type=float, default=2.0, help="seconds allowed per warm call")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--json", dest="json_out")
    parser.add_argument("--only", help="comma-separated tool names to run")
    parser.add_argument("--profile", action="store_true", help="print the slowest functions of each case")
    args = parser.parse_args()

    # The desktop puts per-user tool folders (winget's ripgrep link) on PATH at
    # startup; do the same before the home is swapped for a scratch one.
    from navin.utils.path_env import augment_path_for_user_tools

    augment_path_for_user_tools()
    _isolate_home()
    if args.profile:
        # cProfile sees one thread: run the tools' to_thread work inline.
        async def _inline(func: Any, /, *a: Any, **k: Any) -> Any:
            return func(*a, **k)

        asyncio.to_thread = _inline  # type: ignore[assignment]
    from navin.agent.tools.context import ToolContext
    from navin.agent.tools.file_state import FileStates
    from navin.agent.tools.loader import ToolLoader
    from navin.agent.tools.registry import ToolRegistry
    from navin.config.schema import ToolsConfig

    workspace = Path(args.workspace)
    ctx = ToolContext(config=ToolsConfig(), workspace=str(workspace), file_state_store=FileStates())
    registry = ToolRegistry()
    ToolLoader().load(ctx, registry, scope="core")
    only = {name.strip() for name in (args.only or "").split(",") if name.strip()}

    rows: list[dict[str, Any]] = []
    over: list[str] = []
    for tool, label, params in _cases(args.scratch):
        if only and tool not in only:
            continue
        key = f"{tool}:{label}"
        if not registry.has(tool):
            rows.append({"case": key, "missing": True})
            print(f"{key:<28} not registered")
            continue
        cold, text, ok = await _time(registry.execute(tool, dict(params)), args.timeout)
        warm, _text, _ok = await _time(registry.execute(tool, dict(params)), args.timeout, args.profile)
        if tool in {"write_file", "edit_file", "apply_patch", "manage_files"}:
            warm = cold  # a second edit is a different call; the first one counts
        rows.append({"case": key, "cold_s": round(cold, 3), "warm_s": round(warm, 3), "ok": ok,
                     "head": text[:160]})
        flag = "" if ok else "  [error]"
        if key not in _HEAVY and warm > args.budget:
            over.append(key)
            flag += "  [over budget]"
        print(f"{key:<28} cold {cold:7.2f}s  warm {warm:7.2f}s{flag}  {text[:90]!r}")

    if not only or "panel" in only:
        for key, call in _panel_cases(workspace):
            cold, text, ok = await _time(asyncio.to_thread(call), args.timeout)
            warm, _text, _ok = await _time(asyncio.to_thread(call), args.timeout, args.profile)
            rows.append({"case": key, "cold_s": round(cold, 3), "warm_s": round(warm, 3), "ok": ok,
                         "head": text[:160]})
            flag = "" if ok else "  [error]"
            if warm > args.budget:
                over.append(key)
                flag += "  [over budget]"
            print(f"{key:<28} cold {cold:7.2f}s  warm {warm:7.2f}s{flag}  {text[:90]!r}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"\n{len(rows)} cases, over budget ({args.budget}s warm): {', '.join(over) or 'none'}")
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
