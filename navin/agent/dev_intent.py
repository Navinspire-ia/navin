# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Deterministic dev intent routing for chat turns.

A coding request typed from a fresh chat (``#/new``) used to run with no module
scoping at all: the shell derives ``product_module`` from the visible view, and
the chat view has none. The agent then saw every skill and every command, and the
Code workbench never opened, so the work landed in a chat with no editor next to
it.

This module decides, from the user text only, whether the turn is development
work and should open the Code module. Rule-based on purpose, like
:mod:`navin.agent.media_intent`: the routing has to hold when the primary model
failed over to a smaller one.

The bar is deliberately higher than "mentions a file". A switch is user-visible,
so it needs an actual request to build, fix or inspect software, not merely a
technical word in passing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from navin.agent.media_intent import _normalize, detect_media_intent

CODE_MODULE = "code"

#: Composer modes that keep their own surface, whatever the text says. ``ask`` is
#: a read-only question and must not move the user; ``montage`` owns the media
#: desk and would otherwise lose it the moment a build word appears.
NON_SWITCHING_COMPOSER_MODES: frozenset[str] = frozenset({"ask", "montage"})

# Verbs that ask for software to be produced or changed.
_DEV_VERBS = re.compile(
    r"\b("
    r"code\w*|coder|develop\w*|developp\w*|implemente\w*|implementer|implement\w*|"
    r"refactor\w*|refacto|debug\w*|debogue\w*|deboguer|"
    r"corrige\w*|corriger|repare\w*|reparer|fix\w*|patch\w*|"
    r"compile\w*|build|builds|rebuild|deploie\w*|deployer|deploy\w*|"
    r"teste\w*|tester|migre\w*|migrer|migrate\w*|"
    r"optimise\w*|optimiser|optimize\w*|"
    r"installe\w*|installer|install\w*|configure\w*|configurer|setup"
    r")\b"
)

# Verbs that only mean dev work once paired with a software noun: "cree un site"
# is dev, "cree une image" is not.
_NEUTRAL_BUILD_VERBS = re.compile(
    r"\b("
    r"cree\w*|creer|gener\w*|fais|faire|ajoute\w*|ajouter|"
    r"create\w*|generate\w*|make|add|write|writes|ecris|ecrire|"
    r"modifie\w*|modifier|change\w*|changer|update\w*|met a jour|mettre a jour|"
    r"supprime\w*|supprimer|remove\w*|delete\w*|renomme\w*|rename\w*|"
    r"analyse\w*|analyser|explique\w*|expliquer|explain\w*|review\w*|audit\w*|"
    r"lis|lire|read|inspecte\w*|inspecter|verifie\w*|verifier|check\w*"
    r")\b"
)

# Software nouns. Kept apart from media nouns so "cree une video" never lands
# here even though both share the same verb.
_SOFTWARE_NOUNS = re.compile(
    r"\b("
    r"code|fonction|fonctions|function|functions|classe|classes|class|classes|"
    r"methode|methodes|method|methods|variable|variables|"
    r"composant|composants|component|components|hook|hooks|"
    r"script|scripts|module|modules|package|packages|bibliotheque|library|"
    r"api|apis|endpoint|endpoints|route|routes|middleware|handler|handlers|"
    # No "client": in French it is a customer far more often than a network peer.
    r"backend|frontend|serveur|server|"
    r"base de donnees|database|db|table|tables|schema|migration|migrations|"
    r"requete sql|query|orm|"
    r"test|tests|testing|unitaire|unitaires|e2e|"
    r"bug|bugs|erreur|erreurs|error|errors|exception|exceptions|"
    r"crash|stacktrace|stack trace|traceback|regression|"
    r"site|site web|website|webapp|web app|app|appli|application|applications|"
    r"page|pages|landing|landing page|dashboard|interface|ui|ux|"
    r"formulaire|form|forms|bouton|button|buttons|"
    r"projet|projets|project|repo|repository|depot|"
    r"branche|branch|commit|commits|merge|rebase|pull request|"
    r"build|compilation|bundle|lint|linter|typecheck|ci|cd|pipeline|"
    r"docker|dockerfile|container|conteneur|kubernetes|k8s|"
    r"dependance|dependances|dependency|dependencies|"
    r"config|configuration|env|variables d environnement|"
    r"authentification|authentication|auth|login|oauth|jwt|token|"
    r"cache|caching|websocket|websockets|graphql|rest|grpc|"
    # No bare "performance": business performance is a marketing question.
    r"latence|latency|memory leak|fuite memoire|"
    r"refactoring|architecture|monorepo|workspace"
    r")\b"
)

# Languages, runtimes and frameworks. Naming one is strong evidence on its own
# when a verb is present.
_TECH_NAMES = re.compile(
    r"\b("
    r"python|javascript|typescript|js|ts|tsx|jsx|node|nodejs|deno|bun|"
    r"react|vue|svelte|angular|next|nextjs|nuxt|remix|astro|vite|webpack|"
    r"rust|golang|go|java|kotlin|swift|objective c|c\+\+|cpp|csharp|c#|php|ruby|"
    r"scala|elixir|erlang|haskell|lua|perl|bash|shell|zsh|powershell|"
    r"django|flask|fastapi|rails|laravel|spring|express|nestjs|"
    r"tailwind|css|scss|sass|html|sql|postgres|postgresql|mysql|sqlite|"
    r"mongodb|redis|kafka|rabbitmq|elasticsearch|"
    r"pytest|jest|vitest|playwright|cypress|selenium|"
    r"git|github|gitlab|npm|yarn|pnpm|pip|poetry|uv|cargo|maven|gradle|"
    r"terraform|ansible|nginx|apache|aws|gcp|azure|vercel|netlify|supabase|"
    r"pandas|numpy|pytorch|tensorflow|sklearn|langchain"
    r")\b"
)

# A path or a filename with a source extension. Matched on the raw text, before
# normalization eats the separators.
_FILE_HINT = re.compile(
    r"(?:"
    r"[\w./~-]*[\w-]+\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|vue|svelte|rs|go|java|kt|"
    r"swift|c|h|cc|cpp|hpp|cs|php|rb|scala|ex|exs|hs|lua|pl|sh|bash|zsh|ps1|"
    r"html|htm|css|scss|sass|less|sql|json|yaml|yml|toml|ini|cfg|env|lock|"
    r"gradle|dockerfile|md|mdx)\b"
    r"|(?:^|\s)(?:\./|\.\./|/)[\w.-]+/[\w./-]+"
    r"|\b(?:src|tests?|lib|app|components?|packages?|services?)/[\w./-]+"
    r")",
    re.IGNORECASE,
)

# Error text pasted into the chat is a debugging request even with no verb.
_ERROR_SIGNATURES = re.compile(
    r"("
    r"traceback \(most recent call last\)|"
    r"\b(?:type|value|key|index|attribute|import|syntax|runtime|reference|name)error\b|"
    r"\bexception in thread\b|\bsegmentation fault\b|\bnullpointerexception\b|"
    r"\bmodulenotfounderror\b|\bcannot find module\b|\bunexpected token\b|"
    r"\bts\d{4}\b|\berror ts\d+\b|\bnpm err\b|\bfatal:\b"
    r")"
)

# Phrases that name the coding surface outright.
_EXPLICIT_DEV_PHRASES = re.compile(
    r"("
    r"mon code|le code|du code|my code|the code|"
    r"mon projet|mon appli|mon application|mon site|my project|my app|my site|"
    r"ce fichier|this file|le repo|the repo|"
    r"ouvre le workbench|open the workbench|mode code|code module"
    r")"
)


@dataclass(frozen=True, slots=True)
class DevIntent:
    """One turn's dev routing decision."""

    module: str
    #: Which signals fired, for logs and tests.
    matched: tuple[str, ...]
    #: A named language, file or error, rather than a verb plus a generic noun.
    strong: bool


def _composer_mode_allows_switch(composer_mode: str | None) -> bool:
    mode = (composer_mode or "").strip().lower()
    return mode not in NON_SWITCHING_COMPOSER_MODES


def detect_dev_intent(text: str | None) -> DevIntent | None:
    """Return the dev routing decision for *text*, or ``None`` when not dev work.

    Media requests lose on purpose: "cree une video de 10 secondes" shares its
    verb with "cree une api", and the media desk owns that turn.
    """
    raw = text or ""
    normalized = _normalize(raw)
    if not normalized:
        return None

    media = detect_media_intent(raw)
    if media is not None:
        return None

    matched: list[str] = []
    has_file = bool(_FILE_HINT.search(raw))
    has_error = bool(_ERROR_SIGNATURES.search(normalized))
    has_tech = bool(_TECH_NAMES.search(normalized))
    has_software_noun = bool(_SOFTWARE_NOUNS.search(normalized))
    has_dev_verb = bool(_DEV_VERBS.search(normalized))
    has_neutral_verb = bool(_NEUTRAL_BUILD_VERBS.search(normalized))
    has_phrase = bool(_EXPLICIT_DEV_PHRASES.search(normalized))

    for label, hit in (
        ("file", has_file),
        ("error", has_error),
        ("tech", has_tech),
        ("software", has_software_noun),
        ("dev_verb", has_dev_verb),
        ("phrase", has_phrase),
    ):
        if hit:
            matched.append(label)

    # An error dump or a concrete path is self-explanatory: someone is working on
    # software right now, verb or not.
    strong = has_file or has_error or (has_tech and (has_dev_verb or has_neutral_verb))
    if strong:
        return DevIntent(module=CODE_MODULE, matched=tuple(matched), strong=True)

    # Otherwise a verb has to meet a software noun. Either verb family works: a
    # dev verb carries the intent by itself ("debug le login"), and a neutral one
    # needs the noun to disambiguate ("cree une api").
    if (has_dev_verb or has_neutral_verb) and (has_software_noun or has_phrase):
        return DevIntent(module=CODE_MODULE, matched=tuple(matched), strong=False)

    # A dev verb whose object is implied still counts when it cannot be read as
    # anything else ("refactor", "corrige les erreurs de lint").
    if has_dev_verb and has_phrase:
        return DevIntent(module=CODE_MODULE, matched=tuple(matched), strong=False)

    return None


def product_module_for_turn(
    text: str | None,
    *,
    composer_mode: str | None = None,
    current_module: str | None = None,
) -> str | None:
    """Return the module to switch to, or ``None`` to leave the shell alone.

    Only an unscoped shell is moved, which is the chat view this exists for. A
    user sitting in SEO or Ads picked that surface on purpose, and a build word in
    their sentence is not a reason to take it away from them.
    """
    if not _composer_mode_allows_switch(composer_mode):
        return None
    if (current_module or "").strip():
        return None
    intent = detect_dev_intent(text)
    if intent is None:
        return None
    return intent.module
