"""S5.1 - the public protocol: how a transfer campaign is judged.

Everything in this module is public and versioned (``docs/transfer-protocol.md``
mirrors it; a test keeps the two in step). The secret items are not here and
never in this repository.

The rules, in one place:

* four families, all hidden from S4's training and from every skill:
  ``code``, ``browser``, ``business`` (a desk domain Navin was never wired
  for) and ``plan`` (plan, act, observe, replan);
* the bar is a junior human with the same tools, the same time and the same
  ban on "the skill for this exercise": a family passes when its pass rate
  is at least ``JUNIOR_BAR`` (or the measured junior baseline recorded in
  the lock, when higher);
* a family under ``COLLAPSE`` is a collapse; one family under its bar stops
  the campaign: no average rescues it;
* every item runs under a budget (tool calls, wall clock, tokens as the
  cost proxy); over budget is a failed item, never a retry;
* the suites are frozen by hash; touching them after a fail voids the
  campaign;
* a campaign is replayable: same items, same seeds, recorded for a third
  party who wants to refuse the number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = "1.0"

FAMILIES: tuple[tuple[str, str, str], ...] = (
    ("code", "Code", "Read, change and check code in a repository Navin has never seen."),
    ("browser", "Web / browser", "Find and use information on pages Navin has never seen."),
    ("business", "Business desk", "A desk domain Navin was never wired for (not tenders, not the shipped playbooks)."),
    ("plan", "Plan, act, observe, replan", "Multi-step tasks where the first plan is wrong on purpose and must be revised."),
)
FAMILY_IDS: tuple[str, ...] = tuple(f[0] for f in FAMILIES)

# Items per family below which the score means nothing.
MIN_ITEMS_PER_FAMILY = 8

# A family passes at this pass rate or above (a junior human with the same
# tools and time), unless the lock records a higher measured junior baseline.
JUNIOR_BAR = 0.60

# A family under this pass rate has collapsed: the campaign is a fail even if
# the other three families are perfect.
COLLAPSE = 0.25


@dataclass(frozen=True, slots=True)
class ItemBudget:
    """Ceilings per item. Past any of them the item fails; there is no retry."""

    max_tool_calls: int = 30
    timeout_s: float = 600.0
    max_tokens: int = 200_000

    def as_dict(self) -> dict[str, Any]:
        return {"max_tool_calls": self.max_tool_calls, "timeout_s": self.timeout_s, "max_tokens": self.max_tokens}


DEFAULT_BUDGET = ItemBudget()

# The system message every item runs with. No playbook, no skill, no memory:
# the same starting point a junior gets on day one.
SYSTEM_PROMPT = (
    "You are Navin, working alone in a small workspace with the tools listed. "
    "Solve the request completely, verify what you changed, then answer in a "
    "few lines. You have no playbook for this task and no memory of earlier ones."
)

# Rules that are not numbers.
RULES: tuple[str, ...] = (
    "Items are written by people who do not train S4 and do not read the score before the lock.",
    "No skill may be written for an item; no adapter may be trained on an item; no recall of a solution.",
    "The suites are hashed at freeze; a change after a failed campaign voids every later campaign on them.",
    "One family under its bar stops the campaign; families are never averaged.",
    "Over budget is a failed item, not a retry.",
    "A campaign is replayable with the same items and seeds.",
    "Navin never writes the claim itself: the protocol answers forbidden or discussable, a human decides the rest.",
)


def bar_for(family: str, junior_baseline: dict[str, float] | None) -> float:
    """The bar a family must reach: the protocol floor or the measured junior, whichever is higher."""
    measured = (junior_baseline or {}).get(family)
    if isinstance(measured, (int, float)) and not isinstance(measured, bool):
        return max(JUNIOR_BAR, min(1.0, float(measured)))
    return JUNIOR_BAR


def family_verdict(pass_rate: float | None, *, items: int, bar: float) -> str:
    """``pass`` | ``fail`` | ``collapse`` | ``too_few``."""
    if items < MIN_ITEMS_PER_FAMILY or pass_rate is None:
        return "too_few"
    if pass_rate < COLLAPSE:
        return "collapse"
    return "pass" if pass_rate >= bar else "fail"


def protocol_summary() -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "families": [{"id": fid, "title": title, "about": about} for fid, title, about in FAMILIES],
        "min_items_per_family": MIN_ITEMS_PER_FAMILY,
        "junior_bar": JUNIOR_BAR,
        "collapse": COLLAPSE,
        "budget": DEFAULT_BUDGET.as_dict(),
        "rules": list(RULES),
    }
