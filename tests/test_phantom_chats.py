# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A reconnect must never leave an empty chat behind in the sidebar.

Every WebSocket connection mints a throwaway default chat id. Read-only
hydrate paths used to call get_or_create on it, and shutdown then flushed the
blank session to disk, so one reconnect storm turned into hundreds of "New
chat" rows nobody created.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import os
import time

from navin.session.manager import SessionManager
from navin.session.webui_turns import take_interrupted_turn_started_at
from navin.webui.session_list_index import list_webui_sessions


class PhantomChatTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sessions = SessionManager(Path(self._tmp.name))
        self.dir = self.sessions.sessions_dir

    def _files(self) -> list[Path]:
        return sorted(self.dir.glob("*.jsonl"))

    def test_peek_does_not_create_an_unknown_session(self) -> None:
        self.assertIsNone(self.sessions.peek("websocket:never-seen"))
        self.sessions.flush_all()
        self.assertEqual(self._files(), [])

    def test_peek_returns_a_stored_session(self) -> None:
        session = self.sessions.get_or_create("websocket:real")
        session.metadata["webui"] = True
        self.sessions.save(session)
        self.assertIsNotNone(self.sessions.peek("websocket:real"))

    def test_interrupted_turn_probe_leaves_no_trace(self) -> None:
        self.assertIsNone(
            take_interrupted_turn_started_at(self.sessions, "websocket:throwaway")
        )
        self.sessions.flush_all()
        self.assertEqual(self._files(), [])

    def test_flush_never_writes_a_blank_session(self) -> None:
        self.sessions.get_or_create("websocket:blank")
        self.assertEqual(self.sessions.flush_all(), 1)
        self.assertEqual(self._files(), [])

    def test_a_chat_with_metadata_is_still_persisted(self) -> None:
        session = self.sessions.get_or_create("websocket:new-webui-chat")
        session.metadata["webui"] = True
        self.sessions.save(session)
        self.assertEqual(len(self._files()), 1)

    def test_a_chat_with_messages_is_still_persisted(self) -> None:
        session = self.sessions.get_or_create("websocket:spoken")
        session.messages.append({"role": "user", "content": "salut"})
        self.sessions.save(session)
        self.assertEqual(len(self._files()), 1)

    def test_an_existing_file_is_still_updated_when_emptied(self) -> None:
        session = self.sessions.get_or_create("websocket:cleared")
        session.messages.append({"role": "user", "content": "salut"})
        self.sessions.save(session)
        session.messages.clear()
        session.metadata.clear()
        self.sessions.save(session)
        self.assertEqual(len(self._files()), 1)
        stored = self.sessions.read_session_file("websocket:cleared")
        self.assertEqual(stored["messages"], [])

    def _age(self, path: Path, seconds: float) -> None:
        old = time.time() - seconds
        os.utime(path, (old, old))

    def test_the_sidebar_sweeps_away_legacy_blank_sessions(self) -> None:
        blank = self.sessions.get_or_create("websocket:ghost")
        # Bypass the save guard the way an older gateway wrote these files.
        self.dir.joinpath(SessionManager._storage_key(blank.key) + ".jsonl").write_text(
            '{"_type": "metadata", "key": "websocket:ghost", '
            '"created_at": "2026-01-01T00:00:00", '
            '"updated_at": "2026-01-01T00:00:00", "metadata": {}}\n',
            encoding="utf-8",
        )
        path = self._files()[0]
        self._age(path, 600)
        self.assertEqual(list_webui_sessions(self.sessions), [])
        self.assertEqual(self._files(), [])

    def test_a_chat_created_seconds_ago_is_never_swept(self) -> None:
        self.dir.joinpath("d2Vic29ja2V0OmZyZXNo.jsonl").write_text(
            '{"_type": "metadata", "key": "websocket:fresh", '
            '"created_at": "2026-01-01T00:00:00", '
            '"updated_at": "2026-01-01T00:00:00", "metadata": {}}\n',
            encoding="utf-8",
        )
        list_webui_sessions(self.sessions)
        self.assertEqual(len(self._files()), 1)


if __name__ == "__main__":
    unittest.main()
