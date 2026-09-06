"""Destructive git with nobody watching follows the approvals posture.

`git reset --hard` over uncommitted changes and a stale force push are the two
commands git itself cannot undo. With approvals off (the autonomous default)
they keep working everywhere - CLI, cron, subagents - exactly as before. But
an operator who turned approvals on asked for a stop before history is
destroyed, so an unattended run is refused instead of silently waved through.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from navin.agent.tools.git import (
    _ask_before_discarding,
    _unattended_discard_allowed,
)


def _config(*, approvals_enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        tools=SimpleNamespace(approvals=SimpleNamespace(enabled=approvals_enabled))
    )


def _discard() -> str | None:
    """One unattended history-losing request (no approval gate is bound)."""
    return asyncio.run(_ask_before_discarding(
        action="Throw away uncommitted changes with git reset --hard",
        reason="3 tracked file(s) have changes",
        detail="$ git reset --hard HEAD",
        consequence="Uncommitted work is gone",
    ))


class UnattendedDiscardPolicyTest(unittest.TestCase):
    def test_autonomous_default_keeps_working(self) -> None:
        with patch(
            "navin.config.loader.load_config",
            return_value=_config(approvals_enabled=False),
        ):
            self.assertTrue(_unattended_discard_allowed())
            self.assertIsNone(_discard())

    def test_approvals_on_refuses_an_unattended_destruction(self) -> None:
        with patch(
            "navin.config.loader.load_config",
            return_value=_config(approvals_enabled=True),
        ):
            self.assertFalse(_unattended_discard_allowed())
            refusal = _discard()
            self.assertIsNotNone(refusal)
            self.assertIn("refused", refusal)

    def test_an_unreadable_config_never_takes_the_operation_away(self) -> None:
        """Config trouble must not strand a working repo operation."""
        with patch(
            "navin.config.loader.load_config",
            side_effect=RuntimeError("boom"),
        ):
            self.assertTrue(_unattended_discard_allowed())
            self.assertIsNone(_discard())


if __name__ == "__main__":
    unittest.main()
