# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The language Navin answers in: the one the user writes in, named explicitly.

"Reply in the user's language" in a long English system prompt was not enough:
everything else the model reads is English (tool output, files, Navin's own
reminders such as "Validation pending"), and some models (GLM, Kimi...) slid
into English mid-task. Each turn now states the language by name, detected
from the user's message; a short "ok" or "vasy" keeps the chat's language.

Pasted material never decides it. A message is often a short remark around a
long paste in another language (a log, a journal, another assistant's answer):
the paste used to outvote the user's own words, so a French user pasting a
Dutch answer was answered in Dutch and one pasting an English log in English.
The message is read sentence by sentence; when the sentences disagree, the
user's own words win: the first and the last sentences (the words around the
paste), the chat's language to break a tie, and the shorter side (the paste is
the long one). A word several languages share ("de", "en", "je", "met" are
French as much as Dutch) weighs a fraction, so a French message cannot score
Dutch. The chat's language is kept in its session too, so a restart does not
forget it.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from navin.agent.tools.context import RequestContext
from navin.runtime_context import RuntimeContextBlock

_SCRIPTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Arabic", re.compile(r"[\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufeff]")),
    ("Hebrew", re.compile(r"[\u0590-\u05ff]")),
    ("Russian", re.compile(r"[\u0400-\u04ff]")),
    ("Greek", re.compile(r"[\u0370-\u03ff]")),
    ("Japanese", re.compile(r"[\u3040-\u30ff]")),
    ("Korean", re.compile(r"[\uac00-\ud7af]")),
    ("Chinese", re.compile(r"[\u4e00-\u9fff]")),
    ("Hindi", re.compile(r"[\u0900-\u097f]")),
    ("Thai", re.compile(r"[\u0e00-\u0e7f]")),
)

# Frequent words of each language, typed with or without accents. A word that
# appears in several lists weighs 1/n for each of them (see _WEIGHTS).
_WORDS: dict[str, frozenset[str]] = {
    "French": frozenset(
        "je tu il elle on nous vous ils elles les des une un le la de du au aux et à a y en est "
        "sont ne pas que qui quoi dont où ou mais donc car ni si pour avec dans sur sous vers par "
        "sans chez entre puis alors ensuite enfin aussi encore déjà deja toujours jamais bien très "
        "tres trop peu plus moins tout tous toute toutes rien quelque quelques chaque autre autres "
        "même meme cette ce ces cet celui celle ceux mon ma mes ton ta tes son sa ses notre votre "
        "leur leurs moi toi lui eux me te se ça ca cela ceci là voilà voila ici comme comment "
        "pourquoi quand combien quel quelle quels quelles fait faire fais faut peut peux veux veut "
        "dois doit vais vas va vont ai as avons avez ont été ete être etre avoir mettre met mets "
        "mis prendre voir dire savoir besoin oui non merci stp svp c'est j'ai n'est qu'il qu'on "
        "d'un d'une l'autre rajoute ajoute corrige regarde affiche enleve enlève retire verifie "
        "vérifie lance teste modifie change supprime crée cree utilise dis montre explique après "
        "apres avant pendant depuis maintenant aujourd'hui demain hier parfois souvent normal "
        "pourtant parle parles parler français francais anglais langue".split()
    ),
    "English": frozenset(
        "the and is are you your for with this that what why how when can could would should "
        "please thanks it not but have has was were will from into there their them they i my "
        "me do does did make fix add show remove check an to of in at now again just we our "
        "us be been want need use run create build write explain update delete change move "
        "test tests find look all any some new also then than which where who here "
        "i'm it's don't can't let's let get got give see know think a on no yes if else or so "
        "up out by as about after before because still only very more most much many other "
        "same like way work every each those these its his her he she one already never "
        "always cannot must may error failed exit line lines file files output command done "
        "normal comment".split()
    ),
    "Spanish": frozenset(
        "el los las una un unos unas es son para con en por que qué pero como cómo cuando cuándo "
        "donde dónde quién cuál yo tú usted ustedes nosotros ellos mi mis tu tus su sus nuestro "
        "esto esta este ese esa eso hacer puedes puedo puede quiero necesito tengo tiene hay está "
        "están gracias sí si no también tambien ya muy bien todo todos nada algo y a de del al lo "
        "le se me te aquí aqui ahora más mas sin sobre entre desde hasta porque favor ver".split()
    ),
    "German": frozenset(
        "der die das und ist sind nicht ich du er sie es wir ihr mit für fur auf in an aus bei "
        "nach von zu zum zur im am aber oder wie warum wann wo was wer welche dass ob wenn weil "
        "denn auch noch schon nur sehr mehr immer nie jetzt hier dort kannst kann können konnen "
        "muss müssen mussen soll sollen will wollen möchte mochte habe hast hat haben bin bist war "
        "waren wird werden wurde ein eine einen einem einer mein dein sein ihre unser machen mache "
        "bitte danke ja nein gut".split()
    ),
    "Italian": frozenset(
        "il lo la gli le i un una uno è e non che chi cosa come quando dove perché perche ma però "
        "pero anche ancora già gia sempre mai molto poco più piu meno tutto tutti questo questa "
        "quello quella con per tra fra da di del della dei delle degli nel nella sul sulla io tu "
        "lui lei noi voi loro mio mia tuo tua suo sua nostro vostro fare fai faccio puoi posso "
        "voglio devo devi grazie prego sì si ciao bene ora adesso qui qua".split()
    ),
    "Portuguese": frozenset(
        "o os as uma um é e são sao não nao que para com em do da dos das no na se por mais muito "
        "também tambem já ja ainda eu você voce ele ela nós nos eles meu minha seu sua isso isto "
        "este esta esse essa aqui agora fazer pode quero preciso obrigado obrigada sim como quando "
        "onde porque mas ou então entao de a".split()
    ),
    "Dutch": frozenset(
        "de het een en is zijn niet ik jij je hij zij ze wij we jullie u mij jou hem haar ons hun "
        "mijn jouw uw onze dit dat deze die er daar hier wat wie waar hoe waarom wanneer welke welk "
        "met voor op van naar bij uit over om te tot door tegen zonder maar want omdat als dan dus "
        "toch wel geen nog ook al alleen ja nee graag even gewoon zo heel veel meer minder alle "
        "alles iets niets kan kunnen kun kunt moet moeten mag mogen wil willen zal zullen zou "
        "zouden heb hebt heeft hebben had hadden ben bent was waren wordt worden werd werden "
        "gedaan gemaakt maken maak aangemaakt gecontroleerd bericht antwoord vorige nu steeds weer "
        "nieuw nieuwe goed goede dank bedankt alsjeblieft in".split()
    ),
}

