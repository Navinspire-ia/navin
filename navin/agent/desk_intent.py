# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Which business desk a chat turn explicitly opened.

The desk tools (``tenders``, ``career``, ``leads``, ``marketing``,
``trading``, ``montage``, ``crm``) declare dozens of actions each, so their
schemas cost ~9k tokens together. Measured 2026-09-01: a 57k prompt is ~2.3s
slower per model call than a small one *even at a 100% cache hit*.

Each desk is an isolated module: inside its product module the WebUI keeps
it (see ``extra_denied_tools_for_module``); everywhere else, Navin Code (CLI,
Code workbench) included, only the desk's own slash command opens it.

Plain words used to open desks too, and on a coding turn they misfired all
the time: "resume en 3 points" opened ``career``, "navin/trading/store.py"
opened ``trading``, "un champ contacts" opened ``crm``. Each misfire added
its schema to every later step and, because tools lead the prompt, threw
away the whole prompt cache of the conversation.
"""

from __future__ import annotations

import re
import unicodedata

TENDERS_TOOL = "tenders"
CAREER_TOOL = "career"
LEADS_TOOL = "leads"
MARKETING_TOOL = "marketing"
TRADING_TOOL = "trading"
MONTAGE_TOOL = "montage"
CRM_TOOL = "crm"

#: Desks the Code workbench keeps out of the schema until their slash command.
#: Measured 2026-09-02: montage 1 633 + crm 372 tokens on every step of a
#: code turn that never edits a video or a contact.
CODE_WORKBENCH_LIFTABLE_DESKS: frozenset[str] = frozenset({MONTAGE_TOOL, CRM_TOOL})

#: Slash commands that own a desk. Present anywhere in the message, because a
#: workflow line often carries the brief after the command.
_DESK_COMMANDS: dict[str, tuple[str, ...]] = {
    "/tenders": (TENDERS_TOOL,),
    "/career": (CAREER_TOOL,),
    "/leads": (LEADS_TOOL,),
    "/marketing": (MARKETING_TOOL,),
    "/campaign": (MARKETING_TOOL,),
    "/montage": (MARKETING_TOOL, MONTAGE_TOOL),
    "/trading": (TRADING_TOOL,),
    "/crm": (CRM_TOOL,),
}

# A command only counts as its own token: "/career refais mon CV" opens the
# desk, a path such as "navin/career/loop.py" does not.
_COMMAND_PATTERNS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = tuple(
    (re.compile(r"(?:^|\s)" + re.escape(command) + r"(?![\w/.])"), tools)
    for command, tools in _DESK_COMMANDS.items()
)


def _normalize(text: str | None) -> str:
    """Casefold, strip accents and collapse separators for stable matching."""
    lowered = (text or "").casefold()
    decomposed = unicodedata.normalize("NFKD", lowered)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    # Keep "/" so slash commands survive; split the rest of the glue.
    cleaned = re.sub(r"[’'\-_]+", " ", stripped)
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_intent_text(text: str | None) -> str:
    """The normalisation every rule-based intent reader shares (see :mod:`tool_demand`)."""
    return _normalize(text)


def desk_tools_for_text(text: str | None) -> frozenset[str]:
    """Desk tools this turn opened with a slash command, empty otherwise."""
    normalized = _normalize(text)
    if not normalized:
        return frozenset()
    wanted: set[str] = set()
    for pattern, tools in _COMMAND_PATTERNS:
        if pattern.search(normalized):
            wanted.update(tools)
    return frozenset(wanted)
