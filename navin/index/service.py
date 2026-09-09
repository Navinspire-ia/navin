# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Query facade over the code index.

:class:`CodeIndex` owns the refresh cycle and every lookup the agent and the
Dev workbench need:

- ``definition(name)`` - exact symbol lookup, the go-to-definition primitive.
- ``references(name)`` - recorded uses ranked by evidence strength.
- ``callers(name)`` / ``callees(name)`` - the symbol-level call graph.
- ``members(name)`` / ``container(path, name)`` - containment (class ↔ methods).
- ``impact(name)`` - transitive caller closure, for change-risk analysis.
- ``search(query)`` - fuzzy symbol search across the project.
- ``outline(path)`` - the symbol tree of one file.
- ``edges()`` - files as nodes, resolved imports as edges.

Precision contract: definitions, containment and import edges are derived from
parsed source and are reliable. Call edges are exact for Python (AST) and
pattern-derived elsewhere; in both cases a callee name is matched to a project
definition by name, so an overloaded name across unrelated modules can produce
a false edge. Callers surface confidence rather than implying a type-resolved
result.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from navin.index.store import (
    RefreshStats,
    discover_files,
    indexable,
    load_cache,
    load_refs_cache,
    save_cache,
    save_refs_cache,
)
from navin.index.symbols import (
    CALL_KINDS,
    MAX_PARSE_BYTES,
    FileEntry,
    Reference,
    Symbol,
    extract_from_bytes,
    language_for,
    read_and_extract,
)
from navin.utils.native import native
from navin.utils.path import normalize_relative_path

_JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte")
_JS_RESOLVE_SUFFIXES = (
    "", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte",
    "/index.ts", "/index.tsx", "/index.js", "/index.jsx",
)
_MAX_REFERENCE_HITS = 200
_REF_CONTEXT_CHARS = 200
_MAX_IMPACT_DEPTH = 4

# Definitions strong enough that a same-named match is very likely the real
# target, as opposed to an incidental local variable.
STRONG_KINDS = frozenset({
    "function", "method", "class", "interface", "type", "enum",
    "struct", "trait", "protocol", "component", "module", "record",
})

# A call target has to be callable. Constants and plain variables that happen to
# share a name with an attribute call (``time.time()`` looks like a call to
# "time") would otherwise show up as call edges.
CALLABLE_KINDS = STRONG_KINDS | {"object", "macro", "hook"}


def _builtin_names() -> frozenset[str]:
    """Names that belong to a language runtime, not to this project.

    Without this, ``set()`` or ``Object.keys()`` resolve onto any project symbol
    that happens to share the name, inventing call edges out of nothing.
    """
    import builtins

    globals_js = {
        "Object", "Array", "String", "Number", "Boolean", "Symbol", "Promise",
        "Map", "Set", "WeakMap", "WeakSet", "Date", "Error", "JSON", "Math",
        "RegExp", "console", "window", "document", "fetch", "require",
        "setTimeout", "setInterval", "clearTimeout", "clearInterval",
        "parseInt", "parseFloat", "isNaN", "encodeURIComponent", "structuredClone",
    }
    return frozenset(set(dir(builtins)) | globals_js)


_BUILTINS = _builtin_names()


def _iter_stats(root: Path, files: list[str]):
    """Yield ``(rel, mtime_ns, size)`` for every indexable file that stats.

    The change detector compares these two numbers against the cached
    fingerprint for every file on every pass, so on a large repo this is
    thousands of ``stat()`` calls. When the native extension is present they
    all happen in one parallel Rust pass; the values are identical to what
    ``os.stat`` reports, so cached fingerprints stay valid either way. Files
    that cannot be stat'd are skipped, matching the ``OSError`` fallback.
    """
    rels = [rel for rel in files if indexable(rel)]
    core = native()
    if core is not None:
        pairs: dict[str, tuple[int, int]] | None
        try:
            pairs = core.stat_tree(str(root), rels)
        except Exception:
            pairs = None
        if pairs is not None:
            for rel in rels:
                pair = pairs.get(rel)
                if pair is not None:
                    yield rel, pair[0], pair[1]
            return
    for rel in rels:
        try:
            stat = (root / rel).stat()
        except OSError:
            continue
        yield rel, stat.st_mtime_ns, stat.st_size


# Below this many changed files the parallel read gains nothing over the
# per-file loop; the common incremental pass touches one or two files.
_BATCH_READ_MIN = 8