# word -> {language: weight}. "de" is French, Dutch, Spanish and Portuguese at
# once and proves none of them: 0.25 each. "het" is Dutch alone: 1.
_WEIGHTS: dict[str, dict[str, float]] = {}
for _name, _vocabulary in _WORDS.items():
    for _word in _vocabulary:
        _WEIGHTS.setdefault(_word, {})[_name] = 0.0
for _per_language in _WEIGHTS.values():
    for _name in _per_language:
        _per_language[_name] = 1.0 / len(_per_language)

_FENCE = re.compile(r"```.*?```", re.S)
_NOISE = re.compile(
    r"`[^`]*`|https?://\S+|[\w./\\-]+\.(?:py|ts|tsx|js|json|md|yaml|yml|toml|sh|log|txt|csv)\b|#\d+"
)
_COMMAND = re.compile(r"^\s*/[\w-]+\s*")
# Where one thought ends and the next begins: sentence punctuation, a line
# break, or the glyphs a pasted journal or quote puts in front of its lines.
_BOUNDARY = re.compile(r"(?<=[.!?;:])\s+|\n+|\s*[>│├└┌┐┘─━•·✓✔✗×✖→▸▪◦]+\s*")
_WORD = re.compile(r"[a-zà-ÿœæ']+", re.I)

MIN_WORDS = 3
MIN_SCORE = 1.5
MARGIN = 1.5
METADATA_KEY = "reply_language"

_REMEMBERED: OrderedDict[str, str] = OrderedDict()
_REMEMBERED_MAX = 512


def _scoring_words(tokens: list[str]) -> list[str]:
    """Lower-cased words; an elision also yields its parts ("d'un" counts "un")."""
    out: list[str] = []
    for token in tokens:
        word = token.strip("'").lower()
        if not word:
            continue
        out.append(word)
        if "'" in word:
            out.extend(part for part in word.split("'") if len(part) >= 2)
    return out


