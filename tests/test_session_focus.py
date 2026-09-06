"""Session focus must survive a gateway restart (mid-plan resume)."""

from __future__ import annotations

import json
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
