"""Keep an investigation on the targets the user named.

A brief that says "compare the prod and preprod branches of forgejo/lynara,
the celery of the crm service breaks after the merge" has already done the
hard part: it names where to look. The failure this module exists for is an
agent that reads that brief and then lists the whole repository, opens the
README and walks every service "to get oriented", while the two files that
answer the question sit under the paths it was given.

Two pieces, both cheap:

- :func:`named_targets` pulls explicit targets out of the user's text: paths,
  file names, backticked or @-mentioned identifiers, and the word that follows
  a scope noun ("branche main", "service crm", "dossier k8s"), in French or
  English.
- :func:`scope_anchor_context_provider` turns them into one runtime line for
  the turn, and the runner uses the same list to notice a turn whose first
  tool batches touch none of the named targets and say so once.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

from navin.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines

if TYPE_CHECKING:
    from navin.agent.tools.context import RequestContext

MAX_TARGETS = 12

_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_BACKTICK_RE = re.compile(r"`([^`\n]{2,120})`")
_MENTION_RE = re.compile(r"(?<![\w@.])@([\w~][\w./-]{1,120})")
# At least one slash: "forgejo/lynara", "k8s/celery.yaml", "./ms/ocr", "src/".
_PATHLIKE_RE = re.compile(
    r"(?<![\w@/.\-])((?:\.{1,2}/|~/|/)?[\w.\-]+(?:(?:/[\w.\-]+)+/?|/))(?![\w/])"
)
_FILELIKE_RE = re.compile(
    r"(?<![\w/.\-])([\w\-]+\.(?:"
    r"py|pyi|js|jsx|ts|tsx|mjs|cjs|json|ya?ml|toml|ini|cfg|conf|env|md|rst|txt|"
    r"sql|sh|bash|zsh|ps1|go|rs|java|kt|rb|php|cs|c|h|cpp|hpp|swift|dart|vue|"
    r"svelte|html|css|scss|xml|csv|lock|tf|hcl|proto|graphql|prisma|ipynb|"
    r"dockerfile"
    r"))(?![\w.])",
    re.IGNORECASE,
)
_SPECIAL_FILE_RE = re.compile(
    r"(?<![\w/.\-])(dockerfile|docker-compose(?:\.[\w.\-]+)?\.ya?ml|makefile|"
    r"docker-compose)(?![\w.])",
    re.IGNORECASE,
)
# "branche main", "la branche (prod ...)", "service crm", "dossier k8s",
# "namespace lynara-prod", "image ocr". The noun list is the vocabulary a
# user reaches for when pointing at a place; the captured word is the place.
_SCOPE_NOUN = (
    r"branche?s?|branch(?:es)?|dossiers?|r[ée]pertoires?|folders?|"
    r"director(?:y|ies)|chemins?|paths?|repos?|d[ée]p[ôo]ts?|projets?|"
    r"projects?|modules?|services?|namespaces?|fichiers?|files?|composants?|"
    r"components?|images?|microservices?|ms|pods?|deployments?|charts?|"
    r"conteneurs?|containers?|packages?|tables?|sch[ée]mas?|workers?|"
    r"queues?|apps?|applications?|fonctions?|functions?|classes?|tags?|"
    r"versions?|commits?|clusters?|environnements?|environments?|envs?"
)
# Up to two of these between the noun and the place: "branch of the payments".
_ARTICLE = (
    r"(?:(?:de|du|des|la|le|les|un|une|the|a|an|of|in)\s+|d'|l'|:\s*|\(\s*|«\s*|\"|')"
    r"{0,2}"
)
# "l'image de ms crm": the article may be followed by a second scope noun
# before the place itself.
_KEYWORD_RE = re.compile(
    rf"\b(?:{_SCOPE_NOUN})\s+{_ARTICLE}(?:(?:{_SCOPE_NOUN})\s+)?"
    rf"([A-Za-z0-9_][\w./\-]{{1,60}})",
    re.IGNORECASE,
)
_NOUN_WORD_RE = re.compile(rf"^(?:{_SCOPE_NOUN})$", re.IGNORECASE)
# Deployment vocabulary that never appears as an ordinary word in a sentence.
_ENV_WORD_RE = re.compile(
    r"(?<![\w\-])(preprod|pre-prod|staging|master|develop|hotfix)(?![\w\-])",
    re.IGNORECASE,
)
_STOPWORDS = frozenset(
    """
    a à au aux avec ce ces cette ceci cela ça c d dans de des du elle elles en
    est et être il ils je la le les leur leurs lui ma mais me même mes moi mon
    ne ni nos notre nous on ou où par pas pour qu que qui sa se ses si son sur
    ta te tes toi ton tu un une vos votre vous y sont était étaient été avoir
    fait faire faut peux peut veux veut voir tout tous toute toutes plus moins
    très bien mal comme quand donc car alors aussi encore déjà ici là sous vers
    chez entre avant après depuis pendant selon via contre parmi sans
    the a an and or but if then else of in on at to for from by with without
    into onto over under about as is are was were be been being have has had
    do does did not no yes all any each every other same such that this these
    those it its they them their there here where when which who whom whose
    what why how can could should would will shall may might must between
    before after since during through against toward towards across along
    around within beside behind below above off out up down et/ou and/or
    uses has does contains includes crashes fails works runs needs takes
    returns throws breaks calls loads reads writes gets sets makes goes shows
    seems looks starts stops handles sends receives expects requires depends
    exists becomes keeps holds stores saves opens closes builds deploys serves
    listens connects checks tests passes lives sits also still already now
    contient plante gère gere utilise tourne marche fonctionne existe doit va
    envoie reçoit recoit retourne appelle charge lit écrit ecrit casse échoue
    echoue démarre demarre expose sert écoute ecoute dépend depend semble
    devrait reste passe prend donne rend lance
    """.split()
)
_TRAILING_PUNCT = ".,;:!?)]}»\"'"


def _clean(token: str) -> str:
    token = token.strip().rstrip(_TRAILING_PUNCT)
    if token.startswith(("(", "[", "{", "«", '"', "'")):
        token = token[1:]
    return token.strip()


def _acceptable(token: str) -> bool:
    if len(token) < 2 or len(token) > 120:
        return False
    if not re.search(r"[A-Za-z]", token):
        return False
    # Paths, branches, services and files are ASCII; an accented word after a
    # scope noun ("images buildées") is prose, not a place.
    if not token.isascii():
        return False
    lowered = token.lower()
    if lowered in _STOPWORDS or _NOUN_WORD_RE.match(lowered):
        return False
    # A bare version, a duration or a code like "v2" carries no location.
    return not re.fullmatch(r"v?\d+(?:\.\d+)*[a-z]?", lowered)


def named_targets(text: str | None) -> list[str]:
    """Explicit places the user pointed at, in order of appearance, deduped."""
    if not text or not text.strip():
        return []
    body = _URL_RE.sub(lambda m: " " * len(m.group(0)), text)
    hits: list[tuple[int, str]] = []

    def scan(regex: re.Pattern[str], *, single_word: bool = False) -> None:
        for match in regex.finditer(body):
            raw = match.group(1)
            # A backticked command is not a place; keep single identifiers.
            if single_word and " " in raw.strip():
                continue
            hits.append((match.start(1), raw))

    scan(_BACKTICK_RE, single_word=True)
    scan(_MENTION_RE)
    scan(_PATHLIKE_RE)
    scan(_FILELIKE_RE)
    scan(_SPECIAL_FILE_RE)
    scan(_KEYWORD_RE)
    scan(_ENV_WORD_RE)

    found: list[str] = []
    seen: set[str] = set()
    for _pos, raw in sorted(hits, key=lambda item: item[0]):
        token = _clean(raw)
        if not _acceptable(token):
            continue
        key = token.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        found.append(token)
        if len(found) >= MAX_TARGETS:
            break
    return found


def scope_anchor_lines(targets: Sequence[str]) -> list[str]:
    if not targets:
        return []
    joined = ", ".join(targets)
    return [
        f"Targets named in the request: {joined}.",
        "Start there and stay there: open, diff or grep those exact paths, "
        "branches, services or files first. Do not list or read the whole "
        "project to get oriented; widen only once a named target has been "
        "checked and points somewhere else.",
    ]


async def scope_anchor_context_provider(
    request: RequestContext,
) -> RuntimeContextBlock | None:
    """One runtime line per turn naming where the user pointed."""
    targets = named_targets(request.original_user_text)
    content = wrap_runtime_context_lines(scope_anchor_lines(targets))
    if not content:
        return None
    return RuntimeContextBlock(source="scope_anchor", content=content)


# Tools whose only purpose is to look around. A batch made of these that
# touches none of the named targets is the drift this guard is about.
ORIENTATION_TOOLS = frozenset({
    "find_files", "list_dir", "grep", "read_file", "code_index", "metagraph",
    "git", "lsp",
})


def _strings_in(value: Any, out: list[str]) -> None:
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _strings_in(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _strings_in(item, out)


def calls_touch_targets(calls: Iterable[Any], targets: Sequence[str]) -> bool:
    """True when any argument of any call mentions one of ``targets``.

    Matching is on lowercase substrings, so ``forgejo/lynara`` is touched by a
    ``git`` call on ``forgejo/lynara/deploy`` and ``crm`` by a grep for
    ``celery_crm``. Generous on purpose: this only decides whether to say
    something, never whether to block.
    """
    wanted = [t.lower().rstrip("/") for t in targets if t]
    if not wanted:
        return True
    for call in calls:
        args = getattr(call, "arguments", None)
        strings: list[str] = []
        _strings_in(args, strings)
        haystack = "\n".join(strings).lower()
        if any(target in haystack for target in wanted):
            return True
    return False


def is_orientation_batch(calls: Iterable[Any]) -> bool:
    names = [str(getattr(call, "name", "") or "") for call in calls]
    return bool(names) and all(name in ORIENTATION_TOOLS for name in names)


def scope_drift_message(targets: Sequence[str]) -> dict[str, str]:
    joined = ", ".join(targets)
    return {
        "role": "user",
        "content": (
            f"Scope reminder: the request names these targets: {joined}. "
            "Nothing you have opened so far is one of them. Go to them now "
            "(open, diff or grep those exact paths, branches, services or files) "
            "before looking anywhere else, and answer from what they show."
        ),
    }


__all__ = [
    "MAX_TARGETS",
    "ORIENTATION_TOOLS",
    "calls_touch_targets",
    "is_orientation_batch",
    "named_targets",
    "scope_anchor_context_provider",
    "scope_anchor_lines",
    "scope_drift_message",
]
