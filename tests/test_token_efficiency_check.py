# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Whether the doctor can tell a batching run from a one-call-per-turn run.

The batching guidance in the tool contract is a request to a model, not a
mechanism, so nothing in the code can force it to hold. What the code can do
is refuse to let a regression go unnoticed: the usage recorder already stores
requests and output tokens, and their ratio separates the two regimes. These
tests pin the thresholds so the reading cannot drift silently.
"""

import unittest
from unittest.mock import patch

from navin.diagnostics import Section, doctor_report, token_efficiency_checks


def _state(days: list[tuple[str, int, int]]) -> dict:
    """Usage state from (date, requests, completion_tokens) triples."""
    return {
        "days": {
            date: {"requests": requests, "completion_tokens": completion}
            for date, requests, completion in days
        }
    }


def _patch(state: dict):
    return patch("navin.webui.token_usage.read_token_usage_state", return_value=state)


class ReadingTest(unittest.TestCase):
    def test_the_measured_regression_is_reported_as_one(self):
        """The real numbers from the two-deck run: 96 requests, 12,247 out."""
        with _patch(_state([("2026-08-15", 96, 12_247)])):
            (check,) = token_efficiency_checks()
        self.assertFalse(check.ok)
        self.assertIn("128", check.detail)
        self.assertIn("one tool call per round trip", check.detail)
        self.assertIn("tool_contract.md", check.hint)

    def test_a_batching_run_passes(self):
        with _patch(_state([("2026-08-15", 40, 16_000)])):
            (check,) = token_efficiency_checks()
        self.assertTrue(check.ok)
        self.assertIn("batching well", check.detail)

    def test_the_middle_is_named_rather_than_called_a_pass_or_a_fail(self):
        """200 per request is progress, and saying so is the useful answer."""
        with _patch(_state([("2026-08-15", 50, 10_000)])):
            (check,) = token_efficiency_checks()
        self.assertTrue(check.ok)
        self.assertIn("batching some calls", check.detail)

    def test_the_reading_weighs_requests_rather_than_days(self):
        """A short day of prose answers must not outvote two days of tool work.

        The last day averages 400 output tokens a request, well into batching
        territory, but it is ten requests against a hundred and eighty.
        """
        history = [
            ("2026-08-10", 90, 11_000),
            ("2026-08-11", 90, 11_000),
            ("2026-08-12", 10, 4_000),
        ]
        with _patch(_state(history)):
            (check,) = token_efficiency_checks()
        self.assertFalse(check.ok)
        self.assertIn("190 requests", check.detail)

    def test_only_the_window_asked_for_is_read(self):
        with _patch(_state([("2026-08-01", 1, 99_999), ("2026-08-15", 40, 16_000)])):
            (check,) = token_efficiency_checks(days=1)
        self.assertIn("40 requests", check.detail)


class SilenceTest(unittest.TestCase):
    """A check with nothing to say must say nothing, not guess."""

    def test_a_fresh_install_reports_nothing(self):
        with _patch(_state([])):
            self.assertEqual(token_efficiency_checks(), [])

    def test_recorded_days_without_a_single_request_report_nothing(self):
        with _patch(_state([("2026-08-15", 0, 0)])):
            self.assertEqual(token_efficiency_checks(), [])

    def test_unreadable_usage_is_not_a_doctor_crash(self):
        """Diagnostics run on broken installations; that is the point of them."""
        with patch(
            "navin.webui.token_usage.read_token_usage_state",
            side_effect=OSError("no state file"),
        ):
            self.assertEqual(token_efficiency_checks(), [])

    def test_the_check_never_blocks_the_doctor(self):
        """It reports a cost, not a broken installation."""
        with _patch(_state([("2026-08-15", 96, 12_247)])):
            (check,) = token_efficiency_checks()
        self.assertFalse(check.required)


class ReportTest(unittest.TestCase):
    def test_the_doctor_carries_the_section(self):
        with _patch(_state([("2026-08-15", 40, 16_000)])):
            sections = doctor_report()
        titles = [section.title for section in sections]
        self.assertIn("Token efficiency", titles)

    def test_an_empty_section_is_not_printed(self):
        """The renderer skips it rather than showing a heading over nothing."""
        from navin.cli import commands

        printed: list[str] = []

        class _Console:
            def print(self, line: str = "") -> None:
                printed.append(line)

        with (
            patch.object(commands, "console", _Console()),
            patch(
                "navin.diagnostics.doctor_report",
                return_value=[Section("Token efficiency", [])],
            ),
            patch.object(commands, "_load_inspection_config", side_effect=RuntimeError),
        ):
            with self.assertRaises(RuntimeError):
                commands.doctor()
        self.assertEqual([line for line in printed if "Token efficiency" in line], [])


if __name__ == "__main__":
    unittest.main()
