# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Session picker labels stay human on Windows, Linux and macOS."""

from __future__ import annotations

import unittest

from navin.tui.session_labels import (
    format_session_when,
    session_display_title,
    session_origin,
)


class SessionWhenTests(unittest.TestCase):
    def test_iso_becomes_a_short_date(self) -> None:
        self.assertEqual(format_session_when("2026-09-09T15:00:00"), "9 Sep 15:00")
        self.assertEqual(format_session_when("2026-09-04 17:11:02"), "4 Sep 17:11")

    def test_empty_stays_empty(self) -> None:
        self.assertEqual(format_session_when(""), "")


class SessionTitleTests(unittest.TestCase):
    def test_keeps_a_real_title(self) -> None:
        self.assertEqual(
            session_display_title({"title": "Migration v2", "preview": "ignore", "key": "cli:1"}),
            "Migration v2",
        )

    def test_uses_the_user_preview_not_the_raw_key(self) -> None:
        self.assertEqual(
            session_display_title(
                {"title": "", "preview": "/forge fais un test", "key": "cli:direct"}
            ),
            "fais un test",
        )

    def test_drops_assistant_junk_and_raw_ids(self) -> None:
        self.assertEqual(
            session_display_title(
                {
                    "title": "",
                    "preview": "You ended the turn without calling any tools, so no...",
                    "key": "cli:direct",
                }
            ),
            "Untitled chat",
        )
        self.assertEqual(
            session_display_title({"title": "cli:direct", "preview": "", "key": "cli:direct"}),
            "Untitled chat",
        )

    def test_uses_the_user_line_inside_runtime_context(self) -> None:
        from navin.runtime_context import RUNTIME_CONTEXT_TAG

        preview = f"Salut ! On part sur quoi ?\n\n{RUNTIME_CONTEXT_TAG}\nproject: x"
        self.assertEqual(
            session_display_title({"title": "", "preview": preview, "key": "cli:direct"}),
            "Salut ! On part sur quoi ?",
        )

    def test_sdk_and_app_keys_stay_short(self) -> None:
        self.assertEqual(session_origin("sdk:cognition-e2e"), "SDK")
        self.assertEqual(session_origin("websocket:a3014822-7dd2-42d1-83f9-497394ea237a"), "App")
        self.assertEqual(session_origin("cli:direct"), "This window")


class SessionTitlePersistTests(unittest.TestCase):
    def test_set_title_survives_list_sessions(self) -> None:
        import tempfile
        from pathlib import Path

        from navin.runtime_context import RUNTIME_CONTEXT_TAG
        from navin.session.manager import SessionManager

        with tempfile.TemporaryDirectory() as tmp:
            sessions = SessionManager(Path(tmp))
            session = sessions.get_or_create("cli:1")
            session.messages.append(
                {
                    "role": "user",
                    "content": f"Salut ! On part sur quoi ?\n\n{RUNTIME_CONTEXT_TAG}\nproject: x",
                }
            )
            sessions.save(session)
            rows = sessions.list_sessions()
            self.assertEqual(len(rows), 1)
            self.assertIn("Salut ! On part sur quoi ?", rows[0]["preview"])
            self.assertNotIn("Runtime Context", rows[0]["preview"])
            sessions.set_title("cli:1", "Kickoff")
            rows = sessions.list_sessions()
            self.assertEqual(rows[0]["title"], "Kickoff")
            self.assertEqual(session_display_title(rows[0]), "Kickoff")

    def test_first_cli_message_becomes_the_title(self) -> None:
        import tempfile
        from pathlib import Path

        from navin.session.manager import SessionManager
        from navin.session.webui_turns import apply_provisional_title

        with tempfile.TemporaryDirectory() as tmp:
            sessions = SessionManager(Path(tmp))
            session = sessions.get_or_create("cli:direct")
            self.assertTrue(apply_provisional_title(session, "Salut ! On part sur quoi ?"))
            sessions.save(session)
            rows = sessions.list_sessions()
            self.assertIn("Salut", rows[0]["title"])
            self.assertEqual(session_display_title(rows[0]), rows[0]["title"])

    def test_set_title_rejects_a_blank_name(self) -> None:
        import tempfile
        from pathlib import Path

        from navin.session.manager import SessionManager

        with tempfile.TemporaryDirectory() as tmp:
            sessions = SessionManager(Path(tmp))
            with self.assertRaises(ValueError):
                sessions.set_title("cli:1", "   ")


if __name__ == "__main__":
    unittest.main()
