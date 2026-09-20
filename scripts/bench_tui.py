#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Measure CLI input and navigation while tools and Markdown stream.

Run with: python scripts/bench_tui.py --repeat 3 --check --json result.json
Add --subagents 200 to measure a simultaneous wave of durable results.
Uses the real Textual app in headless mode and an isolated temporary config.
It measures UI frames, not terminal hardware, network or model response time.
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import gc
import json
import math
import platform
import statistics
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger  # noqa: E402
from textual import events  # noqa: E402

from navin.bus.queue import MessageBus  # noqa: E402
from navin.config.loader import get_config_path, set_config_path  # noqa: E402
from navin.tui.app import NavinApp  # noqa: E402
from navin.tui.prefs import TuiPrefs  # noqa: E402
from navin.tui.screens import PickerScreen, PickItem  # noqa: E402
from navin.tui.widgets import Sidebar  # noqa: E402


class LoadApp(NavinApp):
    """Keep the real controls, bus and persistence; synthesize engine output."""

    async def on_mount(self, event):
        event.prevent_default()
        self.runtime.bus = MessageBus()
        self._engine_ready = True
        self.query_one(Sidebar).display = False
        self.composer.focus()
        self.set_interval(0.12, self._tick_spinner)

    async def on_unmount(self, event):
        event.prevent_default()
        await self._flush_unsent_work()
        self.runtime._closed = True

    def _refresh_side(self):
        self._set_status()

    def _load_account(self, *args, **kwargs):
        pass


def percentiles(samples: list[float]) -> dict:
    ordered = sorted(samples)
    return {
        "samples": len(ordered),
        "p50_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 2),
        "max_ms": round(ordered[-1], 2),
    }


async def measure(root: Path, keys: int, history_tools: int = 0, width: int = 120,
                  tools: int = 6, subagents: int = 0) -> dict:
    set_config_path(root / "config.json")
    prefs = TuiPrefs(sidebar=False, mode="chat", mode_explicit=True)
    app = LoadApp(SimpleNamespace(workspace_path=root), prefs=prefs)
    app.theme = "navin"
    async with app.run_test(size=(width, 42)) as pilot:
        await app.submit_text("Continue working while I type and navigate.")
        await app.runtime.bus.consume_inbound()
        block = await app._ensure_assistant()
        await block.reveal()
        for index in range(history_tools):
            await block.tool_event(
                f"history-{index}", "exec", "end", {"command": f"check step_{index}"},
                "6 passed\nExit code: 0", None, None,
            )
        rows = []
        seed = "".join(f"case_{i:04d} passed: full output retained\n" for i in range(5000))
        for index in range(tools):
            await block.tool_event(str(index), "exec", "start", {"command": f"pytest audit_{index}.py"},
                                   None, None, None)
            row = block._tools[str(index)]
            row.apply(phase="output", output=seed)
            row.toggle()
            rows.append(row)
        await pilot.pause()
        samples = {name: [] for name in ("input", "scheduled_input", "open", "close", "loop")}
        display = app._display
        target = app.screen
        expected = ""
        painted = asyncio.Event()
        painted_at = 0.0
        chunks = []
        active = True
        last_output = ""
        completions = []
        manager = None
        if subagents:
            from navin.agent.subagent import SubagentManager

            manager = SubagentManager(workspace=root, bus=MessageBus(inbound_maxsize=0),
                                      max_tool_result_chars=4000)

        async def complete_subagents():
            # Exercise the real result persistence and parent notification
            # path, without network requests or paid model invocations.
            await asyncio.sleep(0.1)
            if manager is not None:
                await asyncio.gather(*(manager._announce_result(
                    str(index), f"Worker {index}", "Check an independent file",
                    f"Result {index}: verified. " * 40,
                    {"channel": "cli", "chat_id": "bench", "session_key": "cli:bench"}, "ok",
                ) for index in range(subagents)))
                completions.extend(manager.recent_outcomes("cli:bench"))

        def observe(screen, frame):
            nonlocal painted_at
            display(screen, frame)
            if frame is None or app._batch_count or screen is not target or painted.is_set():
                return
            if expected:
                content = "".join(segment.text for segment in app.console.render(frame) if not segment.control)
                if expected not in content:
                    return
            # Timestamp the accepted frame itself. Waiting for the measuring
            # coroutine to resume would include unrelated work after paint.
            painted_at = time.perf_counter()
            painted.set()

        app._display = observe

        async def output():
            nonlocal last_output
            index = 0
            while active:
                chunk = f"Verification {index}: resultat conserve.\n\n"
                chunks.append(chunk)
                await block.delta(chunk)
                last_output = f"case_live_{index}: passed"
                for row in rows:
                    row.apply(phase="output", output=last_output + "\n")
                index += 1
                await asyncio.sleep(0.03)

        async def heartbeat():
            previous = time.perf_counter()
            while active:
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                samples["loop"].append(max(0, now - previous - 0.01) * 1000)
                previous = now

        producer = asyncio.create_task(output())
        ticker = asyncio.create_task(heartbeat())
        fan_in = asyncio.create_task(complete_subagents())
        typed = ""
        try:
            for index in range(keys):
                target = app.screen
                character = chr(ord("a") + index % 26)
                typed += character
                expected = typed
                painted.clear()
                # Account separately for an input arrival delayed by a busy
                # event loop, as well as dispatch-to-frame latency.
                scheduled = time.perf_counter() + 0.04
                await asyncio.sleep(0.04)
                started = time.perf_counter()
                app.composer.post_message(events.Key(character, character))
                await asyncio.wait_for(painted.wait(), 5)
                finished = painted_at
                samples["input"].append((finished - started) * 1000)
                # Windows' asyncio clock may wake a timer early. Count only
                # lateness, without subtracting it from actual input work.
                samples["scheduled_input"].append(
                    (finished - started + max(0, started - scheduled)) * 1000
                )
                if index % 10 == 9:
                    expected = "Sessions"
                    previous = app.screen
                    target = PickerScreen("Sessions", [PickItem(str(i), f"Session {i}") for i in range(250)])
                    painted.clear()
                    started = time.perf_counter()
                    await app.push_screen(target)
                    await asyncio.wait_for(painted.wait(), 5)
                    samples["open"].append((painted_at - started) * 1000)
                    target = previous
                    expected = typed
                    painted.clear()
                    started = time.perf_counter()
                    app.pop_screen()
                    await asyncio.wait_for(painted.wait(), 5)
                    samples["close"].append((painted_at - started) * 1000)
        finally:
            active = False
            await asyncio.gather(producer, ticker, fan_in)
        await block.finish(latency_ms=1, model=None, preset=None)
        assert app.composer.text == typed, "Input was lost or reordered"
        assert block.text == "".join(chunks), "Streamed response was lost or reordered"
        for row in rows:
            copied = row.copy_text()
            assert last_output in copied, f"Latest tool output was lost: {last_output!r}; buffer tail {row.output_lines[-2:]!r}; copy tail {copied[-200:]!r}"
        await app._flush_unsent_work()
        assert app._session_store.load(app.runtime.session_key)["draft"] == typed, "Draft was not saved"
        if manager is not None:
            assert len(completions) == subagents, "Subagent results were lost"
            assert manager.bus.inbound_size == subagents, "Parent notifications were lost"
        return {"samples": samples, "stream_characters": len(block.text)}


