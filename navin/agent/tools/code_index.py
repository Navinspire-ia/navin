# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Code index tool: semantic navigation over the project.

Gives the agent the primitives an IDE relies on - go-to-definition, find
references, symbol search, file outline, dependency graph - instead of forcing
it to guess with repeated greps. Backed by :mod:`navin.index`, which parses
Python with the stdlib AST and other languages with a declarative pattern
table, and caches results incrementally outside the project tree.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from navin.agent.tools.base import ToolResult
from navin.agent.tools.filesystem import _FsTool


class SemanticSearchConfig(BaseModel):
    """Embedding search settings for the ``code_index`` tool.

    ``enabled`` is three-valued. ``None``, the default, means "on if it is free
    and something is listening": a local runner needs no key and bills nothing,
    so there is no reason to make the user find this setting before they can ask
    where authentication is handled. A hosted provider costs money per repository
    and stays off until someone says ``true``.

    Explicitly ``true`` also changes how failure reads. Auto-mode degrades
    quietly to the lexical tools, because a feature the user never asked for must
    not strand a turn; an explicit ``true`` reports the error, because there a
    silent downgrade would hide a misconfiguration the user wants to know about.
    """

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool | None = None
    provider: str = "ollama"  # Any entry in providers config; its key/base are reused
    model: str = "nomic-embed-text"
    dimensions: int = Field(
        default=0,
        ge=0,
        le=4096,
    )  # 0 = the model's native width; smaller trades a little recall for a smaller cache
    max_chunks: int = Field(
        default=20_000,
        ge=100,
        le=200_000,
        validation_alias=AliasChoices("maxChunks", "max_chunks"),
        serialization_alias="maxChunks",
    )  # Ceiling on embedded symbols, so a monorepo cannot run up an unbounded bill


# Runners that serve embeddings from the user's own machine: no key, no bill, so
# auto-mode may use them without asking. Everything else waits for an explicit
# opt-in, because embedding a monorepo against a hosted endpoint is a real
# invoice and nobody should discover it after the fact.
_FREE_EMBEDDING_PROVIDERS = frozenset({"ollama", "lm_studio", "vllm", "ovms", "atomic_chat"})

# Endpoints that were not listening, and until when we take their word for it.
# Without this, every semantic call in a session where Ollama is not running
# pays another refused connection. The expiry is what lets a user start the
# server mid-session and be believed.
_UNREACHABLE: dict[str, float] = {}
_UNREACHABLE_TTL_S = 300.0


def _is_free_provider(name: str) -> bool:
    return (name or "").strip().lower().replace("-", "_") in _FREE_EMBEDDING_PROVIDERS


def _recently_unreachable(key: str) -> bool:
    expiry = _UNREACHABLE.get(key)
    if expiry is None:
        return False
    if time.monotonic() >= expiry:
        _UNREACHABLE.pop(key, None)
        return False
    return True


def _remember_unreachable(key: str) -> None:
    _UNREACHABLE[key] = time.monotonic() + _UNREACHABLE_TTL_S


# One in-flight embedding sync per project root. Embedding a changed repo can
# take seconds to minutes; it must run outside the agent turn, and two turns
# racing the same root must not embed the same files twice.
_SEMANTIC_SYNCS: dict[str, "asyncio.Task[Any]"] = {}


