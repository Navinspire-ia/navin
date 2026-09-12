# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import unittest

from navin.tui.widgets import format_elapsed, format_working_line


class FormatElapsedTests(unittest.TestCase):
    def test_seconds_minutes_hours(self) -> None:
        self.assertEqual(format_elapsed(12), "12s")
        self.assertEqual(format_elapsed(654), "10m 54s")
        self.assertEqual(format_elapsed(3723), "1h 02m")


class FormatWorkingLineTests(unittest.TestCase):
    def test_navin_keys_not_cursor_keys(self) -> None:
        line = format_working_line(elapsed_s=654, background=0)
        self.assertEqual(line, "Working (10m 54s • esc to interrupt)")
        self.assertNotIn("/stop", line)
        self.assertNotIn("/ps", line)

    def test_one_background_terminal(self) -> None:
        line = format_working_line(elapsed_s=12, background=1)
        self.assertIn("esc to interrupt", line)
        self.assertIn("1 background terminal running", line)
        self.assertIn("/ps to view", line)
        self.assertNotIn("/stop to close", line)

    def test_several_background_terminals(self) -> None:
        line = format_working_line(elapsed_s=5, background=3)
        self.assertIn("3 background terminals running", line)
        self.assertIn("/ps to view", line)


class WorkingLineWidgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_hidden_until_set(self) -> None:
        from textual.app import App, ComposeResult

        from navin.tui.widgets import WorkingLine

        class Host(App):
            def compose(self) -> ComposeResult:
                yield WorkingLine(id="working")

        app = Host()
        async with app.run_test(size=(80, 12)) as _pilot:
            line = app.query_one("#working", WorkingLine)
            self.assertFalse(line.has_class("-visible"))
            line.set_line(format_working_line(elapsed_s=12, background=1))
            self.assertTrue(line.has_class("-visible"))
            self.assertIn("esc to interrupt", str(line.content))
            self.assertIn("/ps to view", str(line.content))
            line.set_line("")
            self.assertFalse(line.has_class("-visible"))


if __name__ == "__main__":
    unittest.main()
