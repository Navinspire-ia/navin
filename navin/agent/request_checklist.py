# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Keep every ask of a request in view, and the screens a user-facing ask needs.

Two failures this module exists for. A request of several tasks ends with one
or two done and the rest forgotten, because nothing kept the list in view once
the first task filled the context. And a user-facing feature ("add a dashboard
to the billing module") ends with the API written while the screen, its menu
entry or its buttons are missing, because the backend half felt complete.

- :func:`explicit_asks` pulls the separate asks out of a numbered or bulleted
  list, out of clauses split by semicolons, or out of prose chaining them
  ("puis", "ensuite", "then"). From three asks the turn files a board plan.
- :func:`ui_terms` finds the words saying the request has a part the user
  opens or clicks (French, English, Arabic).
- :func:`request_checklist_context_provider` turns both into a few runtime
  lines for the turn. The runner keeps the UI terms for its end-of-turn check
  (see :func:`navin.agent.integration_check.frontend_gap`).
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import Sequence
from typing import TYPE_CHECKING

from navin.agent.integration_check import frontend_units
from navin.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines

if TYPE_CHECKING:
    from navin.agent.tools.context import RequestContext

MAX_ASKS = 12
_MAX_ASK_CHARS = 160
_MAX_TERMS = 4

_SLASH_PREFIX = re.compile(r"^\s*/[\w-]+(?:\s+|$)")
_LIST_ITEM = re.compile(r"^\s*(?:\d{1,2}\s*[.):]|[-*+\u2022]|[a-zA-Z]\))\s+(\S.*?)\s*$")
_CHECKBOX = re.compile(r"^\[[ xX]\]\s*")
# Pasted code splits on semicolons too: only plain prose is read as clauses.
_CODE_HINT = re.compile(r"```|[{}=]|^\s{4,}\S", re.MULTILINE)
# Chained asks written as prose: "ajoute X, puis corrige Y, ensuite traduis Z".
# "puis" / "ensuite" / "and then" chain actions anywhere; "then", "also",
# "enfin" only after punctuation, since "if it is empty then reload" or "it
# should also log" are one ask. A plain "et" / "and" never splits.
_PROSE_STEP = re.compile(
    r"(?:\s*[,;.!]\s+|\s+)(?:et\s+|and\s+)?"
    r"(?:puis|ensuite|apr[e\u00e8]s\s+(?:[c\u00e7]a|cela)|and\s+then|et\s+aussi)[,:]?\s+"
    r"|\s*[,;.!]\s+(?:et\s+|and\s+)?(?:then|also|enfin|finally|after\s+that|aussi)[,:]?\s+",
    re.IGNORECASE,
)
_MIN_ASK_WORDS = 3
# From this many asks the turn files them as a board plan before coding.
PLAN_MIN_ASKS = 3
_UI_TERMS = re.compile(
    r"\b(?:"
    r"[\u00e9e]crans?|boutons?|tableaux? de bord|dashboard(?:s|ing)?|formulaires?|menus?"
    r"|onglets?|modales?|pop-?ups?|interface utilisateur|interfaces? graphiques?"
    r"|front(?:-?end)?|composants?|graphiques?|widgets?|barre lat[\u00e9e]rale|sidebar"
    r"|navbar|toolbar|barre d'outils|screens?|buttons?|forms?|tabs?|modals?|dialogs?"
    r"|components?|(?<!helm )charts?|user interface|ui"
    r"|\u0634\u0627\u0634\u0629|\u0632\u0631|\u0648\u0627\u062c\u0647\u0629"
    r"|\u0644\u0648\u062d\u0629 \u0627\u0644\u062a\u062d\u0643\u0645"
    r")\b",
    re.IGNORECASE,
)


def explicit_asks(text: str | None) -> tuple[str, ...]:
    """The separate asks of a request written as a list, or () when it is one ask."""
    body = _SLASH_PREFIX.sub("", text or "", count=1)
    items: list[str] = []
    for line in body.splitlines():
        match = _LIST_ITEM.match(line)
        if match:
            item = _CHECKBOX.sub("", match.group(1)).strip()
            if len(item.split()) >= 2:
                items.append(item)
    if len(items) < 2 and not _CODE_HINT.search(body):
        items = [part.strip() for part in body.split(";") if len(part.split()) >= 3]
        if len(items) < 2:
            items = [
                part.strip(" ,.;:!")
                for part in _PROSE_STEP.split(body)
                if len(part.split()) >= _MIN_ASK_WORDS
            ]
    if len(items) < 2:
        return ()
    return tuple(_shorten(item) for item in items[:MAX_ASKS])


def ui_terms(text: str | None) -> tuple[str, ...]:
    """Words of the request naming something the user opens or clicks, in order."""
    body = _SLASH_PREFIX.sub("", text or "", count=1)
    found: list[str] = []
    for match in _UI_TERMS.finditer(body):
        term = match.group(0).lower()
        if term not in found:
            found.append(term)
        if len(found) >= _MAX_TERMS:
            break
    return tuple(found)


def checklist_lines(
    asks: Sequence[str], terms: Sequence[str], *, has_frontend: bool,
) -> list[str]:
    lines: list[str] = []
    if asks:
        lines.append(
            f"The request holds {len(asks)} separate asks. Cover every one, in order; "
            "none is optional and none waits for a go-ahead. Close with one line per "
            "ask: done with its evidence, or blocked with the reason."
        )
        lines.extend(f"  {number}. {ask}" for number, ask in enumerate(asks, 1))
        if len(asks) >= PLAN_MIN_ASKS:
            lines.append(
                "When this turn implements them, file them first as a board plan, one step "
                "per ask, in ONE board action=create tasks=[...] call (a step that needs "
                "another names it in depends_on), then work through it: implement and test "
                "a step, close it with its evidence and claim the next in one call (move "
                "status=done claim_next=true). The turn is not finished while a step is open."
            )
    if terms and has_frontend:
        lines.append(
            f"This request has a user-facing part ({', '.join(terms)}). A backend alone "
            "does not deliver it. Ship the whole chain the way the existing features are "
            "built: data model, service and API route when the data does not exist yet, "
            "the frontend client call, the screen or component, its route and its menu "
            "or navigation entry, every button and action the request implies wired to a "
            "real handler, loading, empty and error states, and the i18n strings in every "
            "language the project ships. Build the frontend unit before the final answer."
        )
    return lines


async def request_checklist_context_provider(
    request: RequestContext,
) -> RuntimeContextBlock | None:
    """Runtime lines listing the asks of the request and the full-stack chain."""
    text = request.original_user_text or ""
    asks = explicit_asks(text)
    terms = ui_terms(text)
    workspace = getattr(request, "workspace", None)
    has_frontend = False
    if terms and workspace is not None:
        has_frontend = bool(await asyncio.to_thread(frontend_units, workspace))
    content = wrap_runtime_context_lines(
        checklist_lines(asks, terms, has_frontend=has_frontend)
    )
    if not content:
        return None
    return RuntimeContextBlock(source="request_checklist", content=content)


def _shorten(item: str) -> str:
    item = " ".join(item.split())
    if len(item) <= _MAX_ASK_CHARS:
        return item
    return item[: _MAX_ASK_CHARS - 3].rstrip() + "..."


# Words that say what to do, not what it is about: an answer saying "added"
# does not show which ask it covered.
_GENERIC_WORDS = frozenset({
    "ajoute", "ajouter", "rajoute", "rajouter", "corrige", "corriger", "cree", "creer",
    "fais", "faire", "mets", "mettre", "modifie", "modifier", "change", "changer",
    "supprime", "supprimer", "implemente", "implementer", "integre", "integrer",
    "aussi", "avec", "dans", "pour", "sans", "tout", "toute", "tous", "toutes", "cette",
    "plus", "bien", "faut", "doit", "veux", "encore", "comme", "quand", "chaque",
    "make", "create", "build", "implement", "update", "remove", "delete", "also", "with",
    "into", "from", "that", "this", "then", "them", "their", "each", "every", "some",
    "sure", "please", "should", "could", "would", "need", "needs", "want",
})
_STEM = 5


def _words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return [word for word in re.findall(r"\w+", plain) if len(word) >= 4 and not word.isdigit()]


def missing_asks(asks: tuple[str, ...], answer: str | None) -> tuple[str, ...]:
    """The asks the final answer never mentions, in request order.

    An ask counts as mentioned when one of its subject words (the action verbs
    left aside) starts a word of the answer, on a five-letter stem so "facture"
    meets "factures" and "traduis" meets "traduction". An ask with no subject
    word cannot be judged and counts as mentioned.
    """
    said = set(_words(answer or ""))
    missing: list[str] = []
    for ask in asks:
        stems = {word[:_STEM] for word in _words(ask) if word not in _GENERIC_WORDS}
        if stems and not any(word.startswith(stem) for stem in stems for word in said):
            missing.append(ask)
    return tuple(missing)


def build_coverage_nudge(missing: tuple[str, ...]) -> str:
    """The reminder sent once when the final answer drops asks of the request."""
    lines = "\n".join(f"  {number}. {ask}" for number, ask in enumerate(missing, 1))
    return (
        "Your final answer does not cover these asks of the request:\n"
        f"{lines}\n\n"
        "Do them now: implement, test, then report them. If one is already done, out of "
        "scope or blocked, say so in one line for each instead. Do not hand back with an "
        "ask silently dropped."
    )


__all__ = [
    "MAX_ASKS",
    "PLAN_MIN_ASKS",
    "build_coverage_nudge",
    "checklist_lines",
    "explicit_asks",
    "request_checklist_context_provider",
    "missing_asks",
    "ui_terms",
]
