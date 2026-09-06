"""Keep one MCP server from crowding every other tool out of the catalogue.

An MCP server declares as many tools as it likes and navin registered all of
them.  A single large connector therefore pushed the catalogue well past the
point where a model still picks the right tool, and it did so silently: the
operator saw a successful connection and a degraded agent.

The budget here refuses the overflow instead of trimming it quietly.  Dropping
tools by declaration order and saying nothing would break workflows for no
visible reason, so the refusal names the server, the count, and the remedy
(``enabledTools``).  Acceptance follows declaration order, which is arbitrary
but stable: a stable prefix is what lets prompt caching survive a reconnect.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from navin.utils.helpers import estimate_text_tokens

# A model's tool selection degrades once the catalogue grows past a few dozen
# entries. navin's own tools already occupy roughly 24 slots, so this leaves
# room for a substantial connector without swamping them.
DEFAULT_MAX_TOOLS = 40

# Roughly the size of navin's entire built-in catalogue. A connector that wants
# more context than everything else combined needs an explicit decision.
DEFAULT_MAX_TOKENS = 10_000


@dataclass(frozen=True)
class ToolCandidate:
    """One tool a server offered, with what it would cost to register it."""

    name: str
    description: str
    schema: Any

    @property
    def tokens(self) -> int:
        rendered = f"{self.name}\n{self.description}\n"
        if self.schema is not None:
            try:
                rendered += json.dumps(self.schema, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                rendered += str(self.schema)
        return estimate_text_tokens(rendered)


@dataclass(frozen=True)
class BudgetVerdict:
    """What survived the budget, and what the operator needs to know."""

    accepted: tuple[ToolCandidate, ...]
    dropped: tuple[ToolCandidate, ...]
    tokens: int
    notice: str = ""

    @property
    def overflowed(self) -> bool:
        return bool(self.dropped)


def _limit(configured: int, default: int) -> int:
    """Resolve a configured limit: 0 means the default, negative means no cap."""
    if configured < 0:
        return 0  # 0 is the sentinel for "unbounded" inside apply_budget
    return configured or default


def apply_budget(
    server: str,
    candidates: list[ToolCandidate],
    *,
    max_tools: int = 0,
    max_tokens: int = 0,
) -> BudgetVerdict:
    """Accept tools in declaration order until either limit is reached.

    ``max_tools`` and ``max_tokens`` take the module defaults when left at 0 and
    lift the cap entirely when negative, which is how an operator opts a trusted
    server out of the budget.
    """
    tool_cap = _limit(max_tools, DEFAULT_MAX_TOOLS)
    token_cap = _limit(max_tokens, DEFAULT_MAX_TOKENS)

    accepted: list[ToolCandidate] = []
    dropped: list[ToolCandidate] = []
    spent = 0
    reasons: set[str] = set()

    for candidate in candidates:
        if dropped:
            # Once the budget is exhausted every later tool is refused too,
            # so the accepted set stays a prefix and the catalogue stays stable.
            dropped.append(candidate)
            continue
        cost = candidate.tokens
        if tool_cap and len(accepted) >= tool_cap:
            reasons.add(f"more than {tool_cap} tools")
            dropped.append(candidate)
            continue
        if token_cap and spent + cost > token_cap:
            reasons.add(f"more than {token_cap} tokens of definitions")
            dropped.append(candidate)
            continue
        accepted.append(candidate)
        spent += cost

    notice = ""
    if dropped:
        notice = (
            f"MCP server '{server}' declares {len(candidates)} tools, "
            f"which is {' and '.join(sorted(reasons))}. "
            f"navin registered the first {len(accepted)} and skipped "
            f"{len(dropped)}, because a catalogue this large stops the model "
            f"from picking the right tool. Choose the tools you need with "
            f"enabledTools for this server, or raise maxTools / maxToolTokens "
            f"(use -1 for no limit) if you want them all."
        )

    return BudgetVerdict(
        accepted=tuple(accepted),
        dropped=tuple(dropped),
        tokens=spent,
        notice=notice,
    )
