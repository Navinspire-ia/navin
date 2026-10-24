# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The admin bonus the site reports is kept apart; the plan's own share is not."""

from __future__ import annotations

import unittest

from navin.config.schema import Config
from navin.usage_mode import apply_usage_summary

MICRO = 1_000_000


class UsageBonusTest(unittest.TestCase):
    def test_the_bonus_and_its_end_date_are_kept(self) -> None:
        config = Config()
        apply_usage_summary(config, {
            "budgetMicroUsd": 1_062_100_000,
            "extraBudgetMicroUsd": 1000 * MICRO,
            "extraBudgetUntil": "2027-01-15T12:00:00.000Z",
            "spentMicroUsd": 106_210_000,
            "usedPercent": 10,
        })
        lic = config.license
        self.assertEqual(lic.usage_budget_micro_usd, 1_062_100_000)
        self.assertEqual(lic.usage_extra_budget_micro_usd, 1000 * MICRO)
        self.assertEqual(lic.usage_extra_until, "2027-01-15T12:00:00.000Z")

    def test_the_plan_share_is_never_stored(self) -> None:
        # The plan's AI budget would reveal the margin on the price.
        self.assertFalse(hasattr(Config().license, "usage_plan_budget_micro_usd"))

    def test_a_site_without_the_bonus_reads_as_no_bonus(self) -> None:
        config = Config()
        apply_usage_summary(config, {"budgetMicroUsd": 62_100_000, "spentMicroUsd": 0})
        self.assertEqual(config.license.usage_extra_budget_micro_usd, 0)
        self.assertEqual(config.license.usage_extra_until, "")

    def test_removing_the_bonus_clears_it_and_its_date(self) -> None:
        config = Config()
        apply_usage_summary(config, {
            "budgetMicroUsd": 1_062_100_000, "extraBudgetMicroUsd": 1000 * MICRO,
            "extraBudgetUntil": "2027-01-15T12:00:00.000Z",
        })
        apply_usage_summary(config, {"budgetMicroUsd": 62_100_000, "extraBudgetMicroUsd": 0, "extraBudgetUntil": None})
        self.assertEqual(config.license.usage_extra_budget_micro_usd, 0)
        self.assertEqual(config.license.usage_extra_until, "")


if __name__ == "__main__":
    unittest.main()
