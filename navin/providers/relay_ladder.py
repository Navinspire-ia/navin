# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Which model takes over when the chosen one cannot answer.

The flagships of the plan form a ladder, strongest first. When one of them
cannot answer (quota reached, rate limited, down), the step goes to the next
one still available: first the flagships of its own family (Claude Opus and
Claude Fable relay each other, then Claude Sonnet), then those of the other
family (GPT-6 Astra, then GPT-6.1 Sol). Staying in the family first keeps the
tool-call habits and the prompt style the turn was written for.

A substitute below the chosen model on the ladder runs at its deepest
reasoning effort, to make up for the step down. A peer, or a stronger model,
keeps the effort the turn runs at.

Families are matched by name without their version, and the newest version in
the model list is the one used, so the ladder follows new releases without an
edit here: Claude Opus 5.6 replaces 5.5 the day it reaches the catalog.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

#: Model families, strongest first. Families sharing a rung are peers.
RELAY_LADDER: tuple[tuple[str, ...], ...] = (
    ("claude-opus", "claude-fable"),
    ("claude-sonnet",),
    ("gpt-astra",),
    ("gpt-sol",),
)

#: Effort asked of a substitute below the chosen model. Each provider fits it
#: to the deepest value the model takes (see ``reasoning_control``).
STEP_DOWN_EFFORT = "max"

_VERSION = re.compile(r"\d+(?:\.\d+)*")


@dataclass(frozen=True)
class RelayCandidate:
    """One substitute of the ladder, in the order it is tried."""

    preset_name: str
    model: str
    #: ``None`` keeps the effort the turn runs at.
    reasoning_effort: str | None


def model_family(slug: str) -> tuple[str, tuple[int, ...]]:
    """The family and version of a model slug.

    ``"anthropic/claude-opus-5.5"`` gives ``("claude-opus", (5, 5))``, and so
    does ``"claude-opus-5-5"``; ``"openai/gpt-6.1-sol"`` gives
    ``("gpt-sol", (6, 1))``. A slug without a version gives ``("", ())``.
    """
    name = slug.strip().lower().rsplit("/", 1)[-1].split(":", 1)[0]
    words: list[str] = []
    version: list[int] = []
    for token in name.split("-"):
        if _VERSION.fullmatch(token):
            version.extend(int(part) for part in token.split("."))
        elif token:
            words.append(token)
    if not words or not version:
        return "", ()
    return "-".join(words), tuple(version)


def _rung(family: str) -> int | None:
    for index, rung in enumerate(RELAY_LADDER):
        if family in rung:
            return index
    return None


def _maker(family: str) -> str:
    return family.split("-", 1)[0]


def relay_candidates(
    primary_model: str,
    models: Iterable[tuple[str, str]],
) -> list[RelayCandidate]:
    """The ladder's substitutes for *primary_model*, in the order they are tried.

    *models* lists the usable chat presets as ``(preset name, model slug)``,
    in catalog order. The chosen model's own family is left out: its older
    versions draw on the same quota and answer worse. Empty when the chosen
    model is not on the ladder; the generic chain then applies unchanged.
    """
    family, _version = model_family(primary_model)
    primary_rung = _rung(family)
    if primary_rung is None:
        return []
    chosen = primary_model.strip().lower()
    newest: dict[str, tuple[tuple[int, ...], str, str]] = {}
    for preset_name, slug in models:
        candidate_family, version = model_family(slug)
        if candidate_family == family or _rung(candidate_family) is None:
            continue
        if slug.strip().lower() == chosen:
            continue
        best = newest.get(candidate_family)
        if best is None or version > best[0]:
            newest[candidate_family] = (version, preset_name, slug.strip())
    ladder = [name for rung in RELAY_LADDER for name in rung]
    own = _maker(family)
    # Stable sort: the ladder's own order is kept inside each group.
    ladder.sort(key=lambda name: 0 if _maker(name) == own else 1)
    candidates: list[RelayCandidate] = []
    for name in ladder:
        if name not in newest:
            continue
        _found, preset_name, slug = newest[name]
        rung = _rung(name)
        weaker = rung is not None and rung > primary_rung
        candidates.append(
            RelayCandidate(
                preset_name=preset_name,
                model=slug,
                reasoning_effort=STEP_DOWN_EFFORT if weaker else None,
            )
        )
    return candidates
