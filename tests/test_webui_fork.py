"""WebUI chat fork copies the session prefix at a user-message index."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.session.manager import SessionManager


class SessionForkIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sessions = SessionManager(Path(self._tmp.name) / "sessions")
        self.source = "websocket:src"
        session = self.sessions.get_or_create(self.source)
        session.messages = [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "first reply"},
            {"role": "user", "content": "two"},
            {"role": "assistant", "content": "second reply"},
        ]
        self.sessions.save(session, fsync=True)

    def test_index_equal_to_user_count_copies_the_full_prefix(self) -> None:
        forked = self.sessions.fork_session_before_user_index(
            self.source,
            "websocket:copy-all",
            2,
        )
        self.assertIsNotNone(forked)
        assert forked is not None
        self.assertEqual([row["content"] for row in forked.messages], [
            "one",
            "first reply",
            "two",
            "second reply",
        ])

    def test_index_past_the_last_user_still_copies_the_full_prefix(self) -> None:
        forked = self.sessions.fork_session_before_user_index(
            self.source,
            "websocket:sidebar",
            1_000_000,
        )
        self.assertIsNotNone(forked)
        assert forked is not None
        self.assertEqual(len(forked.messages), 4)
        self.assertEqual(forked.messages[-1]["content"], "second reply")

    def test_index_before_the_second_user_keeps_the_first_turn(self) -> None:
        forked = self.sessions.fork_session_before_user_index(
            self.source,
            "websocket:mid",
            1,
        )
        self.assertIsNotNone(forked)
        assert forked is not None
        self.assertEqual([row["content"] for row in forked.messages], [
            "one",
            "first reply",
        ])
