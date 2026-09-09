# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""What a long-running exec session does with its output and its children.

Three regressions are pinned here. Output arriving from a pipe is sliced at
arbitrary byte offsets, so a multi-byte character cut at a chunk boundary used
to come back as replacement characters. The buffer between two polls grew
without bound, so a chatty dev server left alone for half an hour was a memory
leak. And ``background=true``, which two error messages recommended, was not a
parameter at all.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

import navin.agent.tools.exec_session as session_mod
from navin.agent.tools.exec_session import _ExecSession
from navin.agent.tools.shell import ExecTool

WINDOWS = sys.platform == "win32"


class _FakeStream:
    """Feeds pre-cut byte chunks, the way a pipe hands them over."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    async def read(self, _size: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


def _session(process: unittest.mock.MagicMock) -> _ExecSession:
    return _ExecSession(
        session_id="test", process=process, command="cmd", cwd=".", timeout=None
    )


def _idle_process() -> unittest.mock.MagicMock:
    process = unittest.mock.MagicMock()
    process.stdout = None
    process.stderr = None
    process.returncode = None
    return process


class IncrementalDecodingTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_character_cut_at_a_chunk_boundary_survives(self) -> None:
        # "é" is 0xC3 0xA9; the pipe hands each byte in its own chunk.
        session = _session(_idle_process())
        await session._read_stream(_FakeStream([b"caf", b"\xc3", b"\xa9"]), "")
        self.assertEqual("".join(session._chunks), "café")

    async def test_genuinely_broken_bytes_still_degrade_not_crash(self) -> None:
        session = _session(_idle_process())
        await session._read_stream(_FakeStream([b"ok \xc3"]), "")
        self.assertEqual("".join(session._chunks), "ok \ufffd")


class BoundedBufferTest(unittest.IsolatedAsyncioTestCase):
    async def test_old_output_is_dropped_and_counted_past_the_cap(self) -> None:
        session = _session(_idle_process())
        with unittest.mock.patch.object(session_mod, "BUFFER_CAP_CHARS", 10):
            chunks = [b"a" * 8, b"b" * 8, b"c" * 8]
            await session._read_stream(_FakeStream(chunks), "")
        # Memory stays around the cap; nothing dropped goes unaccounted.
        self.assertLessEqual(session._buffered_chars, 16)
        self.assertEqual(
            session._buffered_chars + session._dropped_chars, 24
        )
        self.assertGreater(session._dropped_chars, 0)


class BackgroundAliasTest(unittest.TestCase):
    """exec(background=true) is real now, so the guidance in errors is honest."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    @unittest.skipIf(WINDOWS, "uses a POSIX sleep")
    def test_background_returns_a_session_id_for_a_running_command(self) -> None:
        tool = ExecTool(working_dir=str(self.root))

        async def run() -> str:
            result = str(await tool.execute(command="sleep 5", background=True))
            for line in result.splitlines():
                if "session_id:" in line:
                    session_id = line.split("session_id:")[1].strip()
                    await tool._session_manager.write(
                        session_id=session_id,
                        chars=None,
                        close_stdin=False,
                        terminate=True,
                        yield_time_ms=0,
                        max_output_chars=1000,
                    )
            return result

        out = asyncio.run(run())
        self.assertIn("session_id", out)
        self.assertIn("Process running", out)

    @unittest.skipIf(WINDOWS, "uses a POSIX echo")
    def test_a_command_that_finishes_in_time_just_returns(self) -> None:
        tool = ExecTool(working_dir=str(self.root))
        out = str(asyncio.run(tool.execute(command="echo done", background=True)))
        self.assertIn("done", out)
        self.assertNotIn("Process running", out)