def _extract_batch(
    root: Path, items: list[tuple[str, int, int]]
) -> dict[str, FileEntry | None]:
    """``read_and_extract`` for many ``(rel, mtime_ns, size)`` at once.

    Reading is the I/O-bound half of an index pass and the part that crawls on
    slow filesystems (a WSL project opened from Windows); with the native
    extension all contents are read in one parallel Rust pass. Parsing stays
    in Python either way and the entries produced are identical.
    """
    core = native()
    if core is None or len(items) < _BATCH_READ_MIN:
        return {rel: read_and_extract(root, rel) for rel, _, _ in items}

    out: dict[str, FileEntry | None] = {}
    readable: list[tuple[str, int, int]] = []
    for rel, mtime_ns, size in items:
        if size > MAX_PARSE_BYTES:
            out[rel] = FileEntry(
                path=rel,
                language=language_for(rel),
                mtime_ns=mtime_ns,
                size=size,
                lines=0,
                parse_error="file too large to index",
            )
        else:
            readable.append((rel, mtime_ns, size))
    try:
        blobs = core.read_files(
            [str(root / rel) for rel, _, _ in readable], max_bytes=MAX_PARSE_BYTES
        )
    except Exception:
        for rel, _, _ in readable:
            out[rel] = read_and_extract(root, rel)
        return out
    for rel, mtime_ns, size in readable:
        data, _err = blobs.get(str(root / rel), (None, "unreadable"))
        if data is None:
            # Unreadable in the batch (vanished, permissions, grown past the
            # limit): the per-file path re-stats and keeps the exact
            # read_and_extract semantics for this edge.
            out[rel] = read_and_extract(root, rel)
        else:
            out[rel] = extract_from_bytes(rel, data, mtime_ns=mtime_ns, size=size)
    return out


# Package entry points re-export their submodules' symbols, so an importer of
# ``navin/index/__init__.py`` really does see what ``service.py`` defines. Import
# resolution follows one extra hop through these, and only these.
_REEXPORT_NAMES = frozenset({
    "__init__.py", "index.ts", "index.tsx", "index.js", "index.jsx",
    "index.mjs", "mod.rs", "lib.rs",
})


def _is_reexport(rel: str) -> bool:
    return rel.rsplit("/", 1)[-1] in _REEXPORT_NAMES


_TEST_MARKERS = ("test_", "_test.", ".test.", ".spec.", "_spec.")
_TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec", "e2e"})


def _looks_like_test(rel: str) -> bool:
    """True when a path is a test file, by directory or filename convention."""
    parts = rel.split("/")
    if any(part in _TEST_DIRS for part in parts[:-1]):
        return True
    name = parts[-1]
    return name.startswith("test_") or any(marker in name for marker in _TEST_MARKERS)


@dataclass(frozen=True, slots=True)
class Location:
    """A symbol occurrence in the project."""

    path: str
    line: int
    kind: str = ""
    name: str = ""
    signature: str = ""
    context: str = ""
    confidence: str = ""

    def render(self) -> str:
        head = f"{self.path}:{self.line}"
        if self.name:
            head += f" {self.name}"
        if self.kind:
            head += f" [{self.kind}]"
        detail = self.signature or self.context
        if detail:
            head += f" - {detail.strip()[:_REF_CONTEXT_CHARS]}"
        if self.confidence:
            head += f" ({self.confidence})"
        return head


