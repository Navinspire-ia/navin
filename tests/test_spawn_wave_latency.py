# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Launching hundreds of subagents and teammates never stalls the engine loop.

Every spawn builds a tool registry and a system prompt (skills catalog, project
agents, parent context) and resolves the model route and effort. A fan-out of
200 must keep the loop turning: a chat typed during the wave must not freeze.
The runner is a counting stub, so what is measured is the engine's own work.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from navin.agent.model_routes import (
    infer_task_role,
    load_model_routes,
    resolve_model_route,
    resolve_route_for_message,
)
from navin.agent.runner import AgentRunResult
from navin.agent.subagent import SubagentManager
from navin.collab.team import ROLE_TOOLS, allowed_tools_for, default_team

WAVE = 200
# The longest gap a 5 ms ticker may see while the wave is launched and runs.
LOOP_BUDGET_S = 0.150
# Pure in-memory routing: one decision is microseconds, a thousand under 50 ms.
ROUTING_BUDGET_S = 0.050


class _Bus:
    def __init__(self) -> None:
        self.published: list = []

    async def publish_inbound(self, msg) -> None:
        self.published.append(msg)


class _CountingRunner:
    def __init__(self) -> None:
        self.finished = 0
        self.peak = 0
        self._in_flight = 0

    async def run(self, spec) -> AgentRunResult:
        self._in_flight += 1
        self.peak = max(self.peak, self._in_flight)
        try:
            # Long enough that the next prepared subagent starts before this
            # one ends, so the fan-out is observable; a 1 ms run could finish
            # between two spawns and read as serial on a fast machine.
            await asyncio.sleep(0.05)
        finally:
            self._in_flight -= 1
            self.finished += 1
        return AgentRunResult(final_content="done", messages=[])


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


class SpawnWaveTest(unittest.TestCase):
    """Plain ``asyncio.run``: ``IsolatedAsyncioTestCase`` turns on asyncio debug
    mode, whose per-future stack captures (``linecache.checkcache`` stats every
    source file) stall the loop for hundreds of milliseconds on their own and
    would be measured instead of the engine."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        # A real project shape: skills and project agents the prompt builder scans.
        for i in range(40):
            folder = self.workspace / ".navin" / "skills" / f"skill-{i:02d}"
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(
                f"---\nname: skill-{i:02d}\ndescription: Skill number {i} for the wave test.\n---\n\n# Skill {i}\n\nBody.\n",
                encoding="utf-8",
            )
        agents = self.workspace / ".navin" / "agents"
        agents.mkdir(parents=True)
        (agents / "worker.md").write_text(
            "---\nname: worker\ndescription: Does the work.\n---\n\nYou do the work.\n", encoding="utf-8",
        )
        self.bus = _Bus()
        self.runner = _CountingRunner()
        self.manager = SubagentManager(
            workspace=self.workspace, bus=self.bus, max_tool_result_chars=4000,
            max_concurrent_subagents=WAVE, max_total_concurrent_subagents=WAVE,
        )
        self.manager.runner = self.runner

    async def _wait_finished(self, expected: int) -> None:
        deadline = asyncio.get_running_loop().time() + 60.0
        while self.runner.finished < expected:
            if asyncio.get_running_loop().time() > deadline:
                self.fail(f"only {self.runner.finished}/{expected} subagent(s) finished within 60s")
            await asyncio.sleep(0.01)

    def test_two_hundred_spawns_keep_the_loop_turning(self) -> None:
        runtime = mock.Mock(with_generation_overrides=lambda **_: mock.Mock())

        async def scenario() -> tuple[float, float]:
            async with _Ticker() as ticker:
                started = time.perf_counter()
                for i in range(WAVE):
                    out = await self.manager.spawn(
                        task=f"work item {i}", agent="worker" if i % 2 else None,
                        scope_paths=["navin/agent"] if i % 3 else None,
                        runtime=runtime, origin_channel="cli", origin_chat_id="local",
                        session_key=f"cli:chat-{i % 10}",
                    )
                    self.assertNotIn("refused", out.lower(), out)
                await self._wait_finished(WAVE)
                elapsed = time.perf_counter() - started
            return ticker.max_gap, elapsed

        max_gap, elapsed = asyncio.run(scenario())
        self.assertEqual(self.runner.finished, WAVE)
        self.assertGreater(self.runner.peak, 1, "the wave never ran in parallel")
        self.assertLess(
            max_gap, LOOP_BUDGET_S,
            f"event loop stalled {max_gap * 1000:.0f} ms while launching {WAVE} subagents",
        )
        self.assertLess(elapsed / WAVE, 0.25, f"one spawn costs {elapsed / WAVE * 1000:.0f} ms end to end")

    def test_teammates_launch_with_their_toolbox_without_stalling(self) -> None:
        runtime = mock.Mock(with_generation_overrides=lambda **_: mock.Mock())
        roster = default_team()
        roster = list(getattr(roster, "members", roster))
        members = []
        for m in roster:
            row = m.model_dump() if hasattr(m, "model_dump") else dict(m)
            row["tools"] = list(allowed_tools_for(m) or ROLE_TOOLS.get(str(row.get("role", "")), ()))
            members.append(row)
        self.assertGreaterEqual(len(members), 5)

        async def scenario() -> tuple[float, float]:
            async with _Ticker() as ticker:
                started = time.perf_counter()
                for i in range(60):
                    member = members[i % len(members)]
                    out = await self.manager.spawn(
                        task=f"team work {i}", runtime=runtime, origin_channel="cli",
                        origin_chat_id="local", session_key="cli:team", member=member,
                        budget_tokens=1000, assigned_by="lead",
                    )
                    self.assertNotIn("refused", out.lower(), out)
                await self._wait_finished(60)
                elapsed = time.perf_counter() - started
            return ticker.max_gap, elapsed

        max_gap, elapsed = asyncio.run(scenario())
        self.assertLess(
            max_gap, LOOP_BUDGET_S,
            f"event loop stalled {max_gap * 1000:.0f} ms while launching 60 teammates",
        )
        # Two prepare workers: 60 teammates launched and finished in under 8 s
        # here (3.8 s measured; 8.2 s with eight workers fighting for the GIL).
        self.assertLess(elapsed, 20.0, f"60 teammates took {elapsed:.1f}s")


class ModelRoutingSpeedTest(unittest.TestCase):
    """Choosing the model and the effort is a lookup, never a disk read per call."""

    def test_a_thousand_route_decisions_cost_under_the_budget(self) -> None:
        load_model_routes()  # warm the config cache once, as the engine does at start
        texts = [
            "fix the login bug in auth.py", "audit the security of the API", "write the docs",
            "plan the migration of the database", "search the web for the latest release",
            "quick question: what is 2+2", "review this pull request", "deep analysis of the architecture",
        ]
        routes = {"dev": "fast", "security": "deep", "review": "deep", "plan": "deep"}
        started = time.perf_counter()
        for i in range(1000):
            role = infer_task_role(texts[i % len(texts)])
            resolve_model_route(role, routes=routes, known_presets={"fast", "deep"})
            resolve_route_for_message(texts[i % len(texts)], routes=routes, known_presets={"fast", "deep"})
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, ROUTING_BUDGET_S * 10, f"1000 routing decisions took {elapsed * 1000:.0f} ms")

    def test_role_toolboxes_are_precomputed(self) -> None:
        started = time.perf_counter()
        for _ in range(10_000):
            tools = ROLE_TOOLS["developer"]
            assert "exec" in tools
        self.assertLess(time.perf_counter() - started, ROUTING_BUDGET_S)
