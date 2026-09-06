"""Tests for recent-edit context in Tab completion (next-edit prediction).

The model call is stubbed: what matters is that the completion prompt and the
native-FIM prefix carry the user's recent edits, correctly sanitized.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from navin.webui import assist_api
from navin.webui.assist_api import (
    fim_prefix_with_context,
    format_recent_edits,
    sanitize_recent_edits,
)


class SanitizeRecentEditsTest(unittest.TestCase):
    def test_non_list_yields_empty(self):
        self.assertEqual(sanitize_recent_edits(None), [])
        self.assertEqual(sanitize_recent_edits("nope"), [])
        self.assertEqual(sanitize_recent_edits({"path": "a"}), [])

    def test_malformed_entries_are_dropped(self):
        edits = sanitize_recent_edits(
            [
                "not a dict",
                {"path": "", "removed": "x", "inserted": "y"},
                {"path": "a.py"},  # no removed/inserted
                {"path": "a.py", "removed": 42, "inserted": None},
                {"path": "ok.py", "line": 3, "removed": "a", "inserted": "b"},
            ]
        )
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["path"], "ok.py")
        self.assertEqual(edits[0]["line"], 3)

    def test_caps_sizes_and_count(self):
        raw = [
            {"path": "f.py", "line": i, "removed": "r" * 1000, "inserted": "i" * 1000}
            for i in range(20)
        ]
        edits = sanitize_recent_edits(raw)
        self.assertEqual(len(edits), 6)  # last six only
        self.assertEqual(edits[0]["line"], 14)
        self.assertEqual(len(edits[0]["removed"]), 400)
        self.assertEqual(len(edits[0]["inserted"]), 400)

    def test_insert_only_and_delete_only_are_valid(self):
        edits = sanitize_recent_edits(
            [
                {"path": "a.py", "line": 1, "removed": "", "inserted": "new line"},
                {"path": "a.py", "line": 2, "removed": "old line", "inserted": ""},
            ]
        )
        self.assertEqual(len(edits), 2)


class FormatRecentEditsTest(unittest.TestCase):
    def test_empty_is_empty(self):
        self.assertEqual(format_recent_edits(None), "")
        self.assertEqual(format_recent_edits([]), "")

    def test_renders_location_and_diff_lines(self):
        block = format_recent_edits(
            [
                {
                    "path": "src/user.py",
                    "line": 12,
                    "removed": "def get_user(id):",
                    "inserted": "def get_user(user_id):",
                }
            ]
        )
        self.assertIn("Recent edits by the user", block)
        self.assertIn("@ src/user.py:12", block)
        self.assertIn("- def get_user(id):", block)
        self.assertIn("+ def get_user(user_id):", block)
        self.assertTrue(block.endswith("\n\n"))

    def test_oldest_first_order_is_preserved(self):
        block = format_recent_edits(
            [
                {"path": "a.py", "line": 1, "removed": "first", "inserted": "FIRST"},
                {"path": "b.py", "line": 2, "removed": "second", "inserted": "SECOND"},
            ]
        )
        self.assertLess(block.index("a.py"), block.index("b.py"))

    def test_multiline_edits_are_row_limited(self):
        block = format_recent_edits(
            [
                {
                    "path": "big.py",
                    "line": 1,
                    "removed": "\n".join(f"r{i}" for i in range(20)),
                    "inserted": "\n".join(f"i{i}" for i in range(20)),
                }
            ]
        )
        self.assertIn("- r5", block)
        self.assertNotIn("- r6", block)
        self.assertIn("+ i5", block)
        self.assertNotIn("+ i6", block)


class FimPrefixWithContextTest(unittest.TestCase):
    def test_no_context_returns_none(self):
        self.assertIsNone(fim_prefix_with_context("code before", None, None))
        self.assertIsNone(fim_prefix_with_context("code before", [], []))

    def test_context_is_prepended_and_code_stays_last(self):
        prefix = fim_prefix_with_context(
            "def handler():\n    ",
            [{"path": "util.py", "content": "def helper(): ..."}],
            [{"path": "a.py", "line": 1, "removed": "x", "inserted": "y"}],
        )
        self.assertIsNotNone(prefix)
        assert prefix is not None
        self.assertTrue(prefix.endswith("def handler():\n    "))
        self.assertIn("util.py", prefix)
        self.assertIn("Recent edits by the user", prefix)


class CompletionPromptCarriesEditsTest(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_and_fim_prefix_include_recent_edits(self):
        captured: dict = {}

        async def fake_ask(**kwargs):
            captured.update(kwargs)
            return "suggestion", "model-x", "code", "fim"

        with patch.object(
            assist_api,
            "_ask_completion_native_or_chat",
            AsyncMock(side_effect=fake_ask),
        ):
            payload = await assist_api.completion_payload(
                path="src/app.py",
                prefix="def get_user(user_id):\n    ",
                suffix="\nprint('done')",
                recent_edits=[
                    {
                        "path": "src/app.py",
                        "line": 3,
                        "removed": "def get_user(id):",
                        "inserted": "def get_user(user_id):",
                    }
                ],
            )
        self.assertEqual(payload["completion"], "suggestion")
        self.assertIn("Recent edits by the user", captured["user_prompt"])
        self.assertIn("- def get_user(id):", captured["user_prompt"])
        self.assertIsNotNone(captured["fim_prefix"])
        self.assertIn("Recent edits by the user", captured["fim_prefix"])
        self.assertTrue(
            captured["fim_prefix"].endswith("def get_user(user_id):\n    ")
        )

    async def test_no_edits_keeps_pure_fim_prefix(self):
        captured: dict = {}

        async def fake_ask(**kwargs):
            captured.update(kwargs)
            return "ok", "m", "code", "fim"

        with patch.object(
            assist_api,
            "_ask_completion_native_or_chat",
            AsyncMock(side_effect=fake_ask),
        ):
            await assist_api.completion_payload(
                path="a.py", prefix="x = ", suffix=""
            )
        self.assertIsNone(captured["fim_prefix"])
        self.assertNotIn("Recent edits", captured["user_prompt"])


if __name__ == "__main__":
    unittest.main()
