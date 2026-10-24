# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

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

import os
import re
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
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
    rf"\b(?P<noun>{_SCOPE_NOUN})\s+{_ARTICLE}(?:(?P<noun2>{_SCOPE_NOUN})\s+)?"
    rf"(?P<place>[A-Za-z0-9_][\w./\-]{{1,60}})",
    re.IGNORECASE,
)
_NOUN_WORD_RE = re.compile(rf"^(?:{_SCOPE_NOUN})$", re.IGNORECASE)
_FILE_NOUN_RE = re.compile(r"^(?:fichiers?|files?)$", re.IGNORECASE)
_MODULE_NOUN_RE = re.compile(r"^modules?$", re.IGNORECASE)
# Navin's own product areas. "le menu du module Code" talks about the app the
# user is using, not about a folder of the project being worked on.
_NAVIN_MODULES = frozenset({
    "navin", "code", "documents", "docs", "marketing", "seo", "tenders",
    "career", "studio", "montage", "reverse",
})
# Deployment vocabulary that never appears as an ordinary word in a sentence.
# "develop", "master" and "hotfix" do ("the user wants to develop..."): as
# branches they come with a noun ("branche develop") and the keyword scan
# catches them there.
_ENV_WORD_RE = re.compile(
    r"(?<![\w\-])(preprod|pre-prod|staging)(?![\w\-])",
    re.IGNORECASE,
)
# "text/style", "lint/type/build", "Rust/Python", "et/ou": an alternative
# written with a slash, not a path. Such a pair is kept only when the
# workspace has it, or when it looks like a lowercase repo path (forgejo/lynara).
_WORD_PAIR_RE = re.compile(r"^[A-Za-z]+(?:/[A-Za-z]+)+$")
_PROSE_PAIR_WORDS = frozenset(
    """
    text style code test tests lint type types build run read write front back
    frontend backend client server input output dev prod ui ux cli api web
    mobile desktop et ou and or oui non yes no on off true false avant apres
    before after fr en ar
    """.split()
)
# A library written like a file: Three.js, Node.js, Vue.js.
_LIBRARY_RE = re.compile(r"^[A-Z][A-Za-z0-9]*\.js$")
# A word after a scope noun with nothing that marks an identifier: no digit,
# dash, underscore, dot or slash. "service crm" and "dépôt complet" look
# alike; only the workspace can tell them apart.
_PLAIN_WORD_RE = re.compile(r"^[^\W\d_]+$")
_SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    "target", ".next", ".cache", ".mypy_cache", ".pytest_cache", ".ruff_cache",
})
_NAME_SCAN_DEPTH = 4
_NAME_SCAN_LIMIT = 40_000
_NAME_CACHE_TTL_S = 60.0
_name_cache: dict[str, tuple[float, frozenset[str]]] = {}


def _git_branch_names(root: Path) -> set[str]:
    git = root / ".git"
    names: set[str] = set()
    if not git.is_dir():
        return names
    for refs in (git / "refs" / "heads", git / "refs" / "remotes"):
        if refs.is_dir():
            for ref in refs.rglob("*"):
                if ref.is_file():
                    rel = ref.relative_to(refs).as_posix()
                    names.add(rel.lower())
                    names.add(rel.rsplit("/", 1)[-1].lower())
    packed = git / "packed-refs"
    if packed.is_file():
        try:
            for line in packed.read_text(encoding="utf-8", errors="replace").splitlines():
                ref = line.split(" ", 1)[-1].strip()
                if ref.startswith(("refs/heads/", "refs/remotes/")):
                    names.add(ref.rsplit("/", 1)[-1].lower())
        except OSError:
            pass
    return names


def workspace_names(root: Path) -> frozenset[str]:
    """Lowercase file, folder and branch names of a workspace (shallow, cached)."""
    key = str(root)
    now = time.monotonic()
    cached = _name_cache.get(key)
    if cached and now - cached[0] < _NAME_CACHE_TTL_S:
        return cached[1]
    names: set[str] = set()
    stack: list[tuple[Path, int]] = [(root, 1)]
    seen = 0
    while stack and seen < _NAME_SCAN_LIMIT:
        folder, depth = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            seen += 1
            name = entry.name.lower()
            names.add(name)
            names.add(name.split(".", 1)[0])
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                is_dir = False
            if is_dir and depth < _NAME_SCAN_DEPTH and entry.name not in _SKIP_DIRS:
                stack.append((Path(entry.path), depth + 1))
    names |= _git_branch_names(root)
    result = frozenset(n for n in names if n)
    _name_cache[key] = (now, result)
    return result