def _segment_language(segment: str) -> tuple[str, int] | None:
    """The language of one sentence and its length in words, or None when unclear."""
    letters = sum(1 for ch in segment if ch.isalpha())
    if letters == 0:
        return None
    for name, pattern in _SCRIPTS:
        hits = len(pattern.findall(segment))
        if hits >= 2 and hits * 2 >= letters:
            return name, len(segment.split())
    tokens = _WORD.findall(segment)
    if len(tokens) < MIN_WORDS:
        return None
    scores = dict.fromkeys(_WORDS, 0.0)
    for word in _scoring_words(tokens):
        for name, weight in _WEIGHTS.get(word, {}).items():
            scores[name] += weight
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best, best_score = ranked[0]
    runner_up = ranked[1][1]
    if best_score < MIN_SCORE or best_score < runner_up * MARGIN:
        return None
    return best, len(tokens)


def detect_language(text: str | None, remembered: str | None = None) -> str | None:
    """The language of a user message, or None when it is too short to tell.

    Sentence by sentence; when they disagree the user's own words decide (see
    the module docstring), ``remembered`` being the chat's language so far.
    """
    cleaned = _COMMAND.sub("", text or "", count=1)
    cleaned = _NOISE.sub(" ", _FENCE.sub(" ", cleaned))
    detected = [found for found in map(_segment_language, _BOUNDARY.split(cleaned)) if found]
    if not detected:
        return None
    first, last = detected[0][0], detected[-1][0]
    if first == last:
        return first
    if remembered in (first, last):
        return remembered
    totals: dict[str, int] = {}
    for language, count in detected:
        totals[language] = totals.get(language, 0) + count
    # The paste is the long side; the user's own words are the short one.
    return first if totals[first] < totals[last] else last


def _store(sessions: Any) -> Any:
    return sessions() if callable(sessions) else sessions


def _session(sessions: Any, session_key: str | None) -> Any:
    """The chat's session when the store already has it; never creates one."""
    store = _store(sessions)
    peek = getattr(store, "peek", None)
    if not session_key or peek is None:
        return None
    return peek(session_key)


def remembered_language(session_key: str | None, sessions: Any = None) -> str | None:
    """The language this chat last used: this process first, then its session."""
    key = session_key or ""
    language = _REMEMBERED.get(key)
    if language is None:
        session = _session(sessions, session_key)
        metadata = getattr(session, "metadata", None) or {}
        stored = metadata.get(METADATA_KEY)
        language = stored if isinstance(stored, str) and stored else None
    return language


def _remember(key: str, language: str, sessions: Any, session_key: str | None) -> None:
    _REMEMBERED[key] = language
    _REMEMBERED.move_to_end(key)
    while len(_REMEMBERED) > _REMEMBERED_MAX:
        _REMEMBERED.popitem(last=False)
    session = _session(sessions, session_key)
    metadata = getattr(session, "metadata", None)
    if isinstance(metadata, dict) and metadata.get(METADATA_KEY) != language:
        # On the cached session the turn is about to save: no extra write.
        metadata[METADATA_KEY] = language


def reply_language(session_key: str | None, text: str | None, sessions: Any = None) -> str | None:
    """Detected language of this message, else the one this chat last used.

    ``sessions`` (a session store, or a callable returning one) keeps the
    language in the chat's session so a restart does not forget it.
    """
    key = session_key or ""
    remembered = remembered_language(session_key, sessions)
    language = detect_language(text, remembered)
    if language:
        _remember(key, language, sessions, session_key)
        return language
    return remembered


def _block(language: str | None) -> RuntimeContextBlock | None:
    if not language:
        return None
    content = (
        f"Reply language: {language}. Write every message to the user in {language} "
        "(progress notes, questions, summaries, errors), even though tools, files, "
        "system notes and Navin's own reminders are in English. Code, commands, "
        "file names and identifiers stay as they are."
    )
    return RuntimeContextBlock(source="reply_language", content=content)


def reply_language_provider(sessions: Any = None) -> Callable[[RequestContext], Any]:
    """The runtime-context provider, remembering each chat's language in its session."""

    async def provider(request: RequestContext) -> RuntimeContextBlock | None:
        return _block(reply_language(request.session_key, request.original_user_text, sessions))

    return provider


async def reply_language_context_provider(request: RequestContext) -> RuntimeContextBlock | None:
    """The provider without a session store (this process remembers the chat)."""
    return _block(reply_language(request.session_key, request.original_user_text))
