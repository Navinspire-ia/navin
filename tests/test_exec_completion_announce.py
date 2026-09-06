"""A background command's exit reaches the agent without a poll.

Every ``write_stdin`` poll while a build ran was one model call. The exit is
now published as a system message for the owning session, the way a subagent
result is; a poll that already handed the exit to the agent keeps it quiet.
"""

from __future__ import annotations

import asyncio
import unittest
import unittest.mock

from navin.agent.tools import shell as shell_mod
from navin.agent.tools.context import RequestContext, request_context
from navin.agent.tools.exec_session import _ExecSession
from navin.agent.tools.shell import _ExecCompletionAnnouncer


class _Bus:
    def __init__(self) -> None:
        self.inbound: list = []

    async def publish_inbound(self, msg) -> None:
        self.inbound.append(msg)


def _ctx(channel: str = "websocket") -> RequestContext:
    return RequestContext(channel=channel, chat_id="chat-1", session_key="websocket:chat-1")


def _finished_process(returncode: int) -> unittest.mock.MagicMock:
    process = unittest.mock.MagicMock()
    process.stdout = None
    process.stderr = None
    process.returncode = returncode
    return process


class OpenTest(unittest.TestCase):
    def test_no_request_context_means_no_announcer(self) -> None:
        self.assertIsNone(_ExecCompletionAnnouncer.open(_Bus(), command="make"))

    def test_no_bus_means_no_announcer(self) -> None:
        with request_context(_ctx()):
            self.assertIsNone(_ExecCompletionAnnouncer.open(None, command="make"))

    def test_a_system_turn_does_not_announce_to_itself(self) -> None:
        with request_context(_ctx(channel="system")):
            self.assertIsNone(_ExecCompletionAnnouncer.open(_Bus(), command="make"))

    def test_a_chat_turn_gets_one(self) -> None:
        with request_context(_ctx()):
            self.assertIsNotNone(_ExecCompletionAnnouncer.open(_Bus(), command="make"))


class AnnounceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._grace = _ExecCompletionAnnouncer._GRACE_S
        _ExecCompletionAnnouncer._GRACE_S = 0.01
        self.addCleanup(setattr, _ExecCompletionAnnouncer, "_GRACE_S", self._grace)

    def _session(self, returncode: int, tail: str = "") -> _ExecSession:
        session = _ExecSession(
            session_id="abc123", process=_finished_process(returncode), command="pytest -q", cwd=".",
            timeout=None,
        )
        session._tail = tail
        return session

    async def test_the_exit_is_published_for_the_owning_session(self) -> None:
        bus = _Bus()
        with request_context(_ctx()):
            announcer = _ExecCompletionAnnouncer.open(bus, command="pytest -q")
        announcer.on_finished(self._session(1, tail="FAILED tests/test_x.py::test_y\n1 failed"))
        await asyncio.sleep(0.2)

        self.assertEqual(len(bus.inbound), 1)
        msg = bus.inbound[0]
        self.assertEqual(msg.channel, "system")
        self.assertEqual(msg.sender_id, "exec")
        self.assertEqual(msg.chat_id, "websocket:chat-1")
        self.assertEqual(msg.session_key_override, "websocket:chat-1")
        self.assertEqual(msg.metadata["injected_event"], "exec_finished")
        self.assertEqual(msg.metadata["exec_session_id"], "abc123")
        self.assertEqual(msg.metadata["exit_code"], 1)
        self.assertIn("session abc123 exited with code 1", msg.content)
        self.assertIn("$ pytest -q", msg.content)
        self.assertIn("1 failed", msg.content)
        self.assertIn("do not poll it", msg.content)

    async def test_a_clean_exit_says_so(self) -> None:
        bus = _Bus()
        with request_context(_ctx()):
            announcer = _ExecCompletionAnnouncer.open(bus, command="npm run build")
        announcer.on_finished(self._session(0, tail="built in 12s"))
        await asyncio.sleep(0.2)
        self.assertIn("succeeded (exit code 0)", bus.inbound[0].content)
        self.assertIn("built in 12s", bus.inbound[0].content)

    async def test_an_exit_the_agent_already_saw_is_not_repeated(self) -> None:
        bus = _Bus()
        with request_context(_ctx()):
            announcer = _ExecCompletionAnnouncer.open(bus, command="pytest -q")
        session = self._session(0)
        session.exit_reported = True
        announcer.on_finished(session)
        await asyncio.sleep(0.2)
        self.assertEqual(bus.inbound, [])

    async def test_a_poll_that_sees_the_exit_marks_it_reported(self) -> None:
        session = self._session(0)
        with unittest.mock.patch.object(shell_mod, "_reap_pid", lambda _pid: None):
            poll = await session.poll(0, 10_000)
        self.assertTrue(poll.done)
        self.assertTrue(session.exit_reported)

    async def test_a_long_tail_is_cut_from_the_front(self) -> None:
        bus = _Bus()
        with request_context(_ctx()):
            announcer = _ExecCompletionAnnouncer.open(bus, command="make")
        announcer.on_finished(self._session(0, tail="x" * 5000 + "THE END"))
        await asyncio.sleep(0.2)
        content = bus.inbound[0].content
        self.assertIn("THE END", content)
        self.assertLess(len(content), 2500)


class SessionHookTest(unittest.IsolatedAsyncioTestCase):
    async def test_on_finished_receives_the_session_after_exit(self) -> None:
        seen: list = []
        process = _finished_process(0)

        async def _wait() -> int:
            return 0

        process.wait = _wait
        session = _ExecSession(
            session_id="s1", process=process, command="true", cwd=".", timeout=None,
            on_finished=seen.append,
        )
        await asyncio.sleep(0.05)
        self.assertEqual(seen, [session])


if __name__ == "__main__":
    unittest.main()
