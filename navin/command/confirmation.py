"""Short user confirmations ("oui", "go ahead") answering the agent's question.

The intent gate reads a bare "oui" as low-info chit-chat. That is right on a
cold open and wrong right after the agent asked "shall I start?": the gate
then forbids tools and asks what to build, the user answers "oui" again, and
the pair loops forever without a single tool call.

This module recovers what the user actually agreed to - the question the agent
asked at the end of the previous turn - so the gate can treat the confirmation
as the build target instead of as a greeting.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from navin.session.turn_continuation import ends_with_user_question

# Words that carry the "yes, go" meaning. One of them must be present.
_AFFIRMATIVE_WORDS = frozenset(
    {
        "oui", "ouais", "ouaip", "ouep", "yes", "yep", "yeah", "yup",
        "ok", "okay", "oki", "okey", "dac", "daccord", "accord", "accepte",
        "go", "gogo", "vas", "vazy", "allez", "allons", "lance", "lancez",
        "fonce", "banco", "parti", "partez", "demarre", "démarre", "start",
        "sure", "affirmatif", "confirme", "confirmé", "confirmed",
        "valide", "validé", "continue", "continuez", "poursuis", "proceed",
        "ahead", "carrement", "carrément", "certainement", "absolutely",
        "evidemment", "évidemment", "exactement", "volontiers",
        # "bien sûr" / "of course": the adverb is the affirmative half.
        "sur", "sûr", "course",
        "yalla", "aywa",
    }
)

# Words that ride along with a confirmation without adding a target.
_CONFIRM_FILLER_WORDS = frozenset(
    {
        "y", "le", "la", "les", "ca", "ça", "c", "cest", "est", "s",
        "il", "elle", "on", "je", "tu", "te", "toi", "nous", "vous", "moi",
        "de", "du", "des", "a", "à", "au", "et", "en", "pour", "avec", "tout",
        "toute", "suite", "maintenant", "now", "mtn", "alors", "donc", "bah",
        "ben", "voila", "voilà", "merci", "thanks", "cool", "super", "great",
        "parfait", "nickel", "top", "bien", "please", "stp", "svp",
        "it", "do", "just", "lets", "let", "us", "we", "i", "you", "the",
        "of", "up", "then", "and", "with", "all", "right",
        "navin", "frere", "frère", "bro", "mec", "man",
        # Elided heads left by the split on apostrophes: d'accord, c'est, j'ai.
        "d", "l", "j", "n", "m", "t", "qu",
    }
)

# Split on apostrophes and hyphens too, so "d'accord" and "vas-y" read as the
# two short words they are instead of one unknown token.
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

# A confirmation is short by nature. A longer sentence built only from these
# words is almost certainly not one, so the gate keeps its normal verdict.
_MAX_CONFIRMATION_WORDS = 8

# The question is injected into the prompt as the build target, so it must stay
# a target and not drag a whole essay into the brief.
_MAX_QUESTION_CHARS = 400


def focus_is_affirmative(text: str) -> bool:
    """True when *text* is only a green light ("oui", "ok vas-y", "go ahead").

    Any substantive word (a noun, a file name, a new instruction) disqualifies
    it: that message stands on its own and the normal intent gate applies.
    """
    words = _WORD_RE.findall((text or "").casefold())
    if not words or len(words) > _MAX_CONFIRMATION_WORDS:
        return False
    if not any(word in _AFFIRMATIVE_WORDS for word in words):
        return False
    return all(
        word in _AFFIRMATIVE_WORDS or word in _CONFIRM_FILLER_WORDS
        for word in words
    )


def last_assistant_question(messages: Iterable[Mapping[str, Any]] | None) -> str:
    """Return the question the agent left the user with, or "".

    Only the most recent assistant message counts: an older one was already
    answered, and pairing a fresh "oui" with it would invent an intent.
    """
    if not messages:
        return ""
    for message in reversed(list(messages)):
        if not isinstance(message, Mapping):
            continue
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            # Tool-call records carry no prose; keep walking back to the last
            # thing the user actually read.
            continue
        if not ends_with_user_question(content):
            return ""
        return _question_line(content)
    return ""


def _question_line(content: str) -> str:
    """The last interrogative line of *content*, trimmed for prompt use."""
    lines = [line.strip() for line in content.strip().splitlines()]
    lines = [line for line in lines if line]
    question = lines[-1] if lines else ""
    if len(question) > _MAX_QUESTION_CHARS:
        question = question[:_MAX_QUESTION_CHARS].rstrip() + "…"
    return question


def confirmed_pending_question(
    focus: str,
    messages: Iterable[Mapping[str, Any]] | None,
) -> str:
    """The agent's own question when *focus* is the user saying yes to it.

    Empty string when this turn is not a confirmation, which leaves every
    existing gate verdict untouched.
    """
    if not focus_is_affirmative(focus):
        return ""
    return last_assistant_question(messages)
