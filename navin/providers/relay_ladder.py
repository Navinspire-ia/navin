# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Which model takes over when the chosen one cannot answer.

The models of the plan form one ladder. When the chosen model cannot answer
(quota reached, rate limited, down), the step goes to the next one of the
ladder still available, in the order the user set (2026-11-06):

1. Claude Opus and Claude Fable, which relay each other;
2. GPT-6 Astra, then Claude Sonnet, then GPT-6.1 Sol;
3. the release before the newest Claude Fable, then the one before the
   newest Claude Opus;
4. Kimi, Grok, GLM, then GLM Flash.

The chosen model is left out wherever it sits, and a model off the ladder is
relayed by the whole ladder. Each Bedrock model draws on its own quota, so an
older Fable or Opus still answers when the newest one is limited.

The newest Opus and Fable keep the effort the turn runs at. Every other model
takes over at its deepest effort: xhigh for GPT, max for the others, which
each provider fits to what the model takes (see ``reasoning_control``). An
effort the user picked in the chat is kept by every model taking over
(``chat_chosen_effort``).

Families are matched by name without their version, and the releases are
read from the model list, so the ladder follows new releases without an edit
here: Claude Opus 5.6 takes the place of 5.5 the day it reaches the catalog,
and 5.5 becomes the release before it. The factory serves this ladder to the
managed providers only (Navin, Bedrock, LiteLLM), and never makes a step leave
its provider.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

#: The models that take over, in the order they are tried: a family and a
#: release, 0 for the newest version of the family in the model list and 1
#: for the one before it.
RELAY_ORDER: tuple[tuple[str, int], ...] = (
    ("claude-opus", 0),
    ("claude-fable", 0),
    ("gpt-astra", 0),
    ("claude-sonnet", 0),
    ("gpt-sol", 0),
    ("claude-fable", 1),
    ("claude-opus", 1),
    ("kimi", 0),
    ("grok", 0),
    ("glm", 0),
    ("glm-flash", 0),
)

#: The flagships: their newest release keeps the effort the turn runs at.
PEER_FAMILIES = frozenset({"claude-opus", "claude-fable"})

#: The deepest effort asked of every other model taking over. Each provider
#: fits it to what the model takes (see ``reasoning_control``).
DEEPEST_EFFORT = "max"
#: GPT models stop at xhigh.
GPT_DEEPEST_EFFORT = "xhigh"

#: True while a step runs at an effort the user picked in the chat.
_CHAT_EFFORT: ContextVar[bool] = ContextVar("navin_chat_chosen_effort", default=False)


@contextmanager
def chat_chosen_effort(chosen: bool) -> Iterator[None]:
    """Within the block, a model taking over keeps the step's effort when *chosen*."""
    token = _CHAT_EFFORT.set(bool(chosen))
    try:
        yield
    finally:
        _CHAT_EFFORT.reset(token)


def effort_chosen_in_chat() -> bool:
    """True when the running step's effort was picked by the user in the chat."""
    return _CHAT_EFFORT.get()


_VERSION = re.compile(r"\d+(?:\.\d+)*")
#: Moonshot writes the version after a "k": "kimi-k3".
_KIMI_VERSION = re.compile(r"k(\d+(?:\.\d+)*)")
#: A Bedrock id ("us.anthropic.claude-opus-5-5-v1:0") names a region and a
#: vendor before the model, and a revision after it.
_BEDROCK_PREFIX = re.compile(r"^(?:[a-z]+\.)+(?=[a-z])")
_REVISION = re.compile(r"v\d+")


@dataclass(frozen=True)
class RelayCandidate:
    """One model of the ladder, in the order it is tried."""

    preset_name: str
    model: str
    #: ``None`` keeps the effort the turn runs at.
    reasoning_effort: str | None


def model_family(slug: str) -> tuple[str, tuple[int, ...]]:
    """The family and version of a model slug.

    ``"anthropic/claude-opus-5.5"`` gives ``("claude-opus", (5, 5))``, and so
    do ``"claude-opus-5-5"`` and the Bedrock id
    ``"us.anthropic.claude-opus-5-5-v1:0"``; ``"openai/gpt-6.1-sol"`` gives
    ``("gpt-sol", (6, 1))`` and ``"moonshotai/kimi-k3"`` ``("kimi", (3,))``.
    A slug without a version gives ``("", ())``.
    """
    name = slug.strip().lower().rsplit("/", 1)[-1].split(":", 1)[0]
    tokens = [token for token in _BEDROCK_PREFIX.sub("", name).split("-") if token]
    if len(tokens) > 1 and _REVISION.fullmatch(tokens[-1]):
        tokens.pop()
    words: list[str] = []
    version: list[int] = []
    for token in tokens:
        kimi = _KIMI_VERSION.fullmatch(token) if words == ["kimi"] else None
        if _VERSION.fullmatch(token):
            version.extend(int(part) for part in token.split("."))
        elif kimi is not None:
            version.extend(int(part) for part in kimi.group(1).split("."))
        else:
            words.append(token)
    if not words or not version:
        return "", ()
    return "-".join(words), tuple(version)


def relay_effort(family: str, release: int) -> str | None:
    """The effort a model of *family* takes over at; ``None`` keeps the turn's."""
    if family in PEER_FAMILIES and release == 0:
        return None
    return GPT_DEEPEST_EFFORT if family.startswith("gpt-") else DEEPEST_EFFORT


def relay_candidates(
    primary_model: str,
    models: Iterable[tuple[str, str]],
) -> list[RelayCandidate]:
    """The ladder's models for a step chosen on *primary_model*, in the order they are tried.

    *models* lists the usable chat presets as ``(preset name, model slug)``,
    in catalog order; the first preset of a model is the one used. The chosen
    model keeps its place among the releases of its family, so the release
    before it stays "the one before" even when the chosen preset is not in
    *models*.
    """
    chosen = primary_model.strip().lower()
    chosen_identity = model_family(primary_model)
    releases: dict[str, list[tuple[tuple[int, ...], str | None, str]]] = {}
    for preset_name, slug in models:
        cleaned = slug.strip()
        family, version = model_family(cleaned)
        if family:
            releases.setdefault(family, []).append((version, preset_name, cleaned))
    family, version = chosen_identity
    if family and all(known != version for known, _name, _slug in releases.get(family, [])):
        releases.setdefault(family, []).append((version, None, primary_model.strip()))
    for entries in releases.values():
        # Newest first; the catalog order settles two presets of one release,
        # and the first of them stands for the release.
        entries.sort(key=lambda entry: entry[0], reverse=True)
        entries[:] = [entry for index, entry in enumerate(entries) if index == 0 or entries[index - 1][0] != entry[0]]

    candidates: list[RelayCandidate] = []
    for family, release in RELAY_ORDER:
        entries = releases.get(family, [])
        if release >= len(entries):
            continue
        version, preset_name, slug = entries[release]
        if preset_name is None or slug.lower() == chosen or (family, version) == chosen_identity:
            continue
        candidates.append(
            RelayCandidate(preset_name=preset_name, model=slug, reasoning_effort=relay_effort(family, release))
        )
    return candidates
