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


class InstallMissingToolsTest(unittest.TestCase):
    """``navin doctor --install`` runs the plan of every MISS line and reports each."""

    def _sections(self, *missing: str) -> list[Section]:
        from navin.diagnostics import Check

        checks = [Check(name="git", ok=True, detail="/usr/bin/git")]
        checks.extend(Check(name=name, ok=False, detail="purpose", hint="hint") for name in missing)
        return [Section("Installation", []), Section("External tools", checks)]

    def test_runs_the_managers_and_reports_installed_failed_and_manual(self):
        from navin.diagnostics import install_missing_tools
        from navin.host.packages import InstallPlan

        plans = {
            "ffmpeg": InstallPlan(argv=["winget", "install", "--id", "Gyan.FFmpeg"], manual="winget install --id Gyan.FFmpeg -e", manager_available=True, kind="winget"),
            "jq": InstallPlan(argv=["winget", "install", "--id", "jqlang.jq"], manual="winget install --id jqlang.jq -e", manager_available=True, kind="winget"),
            "docker": InstallPlan(argv=None, manual="sudo apt-get install -y docker.io", manager_available=True, kind="apt-get"),
            "pandoc": InstallPlan(argv=None, manual="sudo apt-get install -y pandoc", manager_available=True, kind="apt-get"),
        }
        ran: list[list[str]] = []

        def run(argv: list[str]) -> int:
            ran.append(argv)
            return 0 if "Gyan.FFmpeg" in argv else 1

        present = {"git": "/usr/bin/git", "ffmpeg": "C:/ffmpeg.exe"}
        with patch("navin.diagnostics.install_plan", side_effect=lambda spec, **kw: plans[_name_of(spec)]):
            outcomes = install_missing_tools(
                self._sections("ffmpeg", "jq", "docker", "pandoc"),
                run=run, interactive=False, which=present.get,
            )
        self.assertEqual(ran, [plans["ffmpeg"].argv, plans["jq"].argv])
        by_name = {outcome.name: outcome for outcome in outcomes}
        self.assertEqual(by_name["ffmpeg"].status, "installed")
        self.assertEqual(by_name["jq"].status, "failed")
        self.assertEqual(by_name["docker"].status, "manual")
        self.assertEqual(by_name["docker"].command, "sudo apt-get install -y docker.io")
        from navin.diagnostics import manual_commands

        # What is left to type: the failed winget line and ONE apt line for both packages.
        self.assertEqual(
            manual_commands(outcomes),
            ["winget install --id jqlang.jq -e", "sudo apt-get install -y docker.io pandoc"],
        )

    def test_a_tool_the_path_does_not_show_yet_is_done_not_failed(self):
        from navin.diagnostics import install_missing_tools
        from navin.host.packages import InstallPlan

        plan = InstallPlan(argv=["winget", "install", "--id", "jqlang.jq"], manual="winget install --id jqlang.jq -e", manager_available=True, kind="winget")
        with patch("navin.diagnostics.install_plan", return_value=plan):
            (outcome,) = install_missing_tools(self._sections("jq"), run=lambda argv: 0, interactive=False, which=lambda name: None)
        self.assertEqual(outcome.status, "done")
        self.assertIn("new terminal", outcome.detail)

    def test_every_listed_tool_has_a_package_on_every_platform(self):
        from navin.diagnostics import _TOOLS

        for name, _purpose, spec in _TOOLS:
            if name == "curl":
                continue  # ships with Windows 10+
            self.assertTrue(spec.winget, f"{name} has no winget id")
            self.assertTrue(spec.brew, f"{name} has no brew formula")
            self.assertTrue(spec.apt and spec.dnf and spec.pacman, f"{name} misses a Linux package")


def _name_of(spec) -> str:
    from navin.diagnostics import _TOOLS

    for name, _purpose, candidate in _TOOLS:
        if candidate == spec:
            return name
    raise AssertionError(f"unknown spec {spec}")
