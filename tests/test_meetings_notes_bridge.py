"""Meetings <-> Notes integration: bridge, `meetings` agent tool, store paging."""

from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from navin.agent.tools.meetings import MeetingsTool
from navin.meetings import notes_bridge
from navin.meetings.store import MeetingStore
from navin.notes import store as notes_store

MINUTES = """# Weekly sync

## Decisions
- Ship the desktop build on Friday.

## Action items
| Owner | Action | Deadline |
| --- | --- | --- |
| Ana | Prepare the release notes | 2026-09-05 |
| unassigned | Book the demo room | no deadline |
| **Marc** | Call the *supplier* | next week |
"""


class ActionItemsTest(unittest.TestCase):
    def test_table_in_any_column_order_and_language(self) -> None:
        items = notes_bridge.action_items_from_markdown(MINUTES)
        self.assertEqual(
            items,
            [
                {"owner": "Ana", "action": "Prepare the release notes", "deadline": "2026-09-05"},
                {"owner": "", "action": "Book the demo room", "deadline": ""},
                {"owner": "Marc", "action": "Call the supplier", "deadline": "next week"},
            ],
        )
        french = "| Échéance | Responsable | Action |\n|---|---|---|\n| 2026-10-01 | Léa | Relancer le client |\n"
        self.assertEqual(
            notes_bridge.action_items_from_markdown(french),
            [{"owner": "Léa", "action": "Relancer le client", "deadline": "2026-10-01"}],
        )

    def test_bullets_under_an_actions_heading_when_no_table(self) -> None:
        text = "## Summary\n- not an action\n\n## Next steps\n- [ ] Send the quote\n2. Update CRM\n"
        items = notes_bridge.action_items_from_markdown(text)
        self.assertEqual([item["action"] for item in items], ["Send the quote", "Update CRM"])

    def test_task_lines_use_the_notes_due_syntax(self) -> None:
        self.assertEqual(
            notes_bridge.task_line({"owner": "Ana", "action": "Do X", "deadline": "2026-09-05"}),
            "- [ ] Do X (Ana) @due(2026-09-05)",
        )
        self.assertEqual(
            notes_bridge.task_line({"owner": "", "action": "Do Y", "deadline": "next week"}),
            "- [ ] Do Y - next week",
        )


class BridgeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.notes_root = base / "notes"
        self.notes_root.mkdir()
        self.store = MeetingStore(base / "meetings")
        self.addCleanup(self._tmp.cleanup)
        for target in (
            "navin.notes.store.notes_root",
            "navin.meetings.notes_bridge.default_meeting_store",
        ):
            value = self.store if target.endswith("default_meeting_store") else self.notes_root
            patcher = mock.patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.record = self.store.create(
            {
                "id": "weekly",
                "meta": {
                    "title": "Weekly sync",
                    "started_at": "2026-09-01T10:00:00Z",
                    "duration_sec": 1800,
                    "speakers": ["Ana", "Marc"],
                },
                "transcript": "Ana: let's ship Friday",
                "notes": "Prepared: agenda",
                "summary": MINUTES,
            }
        )


class SaveMeetingToNoteTest(BridgeTestCase):
    def test_creates_a_linked_note_with_tasks_then_refreshes_it(self) -> None:
        result = notes_bridge.save_meeting_to_note(
            "weekly", store=self.store, notes_root=self.notes_root
        )
        self.assertTrue(result["created"])
        self.assertEqual(result["action_items"], 3)
        self.assertEqual(result["folder"], "meetings")
        self.assertEqual(result["title"], "2026-09-01 Weekly sync")

        page = notes_store.get_note(self.notes_root, result["note_id"])
        self.assertIn("meeting", page["note"]["tags"])
        self.assertEqual(page["note"]["props"]["meeting_id"], "weekly")
        body = page["markdown"]
        self.assertIn("- Participants: Ana, Marc", body)
        self.assertIn("- Duration: 30 min", body)
        self.assertIn("- [ ] Prepare the release notes (Ana) @due(2026-09-05)", body)
        self.assertIn("## Minutes", body)
        self.assertIn("Prepared: agenda", body)
        self.assertNotIn("let's ship Friday", body)
        # Tasks are real Notes tasks.
        self.assertEqual(notes_store.list_tasks(self.notes_root)["total"], 3)
        # The meeting points back to the note.
        self.assertEqual(self.store.get("weekly")["meta"]["note_id"], result["note_id"])
        events = [e["action"] for e in self.store.read_audit(meeting_id="weekly")]
        self.assertIn("meeting.note_saved", events)

        # Second save: same note, refreshed body, history kept, transcript opt-in.
        self.store.update("weekly", {"summary": MINUTES + "\n## Extra\nmore\n"})
        again = notes_bridge.save_meeting_to_note(
            "weekly", include_transcript=True, store=self.store, notes_root=self.notes_root
        )
        self.assertFalse(again["created"])
        self.assertEqual(again["note_id"], result["note_id"])
        page = notes_store.get_note(self.notes_root, result["note_id"])
        self.assertIn("## Extra", page["markdown"])
        self.assertIn("let's ship Friday", page["markdown"])
        self.assertEqual(notes_store.list_notes(self.notes_root)["total"], 1)
        self.assertTrue(notes_store.list_history(self.notes_root, result["note_id"]))

    def test_survives_a_renamed_and_moved_note(self) -> None:
        result = notes_bridge.save_meeting_to_note(
            "weekly", store=self.store, notes_root=self.notes_root
        )
        notes_store.update_note(
            self.notes_root, result["note_id"], title="Sync hebdo", folder="archive/2026"
        )
        again = notes_bridge.save_meeting_to_note(
            "weekly", store=self.store, notes_root=self.notes_root
        )
        self.assertEqual(again["note_id"], result["note_id"])
        self.assertEqual(again["title"], "Sync hebdo")
        self.assertEqual(again["folder"], "archive/2026")

    def test_store_payload_to_note_mode(self) -> None:
        from navin.webui import meeting_api

        with mock.patch("navin.meetings.store.default_meeting_store", return_value=self.store):
            payload = meeting_api.store_payload("to_note", {"id": "weekly"})
        self.assertEqual(payload["meeting_id"], "weekly")
        self.assertTrue(payload["created"])
        self.assertIn("to_note", meeting_api.STORE_MODES)


