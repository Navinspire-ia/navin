"""Honest context-usage metadata persistence."""

from __future__ import annotations

import unittest

from navin.session.context_usage_meta import (
    LAST_CONTEXT_USAGE_KEY,
    LAST_PRELOAD_SKILLS_KEY,
    last_context_usage_row,
    last_preload_skills,
    persist_last_context_usage,
    persist_last_preload_skills,
)


class ContextUsageMetaTest(unittest.TestCase):
    def test_persist_peak_and_cumulative_billed(self) -> None:
        meta: dict = {}
        row = persist_last_context_usage(
            meta,
            {
                "prompt_tokens": 80_000,  # sum across iterations (billing)
                "completion_tokens": 2_000,
                "peak_prompt_tokens": 45_000,  # max single request (window fill)
            },
            context_window_tokens=200_000,
            model="test/model",
        )
        self.assertIsNotNone(row)
        self.assertEqual(meta[LAST_CONTEXT_USAGE_KEY]["peak_prompt_tokens"], 45_000)
        self.assertEqual(meta[LAST_CONTEXT_USAGE_KEY]["prompt_tokens"], 45_000)
        self.assertEqual(meta[LAST_CONTEXT_USAGE_KEY]["billed_tokens_turn"], 82_000)
        persist_last_context_usage(
            meta,
            {"prompt_tokens": 10_000, "completion_tokens": 500, "peak_prompt_tokens": 10_000},
            context_window_tokens=200_000,
        )
        self.assertEqual(
            last_context_usage_row(meta)["billed_tokens_session"],
            82_000 + 10_500,
        )

    def test_persist_keeps_prompt_profile_sections(self) -> None:
        meta: dict = {}
        persist_last_context_usage(
            meta,
            {"prompt_tokens": 12_000, "peak_prompt_tokens": 12_000},
            context_window_tokens=128_000,
            sections={"system.identity": 1400, "tools": 8000, "conversation": 2600},
            tool_count=42,
        )
        row = last_context_usage_row(meta)
        self.assertEqual(row["sections"]["tools"], 8_000)
        self.assertEqual(row["tool_count"], 42)

    def test_persist_keeps_previous_profile_when_this_turn_has_none(self) -> None:
        meta: dict = {}
        persist_last_context_usage(
            meta,
            {"prompt_tokens": 12_000, "peak_prompt_tokens": 12_000},
            context_window_tokens=128_000,
            sections={"system.identity": 1400, "tools": 8000},
            tool_count=42,
        )
        persist_last_context_usage(
            meta,
            {"prompt_tokens": 9_000, "peak_prompt_tokens": 9_000},
            context_window_tokens=128_000,
        )
        row = last_context_usage_row(meta)
        self.assertEqual(row["sections"]["tools"], 8_000)
        self.assertEqual(row["tool_count"], 42)
        self.assertEqual(row["peak_prompt_tokens"], 9_000)

    def test_preload_skills_roundtrip(self) -> None:
        meta: dict = {}
        persist_last_preload_skills(meta, ["code-reviewer", " fact-checker ", ""])
        self.assertEqual(
            last_preload_skills(meta),
            ["code-reviewer", "fact-checker"],
        )
        self.assertEqual(meta[LAST_PRELOAD_SKILLS_KEY], ["code-reviewer", "fact-checker"])
        persist_last_preload_skills(meta, [])
        self.assertIsNone(last_preload_skills(meta))


if __name__ == "__main__":
    unittest.main()