def _schedule_semantic_sync(
    root: Path,
    index: Any,
    client: Any,
    max_chunks: int,
    *,
    remember_unreachable_key: str | None,
) -> bool:
    """Run ``SemanticIndex.sync`` in the background, deduplicated per root.

    Returns whether a sync is now running (newly scheduled or already in
    flight). Falls back to False when no event loop is available, in which
    case the caller should sync inline as before.
    """
    from navin.index.embeddings import EmbeddingError
    from navin.index.semantic import SemanticIndex

    key = str(root)
    existing = _SEMANTIC_SYNCS.get(key)
    if existing is not None and not existing.done():
        return True

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False

    async def _work() -> None:
        from loguru import logger

        try:
            semantic = SemanticIndex(root, client, max_chunks=max_chunks)
            stats = await semantic.sync(index)
            logger.debug(
                "semantic index synced for {}: {} embedded, {} total",
                root,
                stats.get("embedded", 0),
                stats.get("total", 0),
            )
        except EmbeddingError as exc:
            if remember_unreachable_key:
                _remember_unreachable(remember_unreachable_key)
            logger.debug("background semantic sync failed for {}: {}", root, exc)
        except Exception:
            logger.exception("background semantic sync crashed for {}", root)
        finally:
            _SEMANTIC_SYNCS.pop(key, None)

    _SEMANTIC_SYNCS[key] = loop.create_task(_work())
    return True


_FALL_BACK_TO_LEXICAL = (
    "Semantic search is not available right now, so this answer comes from the "
    "lexical tools instead: use action=search for symbol names, or the grep tool "
    "for exact text."
)

_SEARCH_LIMIT = 40
_REFERENCE_LIMIT = 40
_OUTLINE_LIMIT = 200
_GRAPH_LIST_LIMIT = 40

_SYMBOL_KINDS = [
    "function", "method", "class", "interface", "type", "enum", "struct",
    "trait", "protocol", "constant", "variable", "component", "module",
    "object", "record", "macro", "namespace", "extension", "table", "view",
    "index",
]


def _truncate(items: list[str], limit: int, label: str) -> list[str]:
    if len(items) <= limit:
        return items
    return [*items[:limit], f"  … {len(items) - limit} more {label} (narrow the query)"]


