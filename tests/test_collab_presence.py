"""Unit tests for the in-memory presence tracker."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.board.store import ProjectBoardStore
from navin.collab.presence import PresenceTracker


class PresenceTrackerTest(unittest.TestCase):
    def test_join_sync_and_leave(self):
        tracker = PresenceTracker()
        a = tracker.join(
            "chat-1",
            connection_id="c-a",
            member_id="ada@x.com",
            display_name="Ada",
            role="admin",
        )
        b = tracker.join(
            "chat-1",
            connection_id="c-b",
            member_id="bob@x.com",
            display_name="Bob",
            role="viewer",
        )
        self.assertEqual(a.role, "admin")
        self.assertEqual(b.role, "viewer")
        sync = tracker.sync_payload("chat-1")
        self.assertEqual(sync["chat_id"], "chat-1")
        self.assertEqual(len(sync["members"]), 2)
        ids = {m["member_id"] for m in sync["members"]}
        self.assertEqual(ids, {"ada@x.com", "bob@x.com"})

        left = tracker.leave_connection("c-a")
        self.assertIsNotNone(left)
        assert left is not None
        self.assertEqual(left[0], "chat-1")
        self.assertEqual(left[1].member_id, "ada@x.com")
        self.assertEqual(len(tracker.members("chat-1")), 1)

    def test_rejoin_moves_connection_to_new_chat(self):
        tracker = PresenceTracker()
        tracker.join(
            "chat-1",
            connection_id="c-a",
            member_id="ada@x.com",
            display_name="Ada",
            role="member",
        )
        tracker.join(
            "chat-2",
            connection_id="c-a",
            member_id="ada@x.com",
            display_name="Ada",
            role="member",
        )
        self.assertEqual(len(tracker.members("chat-1")), 0)
        self.assertEqual(len(tracker.members("chat-2")), 1)

    def test_clear(self):
        tracker = PresenceTracker()
        tracker.join(
            "chat-1",
            connection_id="c-a",
            member_id="ada",
            display_name="Ada",
            role="host",
        )
        tracker.clear()
        self.assertEqual(tracker.members("chat-1"), [])


class BoardAssigneeStringTest(unittest.TestCase):
    def test_human_email_and_agent_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectBoardStore(Path(tmp))
            task = store.create_task(
                title="Ship collab",
                actor="agent",
                actor_type="agent",
                assignee={"type": "human", "name": "ada@example.com"},
            )
            self.assertEqual(
                task["assignee"],
                {"type": "human", "name": "ada@example.com"},
            )
            from navin.board.store import _clean_assignee

            self.assertEqual(
                _clean_assignee("human:bob@corp.com"),
                {"type": "human", "name": "bob@corp.com"},
            )
            self.assertEqual(
                _clean_assignee("agent:Builder"),
                {"type": "agent", "name": "Builder"},
            )
            self.assertEqual(
                _clean_assignee("carol@corp.com"),
                {"type": "human", "name": "carol@corp.com"},
            )


if __name__ == "__main__":
    unittest.main()
