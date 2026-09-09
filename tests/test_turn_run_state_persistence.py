# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A gateway crash mid-turn becomes a visible "interrupted" state.

The in-memory wall clock (_WEBSOCKET_TURN_WALL_STARTED_AT) dies with the
process, so before this fix a restart mid-turn left the client staring at a
spinner that only an 8s stale-turn grace cleared, with no explanation. The
running flag is now mirrored into session metadata; hydrate detects the
orphan exactly once and can announce the interruption.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from navin.session.manager import SessionManager
from navin.session import webui_turns
from navin.session.webui_turns import (
    TURN_RUN_STATE_METADATA_KEY,
    persist_turn_run_state,
    reconnect_turn_action,
    take_interrupted_turn_started_at,
)


class TurnRunStatePersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sessions = SessionManager(Path(self._tmp.name) / "sessions")
        self.key = "websocket:chat-1"
        webui_turns._LIVE_TURN_SESSIONS.clear()
        webui_turns._WEBSOCKET_TURN_WALL_STARTED_AT.clear()

    def test_running_is_mirrored_into_metadata(self) -> None:
        t0 = time.time() - 5
        persist_turn_run_state(self.sessions, self.key, "running", started_at=t0)
        state = self.sessions.get_or_create(self.key).metadata[
            TURN_RUN_STATE_METADATA_KEY
        ]
        self.assertEqual(state["status"], "running")
        self.assertAlmostEqual(state["started_at"], t0, places=3)

    def test_a_normal_end_clears_the_flag(self) -> None:
        persist_turn_run_state(self.sessions, self.key, "running")
        persist_turn_run_state(self.sessions, self.key, "idle")
        meta = self.sessions.get_or_create(self.key).metadata
        self.assertNotIn(TURN_RUN_STATE_METADATA_KEY, meta)

    def test_clearing_without_a_prior_flag_is_a_no_op(self) -> None:
        persist_turn_run_state(self.sessions, self.key, "idle")
        meta = self.sessions.get_or_create(self.key).metadata
        self.assertNotIn(TURN_RUN_STATE_METADATA_KEY, meta)

    def test_orphaned_running_state_is_detected_exactly_once(self) -> None:
        """Simulates the crash: metadata says running, memory knows nothing."""
        t0 = time.time() - 42
        persist_turn_run_state(self.sessions, self.key, "running", started_at=t0)

        first = take_interrupted_turn_started_at(self.sessions, self.key)
        self.assertIsNotNone(first)
        self.assertAlmostEqual(first or 0.0, t0, places=3)

        # The state is rewritten to interrupted: later hydrates stay quiet.
        second = take_interrupted_turn_started_at(self.sessions, self.key)
        self.assertIsNone(second)
        state = self.sessions.get_or_create(self.key).metadata[
            TURN_RUN_STATE_METADATA_KEY
        ]
        self.assertEqual(state["status"], "interrupted")
        self.assertIn("detected_at", state)

    def test_a_finished_turn_never_reports_an_interruption(self) -> None:
        persist_turn_run_state(self.sessions, self.key, "running")
        persist_turn_run_state(self.sessions, self.key, "idle")
        self.assertIsNone(take_interrupted_turn_started_at(self.sessions, self.key))

    def test_survives_a_process_restart(self) -> None:
        """A fresh SessionManager over the same directory sees the flag."""
        persist_turn_run_state(self.sessions, self.key, "running", started_at=123.0)
        reborn = SessionManager(Path(self._tmp.name) / "sessions")
        found = take_interrupted_turn_started_at(reborn, self.key)
        self.assertEqual(found, 123.0)

    def test_unknown_started_at_still_reports_the_interruption(self) -> None:
        session = self.sessions.get_or_create(self.key)
        session.metadata[TURN_RUN_STATE_METADATA_KEY] = {"status": "running"}
        self.sessions.save(session)
        self.assertEqual(
            take_interrupted_turn_started_at(self.sessions, self.key), 0.0
        )

    def test_leaving_the_ui_keeps_a_live_turn(self) -> None:
        t0 = time.time() - 8
        persist_turn_run_state(self.sessions, self.key, "running", started_at=t0)
        webui_turns._WEBSOCKET_TURN_WALL_STARTED_AT.clear()
        action, started = reconnect_turn_action(self.sessions, self.key, "chat-1")
        self.assertEqual(action, "running")
        self.assertAlmostEqual(started or 0.0, t0, places=3)
        self.assertEqual(
            webui_turns.websocket_turn_wall_started_at("chat-1"), started
        )

    def test_a_reply_waiting_in_history_is_not_an_interruption(self) -> None:
        persist_turn_run_state(self.sessions, self.key, "running", started_at=1.0)
        session = self.sessions.get_or_create(self.key)
        session.messages.extend(
            [
                {"role": "user", "content": "salut"},
                {"role": "assistant", "content": "ca va"},
            ]
        )
        self.sessions.save(session)
        webui_turns._LIVE_TURN_SESSIONS.clear()
        webui_turns._WEBSOCKET_TURN_WALL_STARTED_AT.clear()
        action, started = reconnect_turn_action(self.sessions, self.key, "chat-1")
        self.assertEqual(action, "idle")
        self.assertIsNone(started)
        meta = self.sessions.get_or_create(self.key).metadata
        self.assertNotIn(TURN_RUN_STATE_METADATA_KEY, meta)

    def test_a_dead_process_mid_user_turn_is_interrupted(self) -> None:
        persist_turn_run_state(self.sessions, self.key, "running", started_at=9.0)
        session = self.sessions.get_or_create(self.key)
        session.messages.append({"role": "user", "content": "salut"})
        self.sessions.save(session)
        webui_turns._LIVE_TURN_SESSIONS.clear()
        webui_turns._WEBSOCKET_TURN_WALL_STARTED_AT.clear()
        action, started = reconnect_turn_action(self.sessions, self.key, "chat-1")
        self.assertEqual(action, "interrupted")
        self.assertEqual(started, 9.0)


if __name__ == "__main__":
    unittest.main()
