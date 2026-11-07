# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Session focus must survive a gateway restart (mid-plan resume)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.board import session_focus


class SessionFocusPersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        # Reset module state between tests.
        with session_focus._guard:
            session_focus._focus.clear()
            session_focus._loaded = False

    def test_remember_persists_and_reloads(self) -> None:
        with patch("navin.config.paths.get_webui_dir", return_value=self.root):
            session_focus.remember("websocket:chat-1", ["t1", "t2"])
            path = self.root / "session-focus.json"
            self.assertTrue(path.is_file())
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["websocket:chat-1"], ["t1", "t2"])

            with session_focus._guard:
                session_focus._focus.clear()
                session_focus._loaded = False

            self.assertEqual(
                session_focus.touched("websocket:chat-1"),
                ["t1", "t2"],
            )

    def test_replace_starts_new_plan_and_survives_reload(self) -> None:
        with patch("navin.config.paths.get_webui_dir", return_value=self.root):
            session_focus.remember("websocket:chat-1", ["old-1", "old-2"])
            session_focus.replace("websocket:chat-1", ["new-1", "new-1"])
            with session_focus._guard:
                session_focus._focus.clear()
                session_focus._loaded = False
            self.assertEqual(session_focus.touched("websocket:chat-1"), ["new-1"])

    def test_a_second_cli_cannot_restore_an_obsolete_completed_plan(self) -> None:
        """A CLI open before a new plan cannot overwrite another CLI's replacement."""
        with patch("navin.config.paths.get_webui_dir", return_value=self.root):
            session_focus.remember("cli:work", ["old-done-1", "old-done-2"])
            subprocess.run(
                [
                    sys.executable, "-c",
                    "from pathlib import Path; from navin.board import session_focus; "
                    f"session_focus._store_path = lambda: Path({str(self.root / 'session-focus.json')!r}); "
                    "session_focus.replace('cli:work', ['new-1', 'new-2'])",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            # The stale CLI still has the old plan in memory. Reopening the
            # session must see the new plan before it writes anything else.
            self.assertEqual(session_focus.touched("cli:work"), ["new-1", "new-2"])
            session_focus.remember("cli:another", "unrelated")
            payload = json.loads((self.root / "session-focus.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["cli:work"], ["new-1", "new-2"])
            self.assertEqual(payload["cli:another"], ["unrelated"])

    def test_a_corrupt_snapshot_is_set_aside_not_saved_over(self) -> None:
        with patch("navin.config.paths.get_webui_dir", return_value=self.root):
            store = self.root / "session-focus.json"
            store.write_text("{not json", encoding="utf-8")
            self.assertEqual(session_focus.touched("cli:work"), [])
            kept = list(self.root.glob("session-focus.json.corrupt-*"))
            self.assertEqual(len(kept), 1)
            self.assertEqual(kept[0].read_text(encoding="utf-8"), "{not json")
            session_focus.remember("cli:work", "new-1")
            self.assertEqual(json.loads(store.read_text(encoding="utf-8")), {"cli:work": ["new-1"]})

    def test_a_valid_file_written_meanwhile_is_read_not_set_aside(self) -> None:
        # Another process replaced the corrupt file with a valid one between
        # this read and the set-aside: moving it away lost every chat's plan.
        with patch("navin.config.paths.get_webui_dir", return_value=self.root):
            store = self.root / "session-focus.json"
            store.write_text("{not json", encoding="utf-8")
            real_loads = json.loads
            calls: list[int] = []

            def loads_then_fixed(text: str):
                if not calls:
                    calls.append(1)
                    store.write_text(json.dumps({"cli:other": ["kept-1"]}) + "\n", encoding="utf-8")
                return real_loads(text)

            with patch.object(session_focus.json, "loads", side_effect=loads_then_fixed):
                self.assertEqual(session_focus.touched("cli:other"), ["kept-1"])
            self.assertEqual(list(self.root.glob("session-focus.json.corrupt-*")), [])

    def test_the_lock_can_be_taken_again_by_a_nested_reload(self) -> None:
        # A reload under remember() takes the same lock: two lock objects on
        # one file in one process blocked each other until the timeout.
        with patch("navin.config.paths.get_webui_dir", return_value=self.root):
            self.assertIs(session_focus._update_lock(), session_focus._update_lock())
            with session_focus._update_lock(), session_focus._update_lock():
                pass

    def test_forget_updates_disk(self) -> None:
        with patch("navin.config.paths.get_webui_dir", return_value=self.root):
            session_focus.remember("websocket:chat-1", ["t1"])
            session_focus.forget("websocket:chat-1")
            payload = json.loads(
                (self.root / "session-focus.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("websocket:chat-1", payload)


if __name__ == "__main__":
    unittest.main()
