# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The language Navin answers in: the one the user writes in, named explicitly.

"Reply in the user's language" in a long English system prompt was not enough:
everything else the model reads is English (tool output, files, Navin's own
reminders such as "Validation pending"), and some models (GLM, Kimi...) slid
into English mid-task. Each turn now states the language by name, detected
from the user's message; a short "ok" or "vasy" keeps the chat's language.
"""

from __future__ import annotations

import re
from collections import OrderedDict

from navin.agent.tools.context import RequestContext
from navin.runtime_context import RuntimeContextBlock

_SCRIPTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Arabic", re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")),
    ("Hebrew", re.compile(r"[֐-׿]")),
    ("Russian", re.compile(r"[Ѐ-ӿ]")),
    ("Greek", re.compile(r"[Ͱ-Ͽ]")),
    ("Japanese", re.compile(r"[぀-ヿ]")),
    ("Korean", re.compile(r"[가-힯]")),
    ("Chinese", re.compile(r"[一-鿿]")),
    ("Hindi", re.compile(r"[ऀ-ॿ]")),
    ("Thai", re.compile(r"[฀-๿]")),
)

# Frequent short words, typed with or without accents.
_WORDS: dict[str, frozenset[str]] = {
    "French": frozenset(
        "je tu il elle nous vous ils les des une est sont pour avec dans pas que qui sur mais "
        "ou donc car mon ma mes ton ta tes son sa ses notre votre leur moi toi ca ça cette ce ces "
        "fait faire fais quand comment pourquoi aussi tout tous toujours encore deja déjà bien "
        "oui non merci peux veux dois faut c'est j'ai n'est qu'il du au aux le la un "
        "rajoute corrige regarde affiche met mets enleve enlève verifie vérifie".split()
    ),
    "English": frozenset(
        "the and is are you your for with this that what why how when can could would should "
        "please thanks it not but have has was were will from into there their them they i my "
        "me do does did make fix add show remove check an to of in at now again just we our "
        "us be been want need use run create build write explain update delete change move "
        "test tests find look all any some new also then than which where who here "
        "i'm it's don't can't let's let get got give see know think".split()
    ),
    "Spanish": frozenset(
        "el los las una es son para con en por que qué pero como cómo cuando cuándo por qué "
        "yo tú usted nosotros mi mis tu su sus esto esta este hacer puedes quiero gracias sí no "
        "también ya muy".split()
    ),
    "German": frozenset(
        "der die das und ist sind nicht ich du sie wir ihr mit für auf aber wie warum wann "
        "kannst bitte danke ein eine einen auch noch schon mein dein machen".split()
    ),
    "Italian": frozenset(
        "il lo gli una è sono per con non che perché come quando io tu lui lei noi voi mio "
        "tuo anche ancora già fare puoi voglio grazie della delle degli".split()
    ),
    "Portuguese": frozenset(
        "o os as uma é são para com não que porque como quando eu você nós meu minha seu sua "
        "também ainda já fazer pode quero obrigado obrigada da do das dos".split()
    ),
    "Dutch": frozenset(
        "de het een en is zijn niet ik jij je wij met voor op maar hoe waarom wanneer kun "
        "alsjeblieft dank ook nog al mijn maken".split()
    ),
}

_NOISE = re.compile(r"```.*?```|`[^`]*`|https?://\S+|[\w./\\-]+\.(?:py|ts|tsx|js|json|md|yaml|yml|toml|sh)\b", re.S)
_WORD = re.compile(r"[a-zà-ÿœæ']+", re.I)
_REMEMBERED: OrderedDict[str, str] = OrderedDict()
_REMEMBERED_MAX = 512


def detect_language(text: str | None) -> str | None:
    """The language of a user message, or None when it is too short to tell."""
    cleaned = _NOISE.sub(" ", text or "")
    letters = sum(1 for ch in cleaned if ch.isalpha())
    if letters == 0:
        return None
    for name, pattern in _SCRIPTS:
        if len(pattern.findall(cleaned)) >= max(2, letters * 0.3):
            return name
    words = [word.strip("'").lower() for word in _WORD.findall(cleaned)]
    words = [word for word in words if word]
    if len(words) < 3:
        return None
    scores = {name: sum(1 for word in words if word in vocabulary) for name, vocabulary in _WORDS.items()}
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best, best_score = ranked[0]
    runner_up = ranked[1][1]
    if best_score < 2 or best_score < runner_up * 1.5:
        return None
    return best


def reply_language(session_key: str | None, text: str | None) -> str | None:
    """Detected language of this message, else the one this chat last used."""
    language = detect_language(text)
    key = session_key or ""
    if language:
        _REMEMBERED[key] = language
        _REMEMBERED.move_to_end(key)
        while len(_REMEMBERED) > _REMEMBERED_MAX:
            _REMEMBERED.popitem(last=False)
        return language
    return _REMEMBERED.get(key)


async def reply_language_context_provider(request: RequestContext) -> RuntimeContextBlock | None:
    language = reply_language(request.session_key, request.original_user_text)
    if not language:
        return None
    content = (
        f"Reply language: {language}. Write every message to the user in {language} "
        "(progress notes, questions, summaries, errors), even though tools, files, "
        "system notes and Navin's own reminders are in English. Code, commands, "
        "file names and identifiers stay as they are."
    )
    return RuntimeContextBlock(source="reply_language", content=content)