def main() -> int:
    # Match navin-cli's default: engine logs must not write to the terminal
    # while the app owns it. Verbose logging is a separate workload.
    logger.disable("navin")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--keys", type=int, choices=range(10, 81), default=50, metavar="10..80")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--profile", type=Path, help="Write cProfile data for a separate diagnostic run")
    parser.add_argument("--history-tools", type=int, default=0, help="Completed commands before the measured stream")
    parser.add_argument("--width", type=int, default=120, help="Terminal columns")
    parser.add_argument("--tools", type=int, default=6, help="Concurrent streaming tool outputs")
    parser.add_argument("--subagents", type=int, default=0, help="Subagent results completing together")
    parser.add_argument("--check", action="store_true", help="Require input p95 <= 50 ms, worst input <= 150 ms and navigation p95 <= 100 ms")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    if args.history_tools < 0 or args.width < 40:
        parser.error("--history-tools must be nonnegative and --width must be at least 40")
    if args.tools < 1 or args.subagents < 0:
        parser.error("--tools must be positive and --subagents must be nonnegative")
    original_config = get_config_path()
    runs = []
    profiler = cProfile.Profile() if args.profile else None
    if profiler is not None:
        profiler.enable()
    try:
        for _ in range(args.repeat):
            # Each run represents a fresh CLI. Dispose of the previous
            # headless app's cyclic render caches outside the measured load.
            gc.collect()
            with tempfile.TemporaryDirectory(prefix="navin-tui-bench-") as directory:
                runs.append(asyncio.run(measure(
                    Path(directory), args.keys, args.history_tools, args.width, args.tools, args.subagents,
                )))
    finally:
        set_config_path(original_config)
        if profiler is not None:
            profiler.disable()
            profiler.dump_stats(str(args.profile))
    metrics = {name: percentiles([sample for run in runs for sample in run["samples"][name]])
               for name in runs[0]["samples"]}
    passed = (metrics["input"]["p95_ms"] <= 50 and metrics["scheduled_input"]["p95_ms"] <= 50
              and metrics["scheduled_input"]["max_ms"] <= 150
              and metrics["open"]["p95_ms"] <= 100
              and metrics["close"]["p95_ms"] <= 100)
    report = {
        "passed": passed,
        "scope": "Headless UI dispatch to rendered frame; hardware and engine/network latency excluded",
        "platform": platform.platform(), "python": platform.python_version(), "textual": version("textual"),
        "load": {"tools": args.tools, "subagent_completions": args.subagents,
                 "retained_lines_per_tool": 5000, "output_interval_ms": 30,
                 "history_tools": args.history_tools,
                 "session_menu_items": 250, "terminal": [args.width, 42], "keys_per_run": args.keys,
                 "runs": len(runs)},
        "metrics": metrics,
        "stream_characters_per_run": [run["stream_characters"] for run in runs],
    }
    output = json.dumps(report, indent=2)
    print(output)
    if args.json:
        args.json.write_text(output + "\n", encoding="utf-8")
    return 1 if args.check and not passed else 0


if __name__ == "__main__":
    raise SystemExit(main())