class CodeIndexTool(_FsTool):
    """Semantic code navigation: definitions, references, symbols, graph."""

    _scopes = {"core", "subagent"}

    @classmethod
    def create(cls, ctx: Any) -> Any:
        # The filesystem base owns construction (workspace, allowed dirs, file
        # states), so the semantic settings are attached afterwards rather than
        # threaded through an __init__ shared by every fs tool.
        tool = super().create(ctx)
        tool.semantic = getattr(ctx.config, "semantic_search", None)
        return tool

    @property
    def name(self) -> str:
        return "code_index"

    @property
    def description(self) -> str:
        return (
            "Navigate the codebase semantically instead of grepping: "
            "definition, references, callers/callees (one hop), impact "
            "(transitive callers + tests at risk - run before changing a "
            "shared signature), members/container, search (fuzzy symbol "
            "name), semantic (find code by what it does when you do not know "
            "the identifier), outline/file (one file's symbols and imports), "
            "overview, refresh. Prefer this over grep for 'where is X "
            "defined', 'what calls X', 'what breaks if I change X': answers "
            "come from parsed code, not string matches."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "overview", "definition", "references", "callers",
                        "callees", "impact", "members", "container", "search",
                        "text", "semantic", "outline", "file", "refresh",
                    ],
                    "description": (
                        "overview: project stats and hubs; "
                        "definition: where a symbol is defined; "
                        "references: every recorded use of a symbol; "
                        "callers: definitions that call it (one hop); "
                        "callees: project definitions it calls (one hop); "
                        "impact: transitive callers plus tests at risk; "
                        "members: methods contained in a class; "
                        "container: the class enclosing a method; "
                        "search: fuzzy symbol search; "
                        "text: ranked full-text search (BM25) across code and "
                        "docs - best terms first, snippets included; "
                        "semantic: meaning-based search for when the identifier "
                        "is unknown; "
                        "outline: all symbols of one file; "
                        "file: symbols + imports + importers of one file; "
                        "refresh: force a full reindex (rarely needed; every "
                        "call already picks up changed files)"
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Symbol name for definition/references/callers/callees/"
                        "impact/members/container. Accepts 'Class.method' to "
                        "disambiguate."
                    ),
                },
                "calls_only": {
                    "type": "boolean",
                    "description": (
                        "For action=references: keep only calls, inheritance "
                        "and decorators, dropping plain name mentions."
                    ),
                },
                "depth": {
                    "type": "integer",
                    "description": "Caller levels to walk (action=impact)",
                    "minimum": 1,
                    "maximum": 4,
                },
                "query": {
                    "type": "string",
                    "description": (
                        "For action=search, a fuzzy symbol name fragment. For "
                        "action=semantic, a description of the behaviour you are "
                        "looking for, in words - a full question works better "
                        "than keywords."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Project-relative file path (for action=outline/file). "
                        "A bare filename is resolved when unambiguous."
                    ),
                },
                "kind": {
                    "type": "string",
                    "enum": _SYMBOL_KINDS,
                    "description": "Optional symbol-kind filter",
                },
                "exported_only": {
                    "type": "boolean",
                    "description": "Only public/exported symbols (for action=search)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return",
                    "minimum": 1,
                    "maximum": 200,
                },
            },
            "required": ["action"],
        }

    def _project_root(self) -> Path:
        root = self._display_workspace() or self._workspace
        if root is None:
            raise ValueError("no workspace configured")
        return Path(root).expanduser().resolve(strict=False)

    async def execute(
        self,
        action: str,
        name: str | None = None,
        query: str | None = None,
        path: str | None = None,
        kind: str | None = None,
        exported_only: bool = False,
        limit: int | None = None,
        calls_only: bool = False,
        depth: int | None = None,
        **kwargs: Any,
    ) -> str:
        from navin.index import get_index

        try:
            root = self._project_root()
            if not root.is_dir():
                return ToolResult.error(f"Error: project root not found: {root}")
            index = get_index(root)
            if action == "refresh":
                stats = index.refresh(force=True)
                return f"Index rebuilt: {stats.summary()}"
            index.ensure()
        except Exception as exc:
            return ToolResult.error(f"Error building code index: {exc}")

        if action == "overview":
            result = self._overview(index)
        elif action == "definition":
            result = self._definition(index, name, kind)
        elif action == "references":
            result = self._references(index, name, limit, calls_only)
        elif action == "callers":
            result = self._callers(index, name, limit)
        elif action == "callees":
            result = self._callees(index, name, limit)
        elif action == "impact":
            result = self._impact(index, name, depth)
        elif action == "members":
            result = self._members(index, name)
        elif action == "container":
            result = self._container(index, name)
        elif action == "search":
            result = self._search(index, query, kind, exported_only, limit)
        elif action == "text":
            result = self._text_search(index, query, limit)
        elif action == "semantic":
            result = await self._semantic(index, query, limit)
        elif action == "outline":
            result = self._outline(index, path, limit)
        elif action == "file":
            result = self._file(index, path)
        else:
            return self.unknown_action(action)
        return self._with_coverage_note(index, result)

    @staticmethod
    def _with_coverage_note(index: Any, result: Any) -> Any:
        """Append a truncation warning so partial coverage is never silent.

        On a monorepo past the file cap the index quietly holds a subset; an
        empty answer then reads as "does not exist" when it only means "not in
        the indexed subset". Every lookup carries the caveat, not just refresh.
        """
        if not isinstance(result, str) or result.startswith("Error"):
            return result
        stats = getattr(index, "stats", None)
        if stats is None or not getattr(stats, "truncated", False):
            return result
        return (
            result
            + f"\n\nWarning: the code index holds only the first {stats.total} "
            "files of this project (file cap reached). A missing result here "
            "does not prove absence - confirm with grep or find_files."
        )

    # -- full-text (tantivy) -------------------------------------------------

    def _text_search(self, index: Any, query: str | None, limit: int | None) -> str:
        from navin.index.fulltext import FulltextIndex, fulltext_file_set

        text = (query or "").strip()
        if not text:
            return ToolResult.error("Error: action=text requires 'query'")
        fulltext = FulltextIndex(Path(index.root))
        if not fulltext.available():
            return (
                "Full-text search needs the native extension, which is not "
                "installed here. Use the grep tool for exact text, or "
                "action=search for symbol names."
            )
        indexed = fulltext.refresh(fulltext_file_set(index))
        hits = fulltext.search(text, limit=limit or 20)
        if hits is None:
            return ToolResult.error(
                "Error: full-text search failed; fall back to the grep tool."
            )
        if not hits:
            return f"No full-text matches for {text!r}."
        lines = [f"Full-text matches for {text!r} (best first):"]
        for hit in hits:
            snippet = " ".join(str(hit.get("snippet", "")).split())
            if len(snippet) > 160:
                snippet = snippet[:160] + "…"
            score = float(hit.get("score", 0.0))
            lines.append(f"  {hit.get('path')} (score {score:.2f})")
            if snippet:
                lines.append(f"    {snippet}")
        if indexed:
            lines.append(f"({indexed} file(s) (re)indexed this call)")
        return "\n".join(lines)

    # -- semantic ----------------------------------------------------------

    def _semantic_config(self) -> SemanticSearchConfig:
        """Settings attached by :meth:`create`, defaulting to off."""
        config = getattr(self, "semantic", None)
        return config if config is not None else SemanticSearchConfig()

    async def _semantic(self, index: Any, query: str | None, limit: int | None) -> str:
        from navin.index.embeddings import EmbeddingError, build_client
        from navin.index.semantic import SemanticIndex, fuse_rankings

        text = (query or "").strip()
        if not text:
            return ToolResult.error("Error: action=semantic requires 'query'")

        config = self._semantic_config()
        explicit = config.enabled is True
        if config.enabled is False:
            # Not an error: the lexical tools still answer the question, and a
            # hard failure here would strand a turn over an optional feature.
            return (
                "Semantic search is switched off in this project "
                "(tools.semanticSearch.enabled=false). Use action=search for "
                "names, or the grep tool for exact text."
            )
        if not explicit and not _is_free_provider(config.provider):
            return (
                f"Semantic search would bill against the {config.provider!r} "
                "provider, so it stays off until tools.semanticSearch.enabled=true "
                "says otherwise. For a free local index, point provider at 'ollama' "
                "with 'nomic-embed-text'. Meanwhile use action=search for names, or "
                "the grep tool for exact text."
            )

        from navin.config.loader import load_config

        try:
            providers = load_config().providers
            client = build_client(config, providers)
        except EmbeddingError as exc:
            if not explicit:
                return f"{_FALL_BACK_TO_LEXICAL} ({exc})"
            return ToolResult.error(f"Error: {exc}")
        except Exception as exc:  # noqa: BLE001 - config surface is broad
            return ToolResult.error(f"Error loading semantic search config: {exc}")

        endpoint = client.base_url or client.model
        if not explicit and _recently_unreachable(endpoint):
            return _FALL_BACK_TO_LEXICAL

        semantic = SemanticIndex(
            Path(index.root), client, max_chunks=config.max_chunks
        )
        # Embedding a changed repo can take seconds to minutes and used to
        # block this turn. Search the vectors we already have and let the
        # sync catch up in the background; only a first-ever index with
        # nothing searchable yet degrades to the lexical tools.
        pending = semantic.pending_files(index)
        background = False
        if pending:
            background = _schedule_semantic_sync(
                Path(index.root),
                index,
                client,
                config.max_chunks,
                remember_unreachable_key=None if explicit else endpoint,
            )
            if background and not semantic.has_vectors():
                return (
                    f"Semantic index is building in the background "
                    f"({pending} file(s) to embed). Meanwhile use action=search "
                    "for symbol names or the grep tool for exact text, and retry "
                    "action=semantic shortly."
                )
        try:
            if pending and not background:
                await semantic.sync(index)
            hits = await semantic.search(text, (limit or 12) * 2)
        except EmbeddingError as exc:
            if explicit:
                return ToolResult.error(
                    f"Error: semantic search unavailable ({exc}). Fall back to "
                    "action=search or the grep tool."
                )
            _remember_unreachable(endpoint)
            return f"{_FALL_BACK_TO_LEXICAL} ({exc})"

        if not hits:
            if pending and background:
                return (
                    "No semantically similar code found in the current vectors "
                    f"(an embedding update for {pending} file(s) is running in "
                    "the background; retry shortly)."
                )
            return "No semantically similar code found."

        # Fuse with the lexical ranking so a query that happens to name the
        # symbol still puts the exact match first.
        lexical_keys = [
            f"{loc.path}:{loc.line}"
            for loc in index.search(text, limit=(limit or 12) * 2)
        ]
        by_key = {f"{hit.path}:{hit.start_line}": hit for hit in hits}
        order = fuse_rankings(list(by_key), lexical_keys, limit or 12)

        lines = [f"Semantic matches for {text!r}:"]
        for key in order:
            hit = by_key.get(key)
            if hit is None:
                continue
            lines.append(
                f"  {hit.path}:{hit.start_line}-{hit.end_line} "
                f"[{hit.kind}] {hit.name} (score {hit.score:.2f})"
            )
        if pending and background:
            lines.append(
                f"(embedding update for {pending} file(s) running in the "
                "background; results may lag the newest edits)"
            )
        return "\n".join(lines)

    # -- actions -----------------------------------------------------------

    def _overview(self, index: Any) -> str:
        data = index.overview()
        lines = [
            f"Project: {data['root']}",
            f"Indexed: {data['files']} files, {data['symbols']} symbols, "
            f"{data['edges']} dependency edges",
            "Languages: " + ", ".join(
                f"{lang}={count}" for lang, count in data["languages"].items()
            ),
        ]
        if data["parse_errors"]:
            lines.append(
                f"Parse errors: {data['parse_errors']} file(s) "
                "(symbols degraded to line patterns there)"
            )
        hubs = data["hubs"]
        if hubs:
            lines.append("")
            lines.append("Most connected files (change these carefully):")
            for hub_path, in_deg, out_deg in hubs:
                lines.append(f"  {hub_path} (imported by {in_deg}, imports {out_deg})")
        if data["stats"]:
            lines.append("")
            lines.append(f"Last refresh: {data['stats']}")
        return "\n".join(lines)

    def _definition(self, index: Any, name: str | None, kind: str | None) -> str:
        needle = (name or "").strip()
        if not needle:
            return ToolResult.error("Error: action=definition requires 'name'")
        locations = index.definition(needle, kind=kind)
        if not locations:
            suggestions = index.search(needle, limit=8)
            if suggestions:
                listing = "\n".join(f"  {loc.render()}" for loc in suggestions)
                return f"No exact definition of '{needle}'. Similar symbols:\n{listing}"
            return f"No definition found for '{needle}'."
        header = (
            f"Definition of '{needle}':"
            if len(locations) == 1
            else f"{len(locations)} definitions of '{needle}':"
        )
        return "\n".join([header, *(f"  {loc.render()}" for loc in locations)])

    def _references(
        self, index: Any, name: str | None, limit: int | None, calls_only: bool
    ) -> str:
        needle = (name or "").strip()
        if not needle:
            return ToolResult.error("Error: action=references requires 'name'")
        capped = min(limit or _REFERENCE_LIMIT, 200)
        locations, total = index.references(needle, limit=capped, calls_only=calls_only)
        if not locations:
            scope = "calls" if calls_only else "uses"
            return (
                f"No {scope} of '{needle}' recorded in the index. It may be "
                "defined but unused, or reached only through dynamic dispatch."
            )
        shown = f"{len(locations)} of {total}" if total > len(locations) else str(total)
        lines = [f"{shown} recorded use(s) of '{needle}', strongest evidence first:"]
        lines.extend(f"  {loc.render()}" for loc in locations)
        lines.append("")
        lines.append(
            "Each line names the definition containing the use. Uses come from "
            "parsed code, so comments and strings are excluded; a callee is "
            "matched to its definition by name, so verify before bulk edits."
        )
        return "\n".join(lines)

    def _callers(self, index: Any, name: str | None, limit: int | None) -> str:
        needle = (name or "").strip()
        if not needle:
            return ToolResult.error("Error: action=callers requires 'name'")
        capped = min(limit or _REFERENCE_LIMIT, 200)
        locations = index.callers(needle, limit=capped)
        if not locations:
            return (
                f"Nothing calls, subclasses or decorates with '{needle}'. "
                "Check the name with action=definition, or it may be an entry "
                "point, dead code, or reached dynamically."
            )
        lines = [f"{len(locations)} definition(s) depending on '{needle}':"]
        lines.extend(f"  {loc.render()}" for loc in locations)
        return "\n".join(lines)

    def _callees(self, index: Any, name: str | None, limit: int | None) -> str:
        needle = (name or "").strip()
        if not needle:
            return ToolResult.error("Error: action=callees requires 'name'")
        capped = min(limit or _REFERENCE_LIMIT, 200)
        locations = index.callees(needle, limit=capped)
        if not locations:
            return (
                f"'{needle}' calls no other project definition (it may only use "
                "the standard library or third-party packages)."
            )
        lines = [f"'{needle}' depends on {len(locations)} project definition(s):"]
        lines.extend(f"  {loc.render()}" for loc in locations)
        return "\n".join(lines)

    def _impact(self, index: Any, name: str | None, depth: int | None) -> str:
        needle = (name or "").strip()
        if not needle:
            return ToolResult.error("Error: action=impact requires 'name'")
        report = index.impact(needle, max_depth=depth or 3)
        levels = report["levels"]
        if not levels:
            return (
                f"Nothing depends on '{needle}' through the call graph. "
                "Changing it should be locally contained."
            )
        total = sum(len(level) for level in levels)
        lines = [
            f"Changing '{needle}' can affect {total} definition(s) "
            f"across {len(report['files'])} file(s):",
        ]
        for depth_index, level in enumerate(levels, start=1):
            if not level:
                continue
            label = "directly" if depth_index == 1 else f"{depth_index} hops away"
            lines.append("")
            lines.append(f"{label} ({len(level)}):")
            lines.extend(
                _truncate(
                    [f"  {loc.path}:{loc.line} {loc.name} [{loc.kind}]" for loc in level],
                    _GRAPH_LIST_LIMIT,
                    "callers",
                )
            )
        lines.append("")
        if report["tests"]:
            lines.append("Tests covering this path (run these to verify):")
            lines.extend(f"  {rel}" for rel in report["tests"])
        else:
            lines.append(
                "No test file appears in the caller closure - consider adding "
                "one before changing this."
            )
        if report["truncated"]:
            lines.append("")
            lines.append("Closure truncated: the real blast radius is larger.")
        return "\n".join(lines)

    def _members(self, index: Any, name: str | None) -> str:
        needle = (name or "").strip()
        if not needle:
            return ToolResult.error("Error: action=members requires 'name'")
        locations = index.members(needle)
        if not locations:
            return f"'{needle}' contains no indexed members (not a class, or empty)."
        lines = [f"{len(locations)} member(s) of '{needle}':"]
        lines.extend(f"  {loc.render()}" for loc in locations)
        return "\n".join(lines)

    def _container(self, index: Any, name: str | None) -> str:
        needle = (name or "").strip()
        if not needle:
            return ToolResult.error("Error: action=container requires 'name'")
        locations = index.container(needle)
        if not locations:
            return f"'{needle}' is not nested inside another definition."
        lines = [f"'{needle}' is contained in:"]
        lines.extend(f"  {loc.render()}" for loc in locations)
        return "\n".join(lines)

    def _search(
        self,
        index: Any,
        query: str | None,
        kind: str | None,
        exported_only: bool,
        limit: int | None,
    ) -> str:
        needle = (query or "").strip()
        if not needle:
            return ToolResult.error("Error: action=search requires 'query'")
        capped = min(limit or _SEARCH_LIMIT, 200)
        locations = index.search(
            needle, kind=kind, limit=capped, exported_only=exported_only
        )
        if not locations:
            return f"No symbols matching '{needle}'."
        lines = [f"{len(locations)} symbol(s) matching '{needle}':"]
        lines.extend(f"  {loc.render()}" for loc in locations)
        return "\n".join(lines)

    def _outline(self, index: Any, path: str | None, limit: int | None) -> str:
        cleaned = (path or "").strip()
        if not cleaned:
            return ToolResult.error("Error: action=outline requires 'path'")
        result = index.outline(cleaned)
        if result is None:
            return self._path_help(index, cleaned)
        resolved, symbols = result
        if not symbols:
            return f"{resolved}: no symbols indexed (unsupported language or empty file)."
        capped = min(limit or _OUTLINE_LIMIT, 500)
        lines = [f"{resolved} - {len(symbols)} symbol(s):"]
        for symbol in symbols[:capped]:
            marker = "" if symbol.exported else " (private)"
            detail = f" - {symbol.signature}" if symbol.signature else ""
            lines.append(
                f"  L{symbol.line}: {symbol.kind} {symbol.display}{marker}{detail}"
            )
        if len(symbols) > capped:
            lines.append(f"  … {len(symbols) - capped} more symbols")
        return "\n".join(lines)

    def _file(self, index: Any, path: str | None) -> str:
        cleaned = (path or "").strip()
        if not cleaned:
            return ToolResult.error("Error: action=file requires 'path'")
        result = index.outline(cleaned)
        if result is None:
            return self._path_help(index, cleaned)
        resolved, symbols = result
        entry = index.entries[resolved]
        dependencies = index.dependencies(resolved)
        dependents = index.dependents(resolved)

        lines = [
            f"{resolved} [{entry.language or 'unknown'}] "
            f"{entry.lines} lines, {len(symbols)} symbols",
        ]
        if entry.doc:
            lines.append(f"purpose: {entry.doc}")
        if entry.parse_error:
            lines.append(f"parse warning: {entry.parse_error}")

        exported = [s for s in symbols if s.exported]
        if exported:
            lines.append("")
            lines.append(f"Public API ({len(exported)}):")
            lines.extend(
                _truncate(
                    [f"  L{s.line}: {s.kind} {s.display}" for s in exported],
                    _GRAPH_LIST_LIMIT,
                    "symbols",
                )
            )

        lines.append("")
        lines.append(f"Imports ({len(dependencies)}):")
        lines.extend(
            _truncate([f"  {dep}" for dep in dependencies], _GRAPH_LIST_LIMIT, "files")
            or ["  (none resolved in project)"]
        )

        lines.append("")
        lines.append(f"Imported by ({len(dependents)}):")
        lines.extend(
            _truncate([f"  {dep}" for dep in dependents], _GRAPH_LIST_LIMIT, "files")
            or ["  (none)"]
        )
        if dependents:
            lines.append("")
            lines.append(
                f"Changing this file's public API can affect {len(dependents)} "
                "importing file(s) above."
            )
        return "\n".join(lines)

    def _path_help(self, index: Any, cleaned: str) -> str:
        candidates = index.candidates_for(cleaned)
        if len(candidates) == 1:
            # One candidate is an answer, not an ambiguity: withholding it
            # forces the caller to grep for the path we already know.
            return ToolResult.error(
                f"Error: '{cleaned}' is not in the index. "
                f"Did you mean '{candidates[0]}'?"
            )
        if candidates:
            listing = "\n".join(f"  {c}" for c in candidates[:10])
            return f"Ambiguous path '{cleaned}' - candidates:\n{listing}"
        return ToolResult.error(
            f"Error: '{cleaned}' is not in the index. It may be ignored by git, "
            "an unsupported language, or the path may be wrong."
        )
