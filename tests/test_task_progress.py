# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for task progress parsing and payload builders."""

from __future__ import annotations

import unittest

from navin.utils.task_progress import (
    build_agent_ui_blob,
    build_task_progress_data,
    parse_progress_from_output,
)


class ParseProgressFromOutputTest(unittest.TestCase):
    def test_percent_from_sdkmanager_style(self):
        text = "Downloading platform-34...\n45%\nStill going"
        parsed = parse_progress_from_output(text)
        self.assertEqual(parsed.get("percent"), 45.0)
        self.assertFalse(parsed.get("indeterminate"))

    def test_eta_minutes(self):
        text = "Downloading… 12%\nETA 2m 15s"
        parsed = parse_progress_from_output(text)
        self.assertEqual(parsed.get("percent"), 12.0)
        self.assertEqual(parsed.get("eta_s"), 135.0)

    def test_indeterminate_when_no_percent(self):
        parsed = parse_progress_from_output("Installing emulator…")
        self.assertTrue(parsed.get("indeterminate"))
        self.assertIn("install", parsed.get("label", "").lower())

    def test_empty_is_indeterminate(self):
        parsed = parse_progress_from_output("")
        self.assertTrue(parsed.get("indeterminate"))


class BuildTaskProgressDataTest(unittest.TestCase):
    def test_clamps_percent_and_builds_blob(self):
        data = build_task_progress_data(
            label="Downloading…",
            percent=150,
            step_index=2,
            steps_total=7,
        )
        self.assertEqual(data["percent"], 100.0)
        self.assertFalse(data["indeterminate"])
        self.assertEqual(data["step_index"], 2)
        blob = build_agent_ui_blob(data)
        self.assertEqual(blob["kind"], "task_progress")
        self.assertEqual(blob["data"]["label"], "Downloading…")

    def test_indeterminate_default_without_percent(self):
        data = build_task_progress_data(label="Working…")
        self.assertTrue(data["indeterminate"])
        self.assertNotIn("percent", data)


if __name__ == "__main__":
    unittest.main()