def _in_workspace(token: str, root: Path | None) -> bool:
    if root is None:
        return False
    cleaned = token.strip().rstrip("/")
    try:
        if (root / cleaned).exists():
            return True
    except (OSError, ValueError):
        return False
    lowered = cleaned.lower()
    names = workspace_names(root)
    return lowered in names or lowered.rsplit("/", 1)[-1] in names


def _prose_token(token: str, noun: str, root: Path | None) -> bool:
    """A word of the sentence the scans picked up as if it were a place."""
    if _WORD_PAIR_RE.match(token):
        parts = token.split("/")
        # Only the path itself, or its first folder at the project root,
        # proves a pair: single words like "text" or "code" are names of
        # something in almost any repository.
        if root is not None:
            try:
                if (root / token).exists() or (root / parts[0]).is_dir():
                    return False
            except (OSError, ValueError):
                pass
        return any(
            not part.islower() or part.lower() in _PROSE_PAIR_WORDS for part in parts
        )
    if _LIBRARY_RE.match(token):
        return not _in_workspace(token, root)
    if noun and _PLAIN_WORD_RE.match(token):
        if _in_workspace(token, root):
            return False
        # "version FR", "applications Electron": acronyms and proper nouns
        # describe the thing; a folder or a service the user points at is
        # written in lowercase. With a workspace, an unknown word is prose.
        return root is not None or not token.islower()
    return False
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
    complet complets complete completes entier entiers entiere entieres
    principal principale principaux actuel actuelle actuels courant courante
    existant existante existants existantes nouveau nouvelle nouveaux ancien
    ancienne anciens global globale entire whole full current existing new old
    same other various several
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


def _place_after_noun(noun: str, token: str) -> bool:
    """Is the word after a scope noun a place of the project, not prose?"""
    lowered = token.lower()
    # "fichiers d'interface", "files of code": a kind of file. A file the user
    # points at has an extension or a path, and the file patterns catch it.
    if _FILE_NOUN_RE.match(noun) and not re.search(r"[./]", token):
        return False
    # "les modules de Navin": the product name. A lowercase navin is the folder.
    if lowered == "navin" and not token.islower():
        return False
    return not (_MODULE_NOUN_RE.match(noun) and lowered in _NAVIN_MODULES)


def named_targets(text: str | None, workspace: Path | str | None = None) -> list[str]:
    """Explicit places the user pointed at, in order of appearance, deduped.

    With ``workspace``, a bare word after a scope noun ("le service crm") is
    kept only when the project has a file, folder or branch of that name, so
    prose ("le dépôt complet") never becomes a target the agent cannot reach.
    """
    if not text or not text.strip():
        return []
    root = Path(workspace) if workspace else None
    if root is not None and not root.is_dir():
        root = None
    body = _URL_RE.sub(lambda m: " " * len(m.group(0)), text)
    hits: list[tuple[int, str, str]] = []

    def scan(regex: re.Pattern[str], *, single_word: bool = False) -> None:
        keyword = "place" in regex.groupindex
        for match in regex.finditer(body):
            raw = match.group("place") if keyword else match.group(1)
            # A backticked command is not a place; keep single identifiers.
            if single_word and " " in raw.strip():
                continue
            noun = (match.group("noun2") or match.group("noun")) if keyword else ""
            hits.append((match.start("place" if keyword else 1), raw, noun))

    scan(_BACKTICK_RE, single_word=True)
    scan(_MENTION_RE)
    scan(_PATHLIKE_RE)
    scan(_FILELIKE_RE)
    scan(_SPECIAL_FILE_RE)
    scan(_KEYWORD_RE)
    scan(_ENV_WORD_RE)

    found: list[str] = []
    seen: set[str] = set()
    for _pos, raw, noun in sorted(hits, key=lambda item: item[0]):
        token = _clean(raw)
        if not _acceptable(token):
            continue
        if noun and not _place_after_noun(noun, token):
            continue
        if _prose_token(token, noun, root):
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
    targets = named_targets(request.original_user_text, getattr(request, "workspace", None))
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


# The orientation tools that search rather than read. A turn made of reads is
# an audit or a review working through its files; one that keeps searching is
# lost, which is what the exploration check is for.
SEARCH_TOOLS = frozenset({
    "find_files", "list_dir", "grep", "code_index", "metagraph", "git", "lsp",
})


