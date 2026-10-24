# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A failed edit says why: a missing file and text that did not match are not the same."""

from __future__ import annotations

import unittest

from navin.utils.tool_hints import humanize_tool_error

LONG = "frontend-marketing/src/components/admin/lynara-local/DesktopLeadsPanel.tsx"


class ToolErrorLabelTest(unittest.TestCase):
    def test_text_that_did_not_match_is_not_reported_as_a_missing_file(self) -> None:
        error = f"Error: old_text not found in {LONG}. No similar text nearby - re-read the file."
        label = humanize_tool_error(error)
        self.assertEqual(label, "text to replace not found")
        self.assertNotEqual(label, "not found")

    def test_a_missing_file_says_so(self) -> None:
        self.assertEqual(humanize_tool_error(f"Error: File not found: {LONG}"), "file not found")
        self.assertEqual(humanize_tool_error(f"file to update does not exist: {LONG}"), "file does not exist")

    def test_a_short_missing_file_message_is_kept(self) -> None:
        self.assertEqual(humanize_tool_error("Error: File not found: a.ts"), "File not found: a.ts")

    def test_an_ambiguous_match_is_named(self) -> None:
        self.assertEqual(
            humanize_tool_error(f"old_text appears 3 times in {LONG} at lines 4, 9, 20"),
            "text appears several times",
        )

    def test_other_long_not_found_errors_still_collapse(self) -> None:
        self.assertEqual(humanize_tool_error("Error: " + "model x " * 20 + "not found"), "not found")


if __name__ == "__main__":
    unittest.main()