class ForegroundKeepsRunningTest(unittest.TestCase):
    """A foreground command that outlives its window is handed over, not killed.

    This is what keeps the terminal from ever blocking a turn: a test suite
    that needs eleven minutes is not cut at ten and started over, and a dev
    server started without background=true does not hold the agent for the
    whole timeout before dying.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.manager = session_mod.ExecSessionManager()

    def _tool(self, **kwargs: object) -> ExecTool:
        return ExecTool(working_dir=str(self.root), session_manager=self.manager, **kwargs)

    @staticmethod
    def _session_id(result: str) -> str:
        for line in result.splitlines():
            if "session_id:" in line:
                return line.split("session_id:")[1].strip()
        raise AssertionError(f"no session_id in {result!r}")

    @unittest.skipIf(WINDOWS, "uses a POSIX sleep")
    def test_the_process_survives_and_its_output_so_far_comes_back(self) -> None:
        tool = self._tool()

        async def run() -> tuple[str, list[session_mod.ExecSessionInfo], str]:
            result = str(await tool.execute(
                command="echo started; sleep 30; echo finished", timeout=1,
            ))
            live = await self.manager.list()
            poll = await self.manager.write(
                session_id=self._session_id(result),
                chars=None,
                close_stdin=False,
                terminate=True,
                yield_time_ms=0,
                max_output_chars=1000,
            )
            return result, live, session_mod.format_session_poll("x", poll)

        result, live, ended = asyncio.run(run())
        self.assertNotIn("Error", result)
        self.assertIn("started", result)
        self.assertIn("Still running after 1s", result)
        self.assertIn("Process running. session_id:", result)
        self.assertEqual(len(live), 1)
        self.assertIsNone(live[0].returncode)
        self.assertIn("Session terminated", ended)

    @unittest.skipIf(WINDOWS, "uses a POSIX sleep")
    def test_the_session_keeps_reading_where_the_foreground_stopped(self) -> None:
        tool = self._tool()

        async def run() -> str:
            result = str(await tool.execute(
                command="echo first; sleep 1.5; echo second", timeout=1,
            ))
            poll = await self.manager.write(
                session_id=self._session_id(result),
                chars=None,
                close_stdin=False,
                terminate=False,
                yield_time_ms=3000,
                max_output_chars=1000,
            )
            return session_mod.format_session_poll("x", poll)

        later = asyncio.run(run())
        self.assertIn("second", later)
        self.assertIn("Exit code: 0", later)

    @unittest.skipIf(WINDOWS, "uses a POSIX sleep")
    def test_the_operator_can_keep_the_historical_kill(self) -> None:
        tool = self._tool(keep_running_in_background=False)

        async def run() -> tuple[str, list[session_mod.ExecSessionInfo]]:
            result = str(await tool.execute(command="sleep 30", timeout=1))
            return result, await self.manager.list()

        result, live = asyncio.run(run())
        self.assertIn("timed out after 1 seconds", result)
        self.assertEqual(live, [])

    @unittest.skipIf(WINDOWS, "uses a POSIX sleep")
    def test_a_full_registry_falls_back_to_the_kill(self) -> None:
        self.manager.max_sessions = 0
        tool = self._tool()

        result = str(asyncio.run(tool.execute(command="sleep 30", timeout=1)))
        self.assertIn("timed out after 1 seconds", result)


class SpooledOutputTest(unittest.TestCase):
    """The middle of a truncated output is on disk, not gone."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    @unittest.skipIf(WINDOWS, "uses a POSIX loop")
    def test_truncation_names_a_file_holding_everything(self) -> None:
        tool = ExecTool(working_dir=str(self.root))
        command = 'for i in $(seq 1 2000); do echo "line $i marker"; done'
        out = str(asyncio.run(tool.execute(command=command, max_output_chars=2000)))
        self.assertIn("chars truncated", out)
        self.assertIn("full output in", out)
        spooled = next((self.root / ".navin" / "tool-results" / "exec").iterdir())
        content = spooled.read_text(encoding="utf-8")
        self.assertIn("line 1 marker", content)
        self.assertIn("line 1000 marker", content)
        self.assertIn("line 2000 marker", content)


if __name__ == "__main__":
    unittest.main()
