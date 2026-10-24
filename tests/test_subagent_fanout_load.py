# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Fan-out load: 200 subagents through the real manager and runner.

The provider is fake (no network, no cost) but every subagent goes through the
real admission, queue, journal and runner path. Checks: every spawn is accepted,
the per-session cap is honoured, every one finishes, and the event loop never
stalls long enough to freeze the gateway.
"""

from __future__ import annotations

import asyncio
import os
import queue
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from navin.agent.subagent import SPAWN_REFUSED_PREFIX, SubagentManager
from navin.providers.base import GenerationSettings, LLMResponse
from navin.utils.llm_runtime import LLMRuntime

FANOUT = int(os.environ.get("FANOUT", "200"))
CAP = 50


class _Bus:
    def __init__(self) -> None:
        self.outbound: queue.Queue = queue.Queue()
        self.inbound: list[Any] = []

    async def publish_inbound(self, msg: Any) -> None:
        self.inbound.append(msg)


class _SlowProvider:
    def __init__(self) -> None:
        self.generation = GenerationSettings()
        self.active = 0
        self.peak = 0
        self.calls = 0

    async def _answer(self) -> LLMResponse:
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.05)
        finally:
            self.active -= 1
        return LLMResponse(content="done", finish_reason="stop")

    async def chat_with_retry(self, **_kwargs: Any) -> LLMResponse:
        return await self._answer()

    async def chat_stream_with_retry(self, **_kwargs: Any) -> LLMResponse:
        return await self._answer()


class SubagentFanoutLoadTest(unittest.IsolatedAsyncioTestCase):
    async def test_two_hundred_subagents_queue_run_and_finish(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bus = _Bus()
        provider = _SlowProvider()
        manager = SubagentManager(
            workspace=Path(tmp.name),
            bus=bus,  # type: ignore[arg-type]
            max_tool_result_chars=4000,
            max_concurrent_subagents=CAP,
        )
        runtime = LLMRuntime(
            provider=provider,  # type: ignore[arg-type]
            model="fake",
            generation=GenerationSettings(),
            context_window_tokens=128_000,
        )

        worst_gap = 0.0
        stop = asyncio.Event()

        async def probe() -> None:
            nonlocal worst_gap
            last = time.monotonic()
            while not stop.is_set():
                await asyncio.sleep(0.01)
                now = time.monotonic()
                worst_gap = max(worst_gap, now - last)
                last = now

        probe_task = asyncio.create_task(probe())
        started = time.monotonic()
        replies = await asyncio.gather(*(
            manager.spawn(
                task=f"job {i}", label=f"job-{i}", runtime=runtime,
                origin_channel="websocket", origin_chat_id="load",
                session_key="websocket:load",
            )
            for i in range(FANOUT)
        ))
        self.assertFalse([r for r in replies if r.startswith(SPAWN_REFUSED_PREFIX)])

        deadline = time.monotonic() + 60
        while (manager._running_tasks or manager._queued.get("websocket:load")) and (
            time.monotonic() < deadline
        ):
            await asyncio.sleep(0.05)
        elapsed = time.monotonic() - started
        stop.set()
        await probe_task

        self.assertFalse(manager._running_tasks, "subagents still running")
        self.assertFalse(manager._queued.get("websocket:load"), "subagents still queued")
        self.assertGreaterEqual(provider.calls, FANOUT)
        self.assertLessEqual(provider.peak, CAP)
        self.assertGreater(provider.peak, 1, "no parallelism")
        self.assertLess(worst_gap, 2.0, f"event loop stalled {worst_gap:.2f}s")
        print(f"\nfanout={FANOUT} cap={CAP} peak={provider.peak} "
              f"elapsed={elapsed:.2f}s worst_loop_gap={worst_gap:.3f}s")


if __name__ == "__main__":
    unittest.main()


class ProjectDirCacheTest(unittest.TestCase):
    def test_cached_dir_still_detects_deletion_after_ttl(self) -> None:
        import shutil

        from navin.security import workspace_access as wa

        tmp = tempfile.mkdtemp()
        first = wa._resolve_project_dir(tmp)
        self.assertEqual(first, wa._resolve_project_dir(tmp))
        shutil.rmtree(tmp)
        wa._PROJECT_DIR_CACHE[tmp] = (time.monotonic() - 10, first)
        with self.assertRaises(wa.WorkspaceScopeError):
            wa._resolve_project_dir(tmp)
        self.assertNotIn(tmp, wa._PROJECT_DIR_CACHE)

    def test_relative_path_rejected(self) -> None:
        from navin.security import workspace_access as wa

        with self.assertRaises(wa.WorkspaceScopeError):
            wa._resolve_project_dir("relative/dir")


class SessionIsolationTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_saturated_chat_does_not_delay_another_chat(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        provider = _SlowProvider()
        manager = SubagentManager(
            workspace=Path(tmp.name), bus=_Bus(),  # type: ignore[arg-type]
            max_tool_result_chars=4000, max_concurrent_subagents=10,
        )
        runtime = LLMRuntime(
            provider=provider, model="fake",  # type: ignore[arg-type]
            generation=GenerationSettings(), context_window_tokens=128_000,
        )
        await asyncio.gather(*(
            manager.spawn(task=f"a{i}", label=f"a{i}", runtime=runtime,
                          origin_channel="websocket", origin_chat_id="a",
                          session_key="websocket:a")
            for i in range(100)
        ))
        started = time.monotonic()
        reply = await manager.spawn(task="b", label="b", runtime=runtime,
                                    origin_channel="websocket", origin_chat_id="b",
                                    session_key="websocket:b")
        self.assertFalse(reply.startswith(SPAWN_REFUSED_PREFIX))
        self.assertFalse(manager._queued.get("websocket:b"), "chat b queued behind chat a")
        while any(
            t for t in manager._running_tasks.values()
            if getattr(t, "_navin_session", None) == "websocket:b"
        ) and time.monotonic() - started < 10:
            await asyncio.sleep(0.02)
        b_latency = time.monotonic() - started
        self.assertTrue(manager._queued.get("websocket:a") or manager._running_tasks,
                        "chat a should still be busy")
        deadline = time.monotonic() + 60
        while (manager._running_tasks or manager._queued.get("websocket:a")) and (
            time.monotonic() < deadline
        ):
            await asyncio.sleep(0.05)
        self.assertLess(b_latency, 10)


class DiskAccessCacheTest(unittest.TestCase):
    def test_repeated_skill_lookups_hit_disk_once(self) -> None:
        from unittest import mock

        from navin.agent import skills as sk

        tmp = Path(tempfile.mkdtemp())
        d = tmp / "skills" / "demo"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: demo\ndescription: x\n---\nbody\n")
        sk.clear_skills_index_cache()
        loader = sk.SkillsLoader(tmp, builtin_skills_dir=tmp / "none")
        real = sk.SkillsLoader._get_skill_metadata_uncached
        with mock.patch.object(sk.SkillsLoader, "_get_skill_metadata_uncached",
                               autospec=True, side_effect=real) as spy:
            for _ in range(50):
                self.assertEqual(loader.get_skill_metadata("demo")["name"], "demo")
        self.assertEqual(spy.call_count, 1)
        loader.invalidate_scan()
        self.assertEqual(loader.get_skill_metadata("demo")["name"], "demo")

    def test_cli_apps_installed_names_cached_and_invalidated_on_save(self) -> None:
        from navin.apps.cli import service as svc

        svc._INSTALLED_NAMES_CACHE.clear()
        mgr = svc.CliAppManager(workspace=Path(tempfile.mkdtemp()))
        mgr._save_installed({"gh": {}})
        self.assertEqual(mgr.installed_names(), ["gh"])
        mgr.installed_path.write_text('{"apps": {"x": {}}}')
        self.assertEqual(mgr.installed_names(), ["gh"])
        mgr._save_installed({"kubectl": {}})
        self.assertEqual(mgr.installed_names(), ["kubectl"])
