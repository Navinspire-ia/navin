# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the web UI background process manager (processes_api)."""

from __future__ import annotations

import asyncio
import sys
import unittest

from navin.agent.tools.exec_session import ExecSessionManager
from navin.webui import processes_api

PYTHON = sys.executable


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _start_sleeper(manager: ExecSessionManager, *, marker: str) -> str:
    command = f'{PYTHON} -c "import time; print(\'{marker}\', flush=True); time.sleep(30)"'
    session_id, _poll = await manager.start(
        command=command,
        cwd=".",
        env={},
        timeout=60,
        shell_program=None,
        login=False,
        yield_time_ms=200,
        max_output_chars=1000,
    )
    return session_id


class ListProcessesTest(unittest.TestCase):
    def test_lists_live_sessions_with_tail(self) -> None:
        async def scenario() -> None:
            manager = ExecSessionManager()
            try:
                session_id = await _start_sleeper(manager, marker="hello-proc")
                # Wait for the tail to catch the marker line.
                tail = ""
                for _ in range(50):
                    tail = await manager.peek(session_id)
                    if "hello-proc" in tail:
                        break
                    await asyncio.sleep(0.05)
                items = await processes_api.list_processes(manager)
                self.assertEqual(len(items), 1)
                item = items[0]
                self.assertEqual(item["id"], session_id)
                self.assertIn("hello-proc", item["tail"])
                self.assertIsNone(item["returncode"])
                self.assertIn("time.sleep", item["command"])
            finally:
                await manager.shutdown()

        run(scenario())

    def test_tail_survives_poll_consumption(self) -> None:
        """poll() drains the agent buffer; the observer tail must remain."""

        async def scenario() -> None:
            manager = ExecSessionManager()
            try:
                session_id = await _start_sleeper(manager, marker="stay-visible")
                for _ in range(50):
                    if "stay-visible" in await manager.peek(session_id):
                        break
                    await asyncio.sleep(0.05)
                # The agent consumes the output (start() already drained some);
                # the observer tail must still hold everything seen so far.
                await manager.poll(
                    session_id=session_id, yield_time_ms=0, max_output_chars=1000
                )
                self.assertIn("stay-visible", await manager.peek(session_id))
            finally:
                await manager.shutdown()

        run(scenario())

    def test_empty_registry(self) -> None:
        async def scenario() -> None:
            manager = ExecSessionManager()
            self.assertEqual(await processes_api.list_processes(manager), [])

        run(scenario())


class KillProcessTest(unittest.TestCase):
    def test_kill_removes_session(self) -> None:
        async def scenario() -> None:
            manager = ExecSessionManager()
            try:
                session_id = await _start_sleeper(manager, marker="to-kill")
                self.assertTrue(await processes_api.kill_process(session_id, manager))
                self.assertEqual(await processes_api.list_processes(manager), [])
            finally:
                await manager.shutdown()

        run(scenario())

    def test_kill_unknown_returns_false(self) -> None:
        async def scenario() -> None:
            manager = ExecSessionManager()
            self.assertFalse(await processes_api.kill_process("nope", manager))

        run(scenario())


class PeekTest(unittest.TestCase):
    def test_peek_does_not_refresh_idle_clock(self) -> None:
        async def scenario() -> None:
            manager = ExecSessionManager()
            try:
                session_id = await _start_sleeper(manager, marker="idle-check")
                infos = await manager.list()
                idle_before = infos[0].idle_s
                await asyncio.sleep(0.2)
                await manager.peek(session_id)
                infos = await manager.list()
                # idle_s keeps growing despite the peek.
                self.assertGreater(infos[0].idle_s, idle_before)
            finally:
                await manager.shutdown()

        run(scenario())

    def test_peek_unknown_raises(self) -> None:
        async def scenario() -> None:
            manager = ExecSessionManager()
            with self.assertRaises(KeyError):
                await manager.peek("missing")

        run(scenario())


if __name__ == "__main__":
    unittest.main()
