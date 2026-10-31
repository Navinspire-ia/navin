# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""How many tests the agent runs to validate one change.

A task used to end with every test file tied to the change (up to 24, plus
whole folders for a conftest edit), then the gate asked again after each edit:
a small fix could spend more time testing than coding. ``essential`` (the
default) keeps the one or two test files closest to the change: the edited
test, the test named after the edited module, then one integration test that
imports it. ``thorough`` keeps the old breadth for users who want it.

Set in Settings > Advanced (``agents.defaults.testingDepth``).
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

ESSENTIAL = "essential"
THOROUGH = "thorough"
DEPTHS: tuple[str, ...] = (ESSENTIAL, THOROUGH)

ESSENTIAL_MAX_TEST_FILES = 2
THOROUGH_MAX_TEST_FILES = 24


@dataclass(frozen=True, slots=True)
class CheckBudget:
    """The tests one change may run: how many files, and whether whole folders."""

    depth: str
    max_files: int
    # A conftest edit maps to its whole folder (often the whole suite).
    whole_folders: bool

    @property
    def essential(self) -> bool:
        return self.depth == ESSENTIAL


def budget_for(depth: str) -> CheckBudget:
    if depth == THOROUGH:
        return CheckBudget(THOROUGH, THOROUGH_MAX_TEST_FILES, True)
    return CheckBudget(ESSENTIAL, ESSENTIAL_MAX_TEST_FILES, False)


def testing_depth() -> str:
    """The configured depth; an unreadable config keeps the default."""
    from navin.config.loader import load_config_cached

    try:
        depth = load_config_cached().agents.defaults.testing_depth
    except ValueError as exc:
        # The loader names the broken file; validation must still run.
        logger.warning("Testing depth falls back to essential: {}", exc)
        return ESSENTIAL
    return depth if depth in DEPTHS else ESSENTIAL


def current_budget() -> CheckBudget:
    return budget_for(testing_depth())