class MeetingsToolTest(BridgeTestCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch("navin.meetings.store.default_meeting_store", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tool = MeetingsTool()
        self.store.create(
            {"id": "pricing", "meta": {"title": "Pricing call"}, "transcript": "29 euros per month"}
        )

    def run_tool(self, **kwargs):
        return asyncio.run(self.tool.execute(**kwargs))

    def test_reads_are_read_only_and_save_note_is_not(self) -> None:
        self.assertTrue(self.tool.call_read_only({"action": "search"}))
        self.assertTrue(self.tool.call_read_only({"action": "read"}))
        self.assertFalse(self.tool.call_read_only({"action": "save_note"}))

    def test_search_then_read_minutes(self) -> None:
        out = self.run_tool(action="search", query="euros month")
        self.assertIn("Pricing call", out)
        self.assertIn("id=pricing", out)
        out = self.run_tool(action="read", id="weekly")
        self.assertIn("# Weekly sync", out)
        self.assertIn("participants: Ana, Marc", out)
        self.assertIn("Prepare the release notes", out)
        self.assertNotIn("let's ship Friday", out)
        out = self.run_tool(action="read", title="pricing call", section="transcript")
        self.assertIn("29 euros per month", out)

    def test_read_empty_minutes_points_to_transcript(self) -> None:
        out = self.run_tool(action="read", id="pricing")
        self.assertIn("No minutes yet", out)

    def test_list_and_errors(self) -> None:
        out = self.run_tool(action="list")
        self.assertIn("2 most recent", out)
        self.assertTrue(self.run_tool(action="read", title="nope").is_error)
        self.assertTrue(self.run_tool(action="search").is_error)
        self.assertTrue(self.run_tool(action="read", id="weekly", section="bogus").is_error)
        self.assertTrue(self.run_tool(action="explode").is_error)

    def test_save_note_files_the_meeting(self) -> None:
        out = self.run_tool(action="save_note", title="Weekly sync")
        self.assertIn("Created note", out)
        self.assertIn("3 action item(s)", out)
        self.assertEqual(notes_store.list_notes(self.notes_root)["total"], 1)
        out = self.run_tool(action="save_note", id="weekly")
        self.assertIn("Updated note", out)


class MeetingStorePagingAndLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = MeetingStore(Path(self._tmp.name) / "meetings")
        self.addCleanup(self._tmp.cleanup)

    def test_list_pages_and_count(self) -> None:
        for index in range(7):
            self.store.create({"id": f"m{index}", "meta": {"title": f"M {index}"}})
        self.assertEqual(self.store.count(), 7)
        first = self.store.list(limit=3)
        second = self.store.list(limit=3, offset=3)
        third = self.store.list(limit=3, offset=6)
        ids = [row["id"] for row in first + second + third]
        self.assertEqual(len(ids), 7)
        self.assertEqual(len(set(ids)), 7)
        self.assertIn("started_at", self.store._index_entry({"id": "x", "started_at": "2026"}))

    def test_audit_log_is_append_only(self) -> None:
        self.store.create({"id": "a", "meta": {"title": "A"}})
        before = self.store.audit_path.read_bytes()
        self.store.append_audit("a", "custom.event", details={"k": 1})
        after = self.store.audit_path.read_bytes()
        self.assertTrue(after.startswith(before))
        actions = [event["action"] for event in self.store.read_audit(meeting_id="a")]
        self.assertEqual(actions[-1], "custom.event")
        self.assertIn("meeting.created", actions)

    def test_concurrent_threads_do_not_trip_the_lock(self) -> None:
        errors: list[BaseException] = []

        def worker(index: int) -> None:
            try:
                for step in range(5):
                    self.store.create({"id": f"t{index}-{step}", "meta": {"title": "T"}})
                    self.store.list(limit=10)
            except BaseException as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(errors, [])
        self.assertEqual(self.store.count(), 20)

    def test_lock_is_reentrant_in_one_thread(self) -> None:
        with self.store.lock:
            with self.store.lock:
                self.store.create({"id": "nested", "meta": {"title": "N"}})
        self.assertEqual(self.store.count(), 1)


if __name__ == "__main__":
    unittest.main()