class CodeIndex:
    """Incremental, queryable index of one project tree."""

    # Skip the stat-walk when the last one is this recent and nothing marked
    # the tree dirty. On a 5k-file repo a revalidate costs ~200ms, and agent
    # turns call ensure() once per index tool: without a TTL a burst of reads
    # pays that walk every single time for a tree that has not moved.
    _REVALIDATE_TTL_S = 2.0

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=False)
        self.entries: dict[str, FileEntry] = {}
        # Every discovered file, including ones with no indexable language
        # (config, docs, assets). Consumers that need the full tree - the Dev
        # workbench graph, the .metadata role map - read this instead of
        # re-walking the project.
        self.all_files: list[str] = []
        self.stats: RefreshStats | None = None
        self._by_name: dict[str, list[tuple[str, Symbol]]] = {}
        # Casefolded view of ``_by_name`` for ``search()``: one bucket per
        # lowered name so a warm lookup is O(unique names) with an O(1) exact
        # hit, not O(files × symbols) with a .lower() per symbol.
        self._by_name_lower: dict[str, list[tuple[str, Symbol]]] = {}
        self._by_qual: dict[tuple[str, str], Symbol] = {}
        self._edges: set[tuple[str, str]] = set()
        self._top_level: set[str] = set()
        self._dependents: dict[str, set[str]] = {}
        self._dependencies: dict[str, set[str]] = {}
        self._loaded = False
        # Reference state is built on first use, not on refresh: most sessions
        # never ask "who calls this", and they should not pay for the answer.
        self._refs_by_name: dict[str, list[tuple[str, Reference]]] = {}
        self._refs_ready: set[str] = set()
        self._refs_loaded = False
        # The background warmer runs ensure() in a worker thread while agent
        # turns query from the event loop thread; refresh mutates every
        # derived structure, so the whole cycle takes one reentrant lock.
        self._lifecycle_lock = threading.RLock()
        self._revalidated_at = 0.0
        self._dirty = False

    # -- lifecycle ---------------------------------------------------------

    def mark_dirty(self) -> None:
        """Force the next :meth:`ensure` to re-stat the tree (bypass the TTL).

        Called after agent file edits so an immediately following lookup sees
        the new code instead of a throttled snapshot.
        """
        self._dirty = True

    def ensure(self, *, force: bool = False) -> RefreshStats:
        """Refresh the index if needed and return the last refresh stats."""
        with self._lifecycle_lock:
            if self._loaded and not force and self.stats is not None:
                self.revalidate()
                return self.stats
            return self.refresh(force=force)

    def revalidate(self) -> bool:
        """Bring the in-memory index back in line with the files on disk.

        Without this the index answers from the state the project had when the
        process first looked, so an agent that edits a file and then asks who
        calls a symbol is told about the code it just replaced. A full refresh
        would fix that too, but it costs seconds because it reloads the cache and
        rebuilds every derived structure; this walks the file list and stats what
        is already known, which is two orders of magnitude cheaper, and pays the
        rebuild only when something actually moved.

        Returns whether anything changed.
        """
        with self._lifecycle_lock:
            return self._revalidate_locked()

    def _revalidate_locked(self) -> bool:
        now = time.monotonic()
        if not self._dirty and now - self._revalidated_at < self._REVALIDATE_TTL_S:
            return False
        self._dirty = False
        self._revalidated_at = now
        files, discovery, truncated = discover_files(self.root)
        self.all_files = files

        entries = dict(self.entries)
        fresh: set[str] = set()
        parsed = 0
        present: set[str] = set()
        changed: list[tuple[str, int, int]] = []
        for rel, mtime_ns, size in _iter_stats(self.root, files):
            present.add(rel)
            previous = entries.get(rel)
            if (
                previous is not None
                and previous.mtime_ns == mtime_ns
                and previous.size == size
            ):
                continue
            changed.append((rel, mtime_ns, size))
        for rel, entry in _extract_batch(self.root, changed).items():
            if entry is None:
                continue
            entries[rel] = entry
            fresh.add(rel)
            parsed += 1

        gone = [rel for rel in entries if rel not in present]
        for rel in gone:
            del entries[rel]

        if not fresh and not gone:
            return False

        self.entries = entries
        # A re-parsed entry carries new references, and the ones cached for its
        # previous contents are now wrong, so the lazy reference state is dropped
        # rather than patched.
        self._refs_ready = (self._refs_ready - set(gone) - fresh) | fresh
        self._refs_by_name = {}
        self._refs_loaded = False
        self._build_derived()

        previous_stats = self.stats
        self.stats = RefreshStats(
            total=len(entries),
            parsed=parsed,
            reused=len(entries) - parsed,
            removed=len(gone),
            truncated=truncated,
            duration_ms=previous_stats.duration_ms if previous_stats else 0,
            discovery=discovery,
        )
        save_cache(
            self.root,
            entries,
            {
                "discovery": discovery,
                "truncated": truncated,
                "indexed_at": time.time(),
            },
        )
        return True

    def refresh(self, *, force: bool = False) -> RefreshStats:
        with self._lifecycle_lock:
            return self._refresh_locked(force=force)

    def _refresh_locked(self, *, force: bool = False) -> RefreshStats:
        started = time.monotonic()
        self._dirty = False
        self._revalidated_at = started
        files, discovery, truncated = discover_files(self.root)
        cached = {} if force else load_cache(self.root)

        entries: dict[str, FileEntry] = {}
        fresh: set[str] = set()
        parsed = reused = 0
        order: list[str] = []
        stale: list[tuple[str, int, int]] = []
        for rel, mtime_ns, size in _iter_stats(self.root, files):
            order.append(rel)
            previous = cached.get(rel)
            if (
                previous is not None
                and previous.mtime_ns == mtime_ns
                and previous.size == size
            ):
                entries[rel] = previous
                reused += 1
                continue
            stale.append((rel, mtime_ns, size))
        for rel, entry in _extract_batch(self.root, stale).items():
            if entry is None:
                continue
            entries[rel] = entry
            # Freshly parsed entries already carry their references; only
            # cache-reused ones will need the reference cache later.
            fresh.add(rel)
            parsed += 1
        # Batch insertion appends fresh entries after the reused ones; restore
        # the stat-pass order so downstream iteration is deterministic and
        # identical to the sequential path.
        entries = {rel: entries[rel] for rel in order if rel in entries}

        removed = len([rel for rel in cached if rel not in entries])
        self.entries = entries
        self.all_files = files
        self._refs_ready = fresh
        self._refs_by_name = {}
        self._refs_loaded = False
        self._build_derived()
        self._loaded = True

        duration_ms = int((time.monotonic() - started) * 1000)
        self.stats = RefreshStats(
            total=len(entries),
            parsed=parsed,
            reused=reused,
            removed=removed,
            truncated=truncated,
            duration_ms=duration_ms,
            discovery=discovery,
        )
        save_cache(
            self.root,
            entries,
            {
                "discovery": discovery,
                "truncated": truncated,
                "indexed_at": time.time(),
            },
        )
        if truncated:
            # A partial index means searches can quietly miss files. Log only,
            # never a toast: this is internal plumbing the user cannot act on,
            # and a popup about it reads as a product failure. The agent-side
            # code_index tool reports coverage in its own output.
            logger.warning(
                "Code index is incomplete for {}: {} files covered; searches "
                "may miss code outside them",
                self.root,
                len(entries),
            )
        return self.stats

    # -- derived structures ------------------------------------------------

    def _build_derived(self) -> None:
        by_name: dict[str, list[tuple[str, Symbol]]] = defaultdict(list)
        by_name_lower: dict[str, list[tuple[str, Symbol]]] = defaultdict(list)
        by_qual: dict[tuple[str, str], Symbol] = {}
        for rel, entry in self.entries.items():
            for symbol in entry.symbols:
                by_name[symbol.name].append((rel, symbol))
                by_name_lower[symbol.name.lower()].append((rel, symbol))
                by_qual.setdefault((rel, symbol.display), symbol)
        self._by_name = dict(by_name)
        self._by_name_lower = dict(by_name_lower)
        self._by_qual = by_qual

        paths = set(self.entries)
        # Hoisted out of the resolver: it is the same for every import in the
        # pass, and deriving it per call meant splitting every project path once
        # per import, which dominated this whole method.
        self._top_level = {p.split("/", 1)[0] for p in paths if "/" in p}
        edges: set[tuple[str, str]] = set()
        for rel, entry in self.entries.items():
            for target in entry.imports:
                resolved = self._resolve_import(rel, target, paths)
                if resolved and resolved != rel:
                    edges.add((rel, resolved))
        self._edges = edges

        dependents: dict[str, set[str]] = defaultdict(set)
        dependencies: dict[str, set[str]] = defaultdict(set)
        for source, target in edges:
            dependencies[source].add(target)
            dependents[target].add(source)
        self._dependents = dict(dependents)
        self._dependencies = dict(dependencies)

    def _ensure_refs(self) -> None:
        """Populate references for every entry, from cache or by re-parsing.

        Called by reference queries only. Files parsed during this session
        already have their references; the rest come from the reference cache,
        and anything the cache cannot vouch for is re-parsed.
        """
        self.ensure()
        if self._refs_loaded:
            return
        cached = load_refs_cache(self.root)
        stale = False
        for rel, entry in self.entries.items():
            payload = cached.get(rel)
            if rel in self._refs_ready:
                # Parsed this session: the references are correct in memory but
                # the cache may predate them.
                if payload is None or int(payload.get("mtime_ns", -1)) != entry.mtime_ns:
                    stale = True
                continue
            if payload is not None and entry.load_refs_json(payload):
                self._refs_ready.add(rel)
                continue
            reparsed = read_and_extract(self.root, rel)
            if reparsed is not None:
                entry.refs = reparsed.refs
            self._refs_ready.add(rel)
            stale = True

        refs_by_name: dict[str, list[tuple[str, Reference]]] = defaultdict(list)
        for rel, entry in self.entries.items():
            for ref in entry.refs:
                refs_by_name[ref.name].append((rel, ref))
        self._refs_by_name = dict(refs_by_name)
        self._refs_loaded = True
        if stale:
            save_refs_cache(self.root, self.entries)

    def _resolve_import(self, rel: str, target: str, paths: set[str]) -> str | None:
        """Map a raw import target onto a project file, when possible."""
        target = target.strip()
        if not target:
            return None
        suffix = Path(rel).suffix.lower()
        if suffix in _JS_EXTS:
            return self._resolve_js(rel, target, paths)
        if suffix in (".py", ".pyi"):
            return self._resolve_python(rel, target, paths)
        # Generic: treat the target as a path fragment (Go, C/C++ includes…).
        cleaned = normalize_relative_path(target.strip("\"'<>"))
        if cleaned in paths:
            return cleaned
        base = (Path(rel).parent / cleaned).as_posix()
        return base if base in paths else None

    def _resolve_js(self, rel: str, target: str, paths: set[str]) -> str | None:
        if not (target.startswith(".") or target.startswith("@/") or target.startswith("~/")):
            return None  # bare package import
        parts = rel.split("/")[:-1]
        if target[0] in "@~":
            if "src" in parts:
                cut = len(parts) - parts[::-1].index("src")
                alias_root = "/".join(parts[:cut])
            else:
                alias_root = "/".join(parts[:1]) if parts else ""
            tail = target[2:]
            joined = f"{alias_root}/{tail}" if alias_root else tail
        else:
            joined = (Path(rel).parent / target).as_posix()
        norm: list[str] = []
        for part in joined.split("/"):
            if part == "..":
                if norm:
                    norm.pop()
            elif part not in {"", "."}:
                norm.append(part)
        stem = "/".join(norm)
        for extra in _JS_RESOLVE_SUFFIXES:
            candidate = f"{stem}{extra}"
            if candidate in paths:
                return candidate
        return None

    def _resolve_python(self, rel: str, target: str, paths: set[str]) -> str | None:
        leading = len(target) - len(target.lstrip("."))
        module = target.lstrip(".")
        candidates: list[str] = []
        if leading:
            base = Path(rel).parent
            for _ in range(max(leading - 1, 0)):
                base = base.parent
            stem = (base / module.replace(".", "/")).as_posix() if module else base.as_posix()
            candidates += [f"{stem}.py", f"{stem}/__init__.py"]
        else:
            mod_path = module.replace(".", "/")
            candidates += [f"{mod_path}.py", f"{mod_path}/__init__.py"]
            # "from navin.x import y" resolves inside a same-named package dir.
            head, _, tail = module.partition(".")
            if tail and head == self.root.name:
                tail_path = tail.replace(".", "/")
                candidates += [f"{tail_path}.py", f"{tail_path}/__init__.py"]
            # Also try the module hanging off any top-level package directory.
            for prefix in self._top_level or {
                p.split("/", 1)[0] for p in paths if "/" in p
            }:
                if prefix == head:
                    continue
                candidates.append(f"{prefix}/{mod_path}.py")
                candidates.append(f"{prefix}/{mod_path}/__init__.py")
        for candidate in candidates:
            normalized = normalize_relative_path(candidate)
            if normalized in paths:
                return normalized
        return None

    # -- queries -----------------------------------------------------------

    def definition(self, name: str, *, kind: str | None = None) -> list[Location]:
        """Exact definitions of ``name`` (also matches ``Class.method``)."""
        self.ensure()
        needle = name.strip()
        if not needle:
            return []
        out: list[Location] = []
        simple = needle.rsplit(".", 1)[-1]
        for rel, symbol in self._by_name.get(simple, []):
            if kind and symbol.kind != kind:
                continue
            if "." in needle and symbol.qualname != needle and symbol.name != needle:
                continue
            out.append(
                Location(
                    path=rel,
                    line=symbol.line,
                    kind=symbol.kind,
                    name=symbol.display,
                    signature=symbol.signature or symbol.doc,
                )
            )
        out.sort(key=lambda loc: (loc.path, loc.line))
        return out

    # -- reference and call-graph helpers ----------------------------------

    def _definition_sites(self, needle: str) -> tuple[set[str], set[str]]:
        """Files defining ``needle``, and those defining it as a strong kind."""
        defining: set[str] = set()
        strong: set[str] = set()
        for rel, symbol in self._by_name.get(needle, []):
            defining.add(rel)
            if symbol.kind in STRONG_KINDS:
                strong.add(rel)
        return defining, strong

    def _importers_of(self, defining: set[str]) -> set[str]:
        """Files that import any of ``defining``, through re-exports too."""
        near: set[str] = set()
        for rel in defining:
            near |= self._dependents.get(rel, set())
        for rel in [item for item in near if _is_reexport(item)]:
            near |= self._dependents.get(rel, set())
        return near

    def _import_scope(self, rel: str) -> set[str]:
        """Files ``rel`` can reach, following one hop through re-exports."""
        direct = self._dependencies.get(rel, set())
        scope = {rel} | direct
        for dep in direct:
            if _is_reexport(dep):
                scope |= self._dependencies.get(dep, set())
        return scope

    def _callable_definitions(self, name: str) -> list[tuple[str, Symbol]]:
        return [
            (rel, symbol)
            for rel, symbol in self._by_name.get(name, [])
            if symbol.kind in CALLABLE_KINDS
        ]

    def _is_unambiguous(self, name: str) -> bool:
        """True when exactly one project definition can answer to ``name``.

        Matching a callee by bare name is only trustworthy when nothing else in
        the project shares that name; ``get`` or ``refresh`` never qualify.
        """
        return len(self._callable_definitions(name)) == 1

    def _scope_label(self, rel: str, scope: str) -> tuple[str, str, str]:
        """``(name, kind, signature)`` describing the definition around a use."""
        if not scope:
            return "(module level)", "module", ""
        symbol = self._by_qual.get((rel, scope))
        if symbol is None:
            # A nested helper the symbol table does not carry; the scope path is
            # still the most precise answer available.
            return scope, "scope", ""
        return symbol.display, symbol.kind, symbol.signature

    def _add_context(self, locations: list[Location]) -> list[Location]:
        """Attach the source line to each location, reading only those files."""
        wanted: dict[str, set[int]] = defaultdict(set)
        for loc in locations:
            wanted[loc.path].add(loc.line)
        text_by_path: dict[str, dict[int, str]] = {}
        for rel, lines in wanted.items():
            try:
                content = (self.root / rel).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            split = content.splitlines()
            text_by_path[rel] = {
                number: split[number - 1].strip()
                for number in lines
                if 0 < number <= len(split)
            }
        out: list[Location] = []
        for loc in locations:
            context = text_by_path.get(loc.path, {}).get(loc.line, "")
            out.append(
                Location(
                    path=loc.path,
                    line=loc.line,
                    kind=loc.kind,
                    name=loc.name,
                    signature=loc.signature,
                    context=context or loc.context,
                    confidence=loc.confidence,
                )
            )
        return out

    def references(
        self, name: str, *, limit: int = 60, calls_only: bool = False
    ) -> tuple[list[Location], int]:
        """Recorded uses of ``name``, best-ranked first.

        Returns ``(locations, total_found)``. Uses come from the reference
        index built at parse time, so string literals and comments never appear
        and each hit names the definition that encloses it. Ranking puts real
        calls in files that import the definition first.
        """
        self._ensure_refs()
        needle = name.strip().rsplit(".", 1)[-1]
        if not needle:
            return [], 0
        defining, strong = self._definition_sites(needle)
        near = self._importers_of(strong or defining)

        hits: list[tuple[int, Location]] = []
        for rel, ref in self._refs_by_name.get(needle, []):
            is_call = ref.kind in CALL_KINDS
            if calls_only and not is_call:
                continue
            if is_call:
                rank = 0 if rel in near else 1
            else:
                rank = 2 if rel in near else 3
            if rel in near:
                confidence = f"{ref.kind}, file imports the definition"
            elif rel in defining:
                confidence = f"{ref.kind}, same file as definition"
            else:
                confidence = ref.kind
            scope_name, scope_kind, signature = self._scope_label(rel, ref.scope)
            hits.append((
                rank,
                Location(
                    path=rel,
                    line=ref.line,
                    kind=scope_kind,
                    name=scope_name,
                    signature=signature,
                    confidence=confidence,
                ),
            ))
            if len(hits) >= _MAX_REFERENCE_HITS:
                break

        hits.sort(key=lambda item: (item[0], item[1].path, item[1].line))
        page = [loc for _, loc in hits[:limit]]
        return self._add_context(page), len(hits)

    def callers(
        self,
        name: str,
        *,
        limit: int = 60,
        defined_in: str | None = None,
        resolved_only: bool = False,
    ) -> list[Location]:
        """Definitions that call, subclass or decorate with ``name``.

        Only true call-graph edges are returned, deduplicated per enclosing
        definition, so the result reads as "these functions depend on this one".

        ``defined_in`` pins the query to one definition site: only files that
        import it (or the file itself) can be callers. That is what keeps a
        common leaf name like ``refresh`` from collecting unrelated homonyms.
        ``resolved_only`` drops matches that rest on nothing but a shared name.
        """
        self._ensure_refs()
        needle = name.strip().rsplit(".", 1)[-1]
        if not needle:
            return []
        if defined_in is not None:
            defining = {defined_in}
            near = self._importers_of(defining)
        else:
            defining, strong = self._definition_sites(needle)
            near = self._importers_of(strong or defining)

        best: dict[tuple[str, str], tuple[int, Location]] = {}
        for rel, ref in self._refs_by_name.get(needle, []):
            if ref.kind not in CALL_KINDS:
                continue
            if rel in near:
                rank, confidence = 0, f"{ref.kind}, imports the definition"
            elif rel in defining:
                rank, confidence = 1, f"{ref.kind}, same file"
            elif defined_in is not None or resolved_only:
                # Nothing ties this file to the definition asked about.
                continue
            else:
                rank, confidence = 2, f"{ref.kind}, name match"
            scope_name, scope_kind, signature = self._scope_label(rel, ref.scope)
            key = (rel, ref.scope)
            existing = best.get(key)
            if existing is not None and existing[1].line <= ref.line:
                continue
            best[key] = (
                rank,
                Location(
                    path=rel,
                    line=ref.line,
                    kind=scope_kind,
                    name=scope_name,
                    signature=signature,
                    confidence=confidence,
                ),
            )
        ordered = sorted(best.values(), key=lambda item: (item[0], item[1].path, item[1].line))
        return self._add_context([loc for _, loc in ordered[:limit]])

    def callees(self, name: str, *, limit: int = 60) -> list[Location]:
        """Project definitions that ``name`` calls, subclasses or decorates.

        Calls into the standard library and third-party packages are omitted:
        only names that resolve to a definition in this project are edges here.
        """
        self._ensure_refs()
        needle = name.strip()
        if not needle:
            return []
        simple = needle.rsplit(".", 1)[-1]
        targets: list[tuple[str, Symbol]] = [
            (rel, symbol)
            for rel, symbol in self._by_name.get(simple, [])
            if "." not in needle or symbol.display == needle or symbol.name == needle
        ]
        if not targets:
            return []

        best: dict[tuple[str, str], tuple[int, Location]] = {}
        for rel, symbol in targets:
            scope = symbol.display
            entry = self.entries.get(rel)
            if entry is None:
                continue
            reachable = self._import_scope(rel)
            for ref in entry.refs:
                if ref.kind not in CALL_KINDS:
                    continue
                # Include nested helpers defined inside the target.
                if ref.scope != scope and not ref.scope.startswith(f"{scope}."):
                    continue
                if ref.name == symbol.name:
                    continue  # recursion is not new information here
                definitions = self._callable_definitions(ref.name)
                if not definitions:
                    continue  # not a callable project symbol
                # A target the caller actually imports, or one in the same file,
                # is a resolved edge. Anything else is a bare name coincidence,
                # trustworthy only when the name is unique in the project.
                resolved = [
                    (target_rel, target)
                    for target_rel, target in definitions
                    if target_rel in reachable
                ]
                if not resolved and (len(definitions) > 1 or ref.name in _BUILTINS):
                    continue
                candidates = resolved or definitions
                rank = 0 if resolved else 1
                target_rel, target = candidates[0]
                key = (target_rel, target.display)
                if key in best:
                    continue
                best[key] = (
                    rank,
                    Location(
                        path=target_rel,
                        line=target.line,
                        kind=target.kind,
                        name=target.display,
                        signature=target.signature or target.doc,
                        confidence=(
                            f"{ref.kind} from {scope}"
                            if resolved
                            else f"{ref.kind} from {scope}, name match only"
                        ),
                    ),
                )
        ordered = sorted(best.values(), key=lambda item: (item[0], item[1].path, item[1].line))
        return [loc for _, loc in ordered[:limit]]

    def members(self, name: str) -> list[Location]:
        """Definitions contained in ``name`` (a class's methods, for example)."""
        self.ensure()
        needle = name.strip()
        if not needle:
            return []
        simple = needle.rsplit(".", 1)[-1]
        out: list[Location] = []
        for rel, symbol in self._by_name.get(simple, []):
            if "." in needle and symbol.display != needle:
                continue
            prefix = f"{symbol.display}."
            entry = self.entries.get(rel)
            if entry is None:
                continue
            for candidate in entry.symbols:
                qual = candidate.display
                if not qual.startswith(prefix):
                    continue
                # Direct children only, so a nested class does not flatten.
                if "." in qual[len(prefix):]:
                    continue
                out.append(
                    Location(
                        path=rel,
                        line=candidate.line,
                        kind=candidate.kind,
                        name=qual,
                        signature=candidate.signature or candidate.doc,
                        confidence="" if candidate.exported else "private",
                    )
                )
        out.sort(key=lambda loc: (loc.path, loc.line))
        return out

    def container(self, name: str) -> list[Location]:
        """Definitions that contain ``name``, walking outward."""
        self.ensure()
        needle = name.strip()
        if not needle:
            return []
        simple = needle.rsplit(".", 1)[-1]
        out: list[Location] = []
        for rel, symbol in self._by_name.get(simple, []):
            if "." in needle and symbol.display != needle:
                continue
            parts = symbol.display.split(".")[:-1]
            while parts:
                parent = self._by_qual.get((rel, ".".join(parts)))
                if parent is not None:
                    out.append(
                        Location(
                            path=rel,
                            line=parent.line,
                            kind=parent.kind,
                            name=parent.display,
                            signature=parent.signature or parent.doc,
                        )
                    )
                parts.pop()
        out.sort(key=lambda loc: (loc.path, loc.line))
        return out

    def impact(self, name: str, *, max_depth: int = 3) -> dict[str, Any]:
        """Transitive caller closure of ``name``, for change-risk analysis.

        Breadth-first over call edges. Every hop past the first is anchored to
        the file of the symbol it came from, so the walk follows one specific
        definition instead of every project symbol sharing a leaf name.
        Returns the callers per depth, the files involved, and which are tests.
        """
        self._ensure_refs()
        needle = name.strip().rsplit(".", 1)[-1]
        depth_cap = max(1, min(max_depth, _MAX_IMPACT_DEPTH))
        empty: dict[str, Any] = {
            "symbol": name, "levels": [], "files": [], "tests": [], "truncated": False,
        }
        if not needle:
            return empty

        seen_nodes: set[tuple[str, str]] = set()
        levels: list[list[Location]] = []
        files: set[str] = set()
        truncated = False
        # (symbol name, defining file or None for the first hop, depth)
        queue: deque[tuple[str, str | None, int]] = deque([(needle, None, 0)])
        visited_edges: set[tuple[str, str | None]] = {(needle, None)}

        while queue:
            current, anchor, depth = queue.popleft()
            if depth >= depth_cap:
                continue
            found = self.callers(
                current,
                limit=_MAX_REFERENCE_HITS,
                defined_in=anchor,
                resolved_only=depth > 0,
            )
            level: list[Location] = []
            for loc in found:
                node = (loc.path, loc.name)
                if node in seen_nodes:
                    continue
                seen_nodes.add(node)
                level.append(loc)
                files.add(loc.path)
                if loc.kind == "module":
                    continue  # module-level use has no caller of its own
                leaf = loc.name.rsplit(".", 1)[-1]
                edge = (leaf, loc.path)
                if edge not in visited_edges:
                    visited_edges.add(edge)
                    queue.append((leaf, loc.path, depth + 1))
            if level:
                while len(levels) <= depth:
                    levels.append([])
                levels[depth].extend(level)
            if len(seen_nodes) >= _MAX_REFERENCE_HITS:
                truncated = True
                break

        tests = sorted(rel for rel in files if _looks_like_test(rel))
        return {
            "symbol": name,
            "levels": levels,
            "files": sorted(files),
            "tests": tests,
            "truncated": truncated,
        }

    def search(
        self,
        query: str,
        *,
        kind: str | None = None,
        limit: int = 50,
        exported_only: bool = False,
    ) -> list[Location]:
        """Fuzzy symbol search: exact, then prefix, then substring.

        Walks the casefolded name index (built once in ``_build_derived``)
        instead of every file × symbol. Exact hits are O(1); prefix and
        substring stop once the better ranks already fill ``limit``.
        """
        self.ensure()
        needle = query.strip().lower()
        if not needle or limit <= 0:
            return []

        scored: list[tuple[int, Location]] = []

        def _append(rank: int, rel: str, symbol: Symbol) -> None:
            if kind and symbol.kind != kind:
                return
            if exported_only and not symbol.exported:
                return
            scored.append((
                rank,
                Location(
                    path=rel,
                    line=symbol.line,
                    kind=symbol.kind,
                    name=symbol.display,
                    signature=symbol.signature or symbol.doc,
                ),
            ))

        # Rank 0: exact name (O(1) bucket).
        for rel, symbol in self._by_name_lower.get(needle, ()):
            _append(0, rel, symbol)
        if len(scored) >= limit:
            scored.sort(key=lambda item: (item[0], len(item[1].name), item[1].path))
            return [loc for _, loc in scored[:limit]]

        # Rank 1 then 2 over unique lowered names. Qualname (rank 3) is rarer
        # and only scanned when the name ranks still leave room under limit.
        for lowered, locs in self._by_name_lower.items():
            if lowered == needle:
                continue
            if lowered.startswith(needle):
                for rel, symbol in locs:
                    _append(1, rel, symbol)
        if len(scored) >= limit:
            scored.sort(key=lambda item: (item[0], len(item[1].name), item[1].path))
            return [loc for _, loc in scored[:limit]]

        for lowered, locs in self._by_name_lower.items():
            if lowered == needle or lowered.startswith(needle):
                continue
            if needle in lowered:
                for rel, symbol in locs:
                    _append(2, rel, symbol)
        if len(scored) >= limit:
            scored.sort(key=lambda item: (item[0], len(item[1].name), item[1].path))
            return [loc for _, loc in scored[:limit]]

        # Rank 3: name missed but qualname contains the needle.
        for rel, entry in self.entries.items():
            for symbol in entry.symbols:
                lowered = symbol.name.lower()
                if (
                    lowered == needle
                    or lowered.startswith(needle)
                    or needle in lowered
                ):
                    continue
                qual = (symbol.qualname or "").lower()
                if qual and needle in qual:
                    _append(3, rel, symbol)

        scored.sort(key=lambda item: (item[0], len(item[1].name), item[1].path))
        return [loc for _, loc in scored[:limit]]

    def outline(self, path: str) -> tuple[str, list[Symbol]] | None:
        """Resolved path and symbols of one file, tolerating partial paths."""
        self.ensure()
        cleaned = normalize_relative_path(path)
        if cleaned in self.entries:
            return cleaned, self.entries[cleaned].symbols
        matches = [rel for rel in self.entries if rel.endswith("/" + cleaned)]
        if len(matches) == 1:
            return matches[0], self.entries[matches[0]].symbols
        if matches:
            return None
        return None

    def candidates_for(self, path: str) -> list[str]:
        cleaned = normalize_relative_path(path)
        return sorted(rel for rel in self.entries if rel.endswith("/" + cleaned))

    def dependencies(self, path: str) -> list[str]:
        self.ensure()
        return sorted(self._dependencies.get(path, set()))

    def dependents(self, path: str) -> list[str]:
        self.ensure()
        return sorted(self._dependents.get(path, set()))

    def edges(self) -> list[tuple[str, str]]:
        self.ensure()
        return sorted(self._edges)

    def hubs(self, limit: int = 15) -> list[tuple[str, int, int]]:
        """Most connected files as ``(path, in_degree, out_degree)``."""
        self.ensure()
        rows = [
            (
                rel,
                len(self._dependents.get(rel, ())),
                len(self._dependencies.get(rel, ())),
            )
            for rel in self.entries
        ]
        rows = [row for row in rows if row[1] + row[2] > 0]
        rows.sort(key=lambda row: (row[1] + row[2], row[1]), reverse=True)
        return rows[:limit]

    def language_breakdown(self) -> dict[str, int]:
        self.ensure()
        counts: dict[str, int] = defaultdict(int)
        for entry in self.entries.values():
            counts[entry.language or "other"] += 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def symbol_count(self) -> int:
        self.ensure()
        return sum(len(entry.symbols) for entry in self.entries.values())

    def reference_count(self) -> int:
        """Total recorded uses. Forces the reference index to load."""
        self._ensure_refs()
        return sum(len(entry.refs) for entry in self.entries.values())

    def call_edge_count(self) -> int:
        """Recorded uses that are true call-graph edges."""
        self._ensure_refs()
        return sum(
            1
            for entry in self.entries.values()
            for ref in entry.refs
            if ref.kind in CALL_KINDS
        )

    def parse_errors(self) -> list[tuple[str, str]]:
        self.ensure()
        return sorted(
            (rel, entry.parse_error)
            for rel, entry in self.entries.items()
            if entry.parse_error
        )

    def overview(self, *, include_refs: bool = False) -> dict[str, Any]:
        """Project summary. ``include_refs`` also loads the reference index."""
        self.ensure()
        data: dict[str, Any] = {
            "root": str(self.root),
            "files": len(self.entries),
            "symbols": self.symbol_count(),
            "edges": len(self._edges),
            "languages": self.language_breakdown(),
            "hubs": self.hubs(),
            "parse_errors": len(self.parse_errors()),
            "stats": self.stats.summary() if self.stats else "",
        }
        if include_refs:
            data["references"] = self.reference_count()
            data["call_edges"] = self.call_edge_count()
        return data


# ---------------------------------------------------------------------------
# Process-wide cache: one index per project root
# ---------------------------------------------------------------------------

_instances: dict[str, CodeIndex] = {}


def get_index(root: Path | str) -> CodeIndex:
    """Return the shared :class:`CodeIndex` for ``root``."""
    resolved = Path(root).expanduser().resolve(strict=False)
    key = str(resolved)
    index = _instances.get(key)
    if index is None:
        index = CodeIndex(resolved)
        _instances[key] = index
    return index