# Shell commands that only look: a turn that greps, seds and awks through the
# shell is exploring exactly like one that calls grep/read_file, but used to
# reset the exploration check on every step (a /forge session read for a whole
# turn through `exec grep | sed -n` and never edited a file).
_READONLY_SHELL_COMMANDS = frozenset({
    "grep", "egrep", "fgrep", "rg", "ag", "ack", "ls", "ll", "cat", "bat",
    "head", "tail", "less", "more", "awk", "gawk", "find", "fd", "wc",
    "tree", "file", "stat", "pwd", "cd", "echo", "printf", "sort", "uniq",
    "cut", "nl", "tr", "column", "basename", "dirname", "realpath",
    "which", "type", "sed", "git", "jq", "du", "diff", "true",
    "dir", "findstr", "select-string", "get-content", "gc", "get-childitem",
    "gci", "sls",
})
_READONLY_GIT_SUBCOMMANDS = frozenset({
    "status", "log", "diff", "show", "grep", "blame", "ls-files", "rev-parse",
    "branch", "remote", "describe", "shortlog",
})
_SHELL_SPLIT = re.compile(r"&&|\|\||[;|\n]")
_QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
_HARMLESS_REDIRECTS = re.compile(r"\d?>\s*/dev/null|\d?>&\d|\$null")
_FIND_WRITES = re.compile(r"(?:^|\s)-(?:delete|exec|execdir|ok|okdir|fprint\w*)\b")
_BOOKKEEPING_TOOLS = frozenset({
    "board", "skill", "tool_search", "set_composer_mode", "my", "create_goal",
    "update_goal", "web_search", "web_fetch", "recall", "notes", "memory",
})


def _shell_command_text(call: Any) -> str:
    args = getattr(call, "arguments", None)
    if not isinstance(args, dict):
        return ""
    value = args.get("command") or args.get("cmd") or ""
    return value if isinstance(value, str) else ""


def _segment_is_readonly(segment: str) -> bool:
    words = segment.strip().lstrip("({").split()
    while words and ("=" in words[0] and not words[0].startswith("=")):
        words.pop(0)  # VAR=value prefix
    while words and words[0] in {"time", "command", "nice"}:
        words.pop(0)
    if not words:
        return True
    program = words[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    if program not in _READONLY_SHELL_COMMANDS:
        return False
    rest = words[1:]
    if program == "sed" and any(w.startswith("-i") or w == "--in-place" for w in rest):
        return False
    if program in {"find", "fd"} and _FIND_WRITES.search(segment):
        return False
    if program == "git":
        sub = next((w for w in rest if not w.startswith("-")), "")
        return sub in _READONLY_GIT_SUBCOMMANDS
    return True


def is_readonly_shell_call(call: Any) -> bool:
    """True for an ``exec`` call whose every command only reads or searches."""
    if str(getattr(call, "name", "") or "") != "exec":
        return False
    command = _shell_command_text(call).strip()
    if not command or "$(" in command or "`" in command:
        return False
    # Quoted text is data (an awk program, a grep pattern): its `>` or `&&`
    # is neither a redirect nor a second command.
    bare = _QUOTED.sub("''", command)
    if ">" in _HARMLESS_REDIRECTS.sub("", bare):
        return False
    return all(_segment_is_readonly(part) for part in _SHELL_SPLIT.split(bare))


def _is_look_call(call: Any) -> bool:
    name = str(getattr(call, "name", "") or "")
    return name in ORIENTATION_TOOLS or is_readonly_shell_call(call)


def is_action_call(call: Any) -> bool:
    """A call that changes or runs something, as opposed to looking around.

    Bookkeeping (board, skills, tool loading, goals) is neither: a turn that
    only read files and moved a board card has still not started the work.
    """
    name = str(getattr(call, "name", "") or "")
    if not name or _is_look_call(call) or name in _BOOKKEEPING_TOOLS:
        return False
    return True


def is_search_batch(calls: Iterable[Any]) -> bool:
    """A look-around batch with at least one search call (not reads alone)."""
    calls = list(calls)
    return is_orientation_batch(calls) and any(
        str(getattr(call, "name", "") or "") in SEARCH_TOOLS
        or is_readonly_shell_call(call)
        for call in calls
    )


def is_orientation_batch(calls: Iterable[Any]) -> bool:
    calls = list(calls)
    return bool(calls) and all(_is_look_call(call) for call in calls)


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


def exploration_budget_message(batches: int) -> dict[str, str]:
    return {
        "role": "user",
        "content": (
            f"Exploration check: the last {batches} steps only searched and looked, "
            "nothing was changed or run. Stop searching now. If you found the code "
            "behind the request, act on it. Otherwise answer with what you know, "
            "say what is still missing, or call ask_user with one precise question "
            "(a question left in the final text is never shown as one)."
        ),
    }


__all__ = [
    "MAX_TARGETS",
    "exploration_budget_message",
    "is_action_call",
    "ORIENTATION_TOOLS",
    "calls_touch_targets",
    "is_orientation_batch",
    "is_readonly_shell_call",
    "is_search_batch",
    "named_targets",
    "scope_anchor_context_provider",
    "scope_anchor_lines",
    "scope_drift_message",
]
