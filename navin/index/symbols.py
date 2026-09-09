# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Symbol and import extraction for the code index.

Three layers, tried in order so a missing native wheel never makes indexing
worse than the pure-Python path:

1. Python → stdlib :mod:`ast` (exact symbols, qualnames, signatures).
2. Other languages → ``navin_core.ts_extract`` when the wheel is present
   (tree-sitter grammars for JS/TS/Go/Rust/Java/C/C++/C#/Ruby/PHP/Kotlin/
   Swift).
3. Fallback → declarative pattern table in ``languages.json`` (vue, sql,
   and anything the native layer returned ``None`` for).

Adding a language is still a data change for the fallback; wiring a new
tree-sitter grammar is an incremental ``navin-core`` change, not a rewrite
of this module or of ``languages.json``.
"""

from __future__ import annotations

import ast
import json
import os
import re
import time
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_PARSE_BYTES = 2 * 1024 * 1024
_MAX_SYMBOLS_PER_FILE = 2000
_MAX_REFS_PER_FILE = 4000
_SIGNATURE_MAX = 160

# Reference kinds, ordered from strongest to weakest evidence of real use.
REF_CALL = "call"
REF_BASE = "base"
REF_DECORATOR = "decorator"
REF_NAME = "name"
REF_ATTR = "attr"
# Only calls, inheritance and decorators are true call-graph edges; plain name
# and attribute loads are usage evidence that keeps recall on par with a text
# search without claiming to be a resolved call.
CALL_KINDS = frozenset({REF_CALL, REF_BASE, REF_DECORATOR})

_LANGUAGES_PATH = Path(__file__).with_name("languages.json")
_table_cache: dict[str, tuple[float, dict[str, Any]]] = {}


@dataclass(frozen=True, slots=True)
class Symbol:
    """One definition found in a file."""

    name: str
    kind: str
    line: int
    end_line: int
    qualname: str = ""
    signature: str = ""
    exported: bool = True
    doc: str = ""

    @property
    def display(self) -> str:
        return self.qualname or self.name

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "line": self.line,
            "end_line": self.end_line,
        }
        if self.qualname and self.qualname != self.name:
            out["qualname"] = self.qualname
        if self.signature:
            out["signature"] = self.signature
        if not self.exported:
            out["exported"] = False
        if self.doc:
            out["doc"] = self.doc
        return out

    @staticmethod
    def from_json(raw: dict[str, Any]) -> Symbol:
        return Symbol(
            name=str(raw.get("name", "")),
            kind=str(raw.get("kind", "")),
            line=int(raw.get("line", 1)),
            end_line=int(raw.get("end_line", raw.get("line", 1))),
            qualname=str(raw.get("qualname", "")),
            signature=str(raw.get("signature", "")),
            exported=bool(raw.get("exported", True)),
            doc=str(raw.get("doc", "")),
        )


@dataclass(frozen=True, slots=True)
class Reference:
    """One use of an identifier, attributed to the definition that encloses it.

    ``scope`` is the qualified name of the enclosing function, method or class
    ("" at module level). That attribution is what turns a flat occurrence list
    into a call graph: an edge runs from ``scope`` to ``name``.
    """

    name: str
    line: int
    kind: str = REF_CALL
    scope: str = ""

    def to_json(self) -> list[Any]:
        # Stored positionally rather than as an object: references outnumber
        # symbols four to one, so key names would dominate the cache file.
        out: list[Any] = [self.name, self.line, self.kind]
        if self.scope:
            out.append(self.scope)
        return out

    @staticmethod
    def from_json(raw: Any) -> Reference | None:
        if not isinstance(raw, list) or len(raw) < 3:
            return None
        try:
            return Reference(
                name=str(raw[0]),
                line=int(raw[1]),
                kind=str(raw[2]),
                scope=str(raw[3]) if len(raw) > 3 else "",
            )
        except (TypeError, ValueError):
            return None


@dataclass(slots=True)
class FileEntry:
    """Indexed state of one file."""

    path: str
    language: str
    mtime_ns: int
    size: int
    lines: int
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    # Identifier uses attributed to their enclosing definition. Persisted in a
    # separate cache file and loaded on demand, so reference queries never slow
    # down definition lookups or the dependency graph.
    refs: list[Reference] = field(default_factory=list)
    parse_error: str = ""
    # File-level summary: the module docstring (Python) or the leading comment
    # block. Feeds automatic project metadata so file roles need no manual
    # annotation.
    doc: str = ""

    def to_json(self) -> dict[str, Any]:
        """Serialize everything except references, which cache separately."""
        out: dict[str, Any] = {
            "language": self.language,
            "mtime_ns": self.mtime_ns,
            "size": self.size,
            "lines": self.lines,
        }
        if self.doc:
            out["doc"] = self.doc
        if self.symbols:
            out["symbols"] = [s.to_json() for s in self.symbols]
        if self.imports:
            out["imports"] = self.imports
        if self.parse_error:
            out["parse_error"] = self.parse_error
        return out

    def refs_to_json(self) -> dict[str, Any]:
        """Serialize references with the stamp needed to validate them."""
        return {
            "mtime_ns": self.mtime_ns,
            "size": self.size,
            "refs": [ref.to_json() for ref in self.refs],
        }

    def load_refs_json(self, raw: dict[str, Any]) -> bool:
        """Adopt cached references when the stamp matches. Returns success."""
        if int(raw.get("mtime_ns", -1)) != self.mtime_ns:
            return False
        if int(raw.get("size", -1)) != self.size:
            return False
        parsed = [Reference.from_json(item) for item in raw.get("refs", [])]
        self.refs = [ref for ref in parsed if ref is not None]
        return True

    @staticmethod
    def from_json(path: str, raw: dict[str, Any]) -> FileEntry:
        return FileEntry(
            path=path,
            language=str(raw.get("language", "")),
            mtime_ns=int(raw.get("mtime_ns", 0)),
            size=int(raw.get("size", 0)),
            lines=int(raw.get("lines", 0)),
            symbols=[Symbol.from_json(s) for s in raw.get("symbols", [])],
            imports=[str(i) for i in raw.get("imports", [])],
            parse_error=str(raw.get("parse_error", "")),
            doc=str(raw.get("doc", "")),
        )


# ---------------------------------------------------------------------------
# Language table
# ---------------------------------------------------------------------------


def _read_table(path: Path) -> dict[str, Any]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    key = str(path)
    cached = _table_cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    _table_cache[key] = (mtime, data)
    return data


def language_table() -> dict[str, dict[str, Any]]:
    """Packaged languages merged with an optional user override."""
    packaged = _read_table(_LANGUAGES_PATH).get("languages", {})
    merged: dict[str, dict[str, Any]] = dict(packaged) if isinstance(packaged, dict) else {}
    with suppress(Exception):
        from navin.config.loader import get_config_path

        user = _read_table(get_config_path().parent / "index_languages.json")
        user_langs = user.get("languages")
        if isinstance(user_langs, dict):
            merged.update(user_langs)
    return merged


_ext_map_cache: dict[str, str] | None = None
_ext_map_checked = 0.0

# The table is two JSON files that a user edits by hand, if ever. Re-reading it
# is worth doing eventually, not tens of thousands of times per project scan.
_EXT_MAP_TTL_S = 2.0


def _extension_map() -> dict[str, str]:
    """Suffix → language name.

    Consulted once per candidate file, so on a large repository this runs tens
    of thousands of times per scan. Rebuilding meant calling ``language_table``,
    which stats two JSON files and resolves the config directory through
    ``Path.home()``; that alone accounted for most of the scan. The override is
    still honoured, just after a short delay instead of instantly.
    """
    global _ext_map_cache, _ext_map_checked
    now = time.monotonic()
    if _ext_map_cache is not None and now - _ext_map_checked < _EXT_MAP_TTL_S:
        return _ext_map_cache
    mapping: dict[str, str] = {}
    for name, spec in language_table().items():
        for ext in spec.get("extensions", []):
            mapping[str(ext).lower()] = name
    _ext_map_cache = mapping
    _ext_map_checked = now
    return mapping


def reset_language_cache() -> None:
    """Drop the memoised table so a fresh override is picked up at once."""
    global _ext_map_cache, _ext_map_checked
    _ext_map_cache = None
    _ext_map_checked = 0.0


def language_for(path: str) -> str:
    """Return the language name for a path, or '' when unsupported."""
    # os.path over Path: constructing a Path per file is measurable at this
    # call rate, and only the suffix is needed.
    suffix = os.path.splitext(path)[1].lower()
    return _extension_map().get(suffix, "")


_compiled_cache: dict[str, list[tuple[str, re.Pattern[str]]]] = {}
_import_cache: dict[str, list[re.Pattern[str]]] = {}


def _compiled_patterns(language: str, spec: dict[str, Any]) -> list[tuple[str, re.Pattern[str]]]:
    cached = _compiled_cache.get(language)
    if cached is not None:
        return cached
    out: list[tuple[str, re.Pattern[str]]] = []
    # Case sensitivity is opt-in per language: most languages need it (Go
    # exports by capitalization, React components start uppercase), while
    # keyword-insensitive languages like SQL set "ignore_case": true.
    flags = re.IGNORECASE if spec.get("ignore_case") else 0
    for rule in spec.get("patterns", []):
        if not isinstance(rule, dict):
            continue
        raw = rule.get("regex")
        kind = str(rule.get("kind", "symbol"))
        if not raw:
            continue
        with suppress(re.error):
            out.append((kind, re.compile(r"\s*(?:" + str(raw) + ")", flags)))
    _compiled_cache[language] = out
    return out


def _compiled_imports(language: str, spec: dict[str, Any]) -> list[re.Pattern[str]]:
    cached = _import_cache.get(language)
    if cached is not None:
        return cached
    out: list[re.Pattern[str]] = []
    for rule in spec.get("imports", []):
        if not isinstance(rule, dict):
            continue
        raw = rule.get("regex")
        if not raw:
            continue
        with suppress(re.error):
            out.append(re.compile(str(raw)))
    _import_cache[language] = out
    return out


def _is_exported(rule: str, name: str, line: str) -> bool:
    if rule == "not_underscore":
        return not name.startswith("_")
    if rule == "capitalized":
        return bool(name) and name[0].isupper()
    if rule.startswith("keyword:"):
        return rule.split(":", 1)[1] in line
    return True


# ---------------------------------------------------------------------------
# Python (exact, via ast)
# ---------------------------------------------------------------------------


def _py_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args
    parts: list[str] = []
    positional = [*args.posonlyargs, *args.args]
    defaults_start = len(positional) - len(args.defaults)
    for i, arg in enumerate(positional):
        text = arg.arg
        if i >= defaults_start:
            text += "=…"
        parts.append(text)
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    elif args.kwonlyargs:
        parts.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        parts.append(f"{arg.arg}=…" if default is not None else arg.arg)
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")
    signature = f"{node.name}({', '.join(parts)})"
    return signature[:_SIGNATURE_MAX]


def _py_doc_summary(node: ast.AST) -> str:
    if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
        return ""
    doc = ast.get_docstring(node, clean=True)
    if not doc:
        return ""
    first = doc.strip().split("\n", 1)[0].strip()
    return first[:200]


_ROLE_MAX = 300


def _leading_comment_summary(text: str, comment_prefixes: list[str]) -> str:
    """First sentence of a file's leading comment block, if any."""
    collected: list[str] = []
    for raw_line in text.splitlines()[:40]:
        line = raw_line.strip()
        if not line:
            if collected:
                break
            continue
        if line.startswith("/**") or line.startswith("/*"):
            line = line.lstrip("/*").strip()
        elif line.startswith("*/"):
            break
        elif line.startswith("*"):
            line = line[1:].strip()
        elif any(line.startswith(prefix) for prefix in comment_prefixes):
            for prefix in comment_prefixes:
                if line.startswith(prefix):
                    line = line[len(prefix) :].strip()
                    break
        else:
            break
        if line:
            collected.append(line)
        if len(" ".join(collected)) > _ROLE_MAX:
            break
    summary = " ".join(collected).strip()
    # Drop license headers and lint pragmas: they describe nothing useful.
    lowered = summary.lower()
    if any(
        lowered.startswith(noise)
        for noise in ("copyright", "license", "licensed", "eslint", "@ts-", "prettier")
    ):
        return ""
    return summary[:_ROLE_MAX]


def _python_symbols(
    text: str,
) -> tuple[list[Symbol], list[str], str, str, list[Reference]]:
    """Return ``(symbols, imports, parse_error, module_doc, refs)``."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return [], [], f"{type(exc).__name__}: {exc}", "", []
    module_doc = _py_doc_summary(tree)

    symbols: list[Symbol] = []
    imports: list[str] = []

    def end_of(node: ast.AST, fallback: int) -> int:
        return int(getattr(node, "end_lineno", None) or fallback)

    def visit_body(body: list[ast.stmt], prefix: str, depth: int) -> None:
        if depth > 3 or len(symbols) >= _MAX_SYMBOLS_PER_FILE:
            return
        for node in body:
            if len(symbols) >= _MAX_SYMBOLS_PER_FILE:
                return
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                qual = f"{prefix}{node.name}"
                symbols.append(
                    Symbol(
                        name=node.name,
                        kind="method" if prefix else "function",
                        line=node.lineno,
                        end_line=end_of(node, node.lineno),
                        qualname=qual,
                        signature=_py_signature(node),
                        exported=not node.name.startswith("_"),
                        doc=_py_doc_summary(node),
                    )
                )
                # Nested defs are rarely useful for navigation; only descend
                # into classes to pick up methods.
                visit_body(
                    [n for n in node.body if isinstance(n, ast.ClassDef)],
                    f"{qual}.",
                    depth + 1,
                )
            elif isinstance(node, ast.ClassDef):
                qual = f"{prefix}{node.name}"
                bases = []
                for base in node.bases:
                    with suppress(Exception):
                        bases.append(ast.unparse(base))
                signature = f"class {node.name}"
                if bases:
                    signature += f"({', '.join(bases)})"
                symbols.append(
                    Symbol(
                        name=node.name,
                        kind="class",
                        line=node.lineno,
                        end_line=end_of(node, node.lineno),
                        qualname=qual,
                        signature=signature[:_SIGNATURE_MAX],
                        exported=not node.name.startswith("_"),
                        doc=_py_doc_summary(node),
                    )
                )
                visit_body(node.body, f"{qual}.", depth + 1)
            elif isinstance(node, ast.Assign | ast.AnnAssign) and not prefix:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    name = target.id
                    kind = "constant" if name.isupper() else "variable"
                    symbols.append(
                        Symbol(
                            name=name,
                            kind=kind,
                            line=node.lineno,
                            end_line=end_of(node, node.lineno),
                            qualname=name,
                            exported=not name.startswith("_"),
                        )
                    )
    visit_body(tree.body, "", 0)

    # Imports are collected over the whole tree, not just module level: lazy
    # imports inside functions are common and carry real dependency edges.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # Relative imports keep their dots so the resolver can walk up.
            imports.append("." * (node.level or 0) + module)

    return symbols, list(dict.fromkeys(imports)), "", module_doc, _python_refs(tree)


class _RefCollector(ast.NodeVisitor):
    """Collect identifier uses, tagging each with its enclosing definition.

    Deduplicated on ``(name, scope, kind)`` keeping the first line: a function
    that calls ``helper()`` six times yields one edge, which is what impact
    analysis needs and keeps the cache small.
    """

    def __init__(self) -> None:
        self._scope: list[str] = []
        self._seen: set[tuple[str, str, str]] = set()
        self.refs: list[Reference] = []

    @property
    def scope(self) -> str:
        return ".".join(self._scope)

    def _record(self, name: str, line: int, kind: str) -> None:
        if not name or len(self.refs) >= _MAX_REFS_PER_FILE:
            return
        key = (name, self.scope, kind)
        if key in self._seen:
            return
        self._seen.add(key)
        self.refs.append(Reference(name=name, line=line, kind=kind, scope=self.scope))

    def _descend(self, node: ast.AST, name: str) -> None:
        self._scope.append(name)
        try:
            self.generic_visit(node)
        finally:
            self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        # Decorators and defaults are evaluated in the *enclosing* scope.
        for decorator in node.decorator_list:
            self._visit_decorator(decorator)
        self._descend(node, node.name)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]  # noqa: N815

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        for decorator in node.decorator_list:
            self._visit_decorator(decorator)
        for base in node.bases:
            target = base
            if isinstance(target, ast.Call):
                target = target.func
            if isinstance(target, ast.Name):
                self._record(target.id, base.lineno, REF_BASE)
            elif isinstance(target, ast.Attribute):
                self._record(target.attr, base.lineno, REF_BASE)
        self._descend(node, node.name)

    def _visit_decorator(self, node: ast.expr) -> None:
        call = node if isinstance(node, ast.Call) else None
        target: ast.AST = call.func if call is not None else node
        if isinstance(target, ast.Name):
            self._record(target.id, node.lineno, REF_DECORATOR)
        elif isinstance(target, ast.Attribute):
            self._record(target.attr, node.lineno, REF_DECORATOR)
            self.visit(target.value)
        else:
            self.visit(target)
        if call is not None:
            self._visit_arguments(call)

    def _visit_arguments(self, node: ast.Call) -> None:
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        # The callee is recorded as a call, never re-walked as a bare name, so
        # one call site produces one reference instead of two.
        func = node.func
        if isinstance(func, ast.Name):
            self._record(func.id, node.lineno, REF_CALL)
        elif isinstance(func, ast.Attribute):
            self._record(func.attr, node.lineno, REF_CALL)
            self.visit(func.value)
        else:
            self.visit(func)
        self._visit_arguments(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, ast.Load):
            self._record(node.id, node.lineno, REF_NAME)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if isinstance(node.ctx, ast.Load):
            self._record(node.attr, node.lineno, REF_ATTR)
        self.generic_visit(node)


def _python_refs(tree: ast.Module) -> list[Reference]:
    collector = _RefCollector()
    with suppress(RecursionError):
        collector.visit(tree)
    return collector.refs


# ---------------------------------------------------------------------------
# Pattern-based languages
# ---------------------------------------------------------------------------

_BLOCK_OPEN = "/*"
_BLOCK_CLOSE = "*/"

# Used only when ast.parse fails (file mid-edit); intentionally coarse.
_PY_FALLBACK_KEY = "__python_fallback__"
_PY_FALLBACK_SPEC: dict[str, Any] = {
    "export_rule": "not_underscore",
    "line_comments": ["#"],
    "patterns": [
        {"kind": "class", "regex": r"class\s+(\w+)"},
        {"kind": "function", "regex": r"(?:async\s+)?def\s+(\w+)"},
    ],
    "imports": [
        {"regex": r"(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))"},
    ],
}


def _code_lines(text: str, comments: list[str]) -> Iterator[tuple[int, str]]:
    """Yield ``(line_number, code)`` with comment content removed.

    Block comments are skipped while code that shares the closing line is
    preserved. Shared by symbol and reference extraction so both see exactly
    the same view of the file.
    """
    in_block = False
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        if in_block:
            if _BLOCK_CLOSE in line:
                in_block = False
                line = line.split(_BLOCK_CLOSE, 1)[1].strip()
                if not line:
                    continue
            else:
                continue
        if _BLOCK_OPEN in line:
            before, _, after = line.partition(_BLOCK_OPEN)
            if _BLOCK_CLOSE not in after:
                in_block = True
            line = before.strip()
            if not line:
                continue

        if any(line.startswith(prefix) for prefix in comments):
            continue
        yield number, line


def _pattern_symbols(
    text: str, language: str, spec: dict[str, Any]
) -> tuple[list[Symbol], list[str]]:
    patterns = _compiled_patterns(language, spec)
    import_patterns = _compiled_imports(language, spec)
    comments = [str(c) for c in spec.get("line_comments", [])]
    export_rule = str(spec.get("export_rule", "always"))

    symbols: list[Symbol] = []
    imports: list[str] = []
    seen: set[tuple[str, str, int]] = set()

    for number, line in _code_lines(text, comments):
        for pattern in import_patterns:
            for match in pattern.finditer(line):
                target = next((g for g in match.groups() if g), "")
                if target:
                    imports.append(target.strip())

        if len(symbols) >= _MAX_SYMBOLS_PER_FILE:
            continue
        for kind, pattern in patterns:
            match = pattern.match(line)
            if match is None:
                continue
            name = next((g for g in match.groups() if g), "")
            if not name:
                continue
            key = (kind, name, number)
            if key in seen:
                break
            seen.add(key)
            symbols.append(
                Symbol(
                    name=name,
                    kind=kind,
                    line=number,
                    end_line=number,
                    qualname=name,
                    signature=line[:_SIGNATURE_MAX],
                    exported=_is_exported(export_rule, name, line),
                )
            )
            break

    return symbols, imports


# Definitions that can enclose other code, used to attribute a reference to a
# scope in languages parsed by pattern rather than by a real parser.
_CONTAINER_KINDS = frozenset({
    "function", "method", "class", "component", "interface", "struct",
    "trait", "protocol", "enum", "module", "record", "hook",
})
_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
_CALL_SITE = re.compile(r"([A-Za-z_$][\w$]*)\s*(?:<[^<>()]*>\s*)?\(")
_STRING_LITERAL = re.compile(r"""(['"`])(?:\\.|(?!\1)[^\\])*\1""")


def _scope_ranges(symbols: list[Symbol]) -> list[tuple[int, str]]:
    """Descending ``(line, qualname)`` of container definitions.

    Pattern-based languages report a single line per definition, so the
    enclosing scope of a reference is approximated by the nearest preceding
    container definition. Callers surface this as an approximation.
    """
    ranges = [
        (symbol.line, symbol.display)
        for symbol in symbols
        if symbol.kind in _CONTAINER_KINDS
    ]
    ranges.sort(key=lambda item: item[0], reverse=True)
    return ranges


def _pattern_refs(
    text: str, spec: dict[str, Any], symbols: list[Symbol]
) -> list[Reference]:
    comments = [str(c) for c in spec.get("line_comments", [])]
    scopes = _scope_ranges(symbols)
    definition_lines = {symbol.line for symbol in symbols}

    refs: list[Reference] = []
    seen: set[tuple[str, str, str]] = set()

    def scope_for(number: int) -> str:
        for line, qualname in scopes:
            if line <= number:
                return qualname
        return ""

    for number, raw in _code_lines(text, comments):
        if len(refs) >= _MAX_REFS_PER_FILE:
            break
        # String contents are not code: dropping them removes the bulk of the
        # false positives a plain text search would report.
        line = _STRING_LITERAL.sub('""', raw)
        scope = scope_for(number)
        called = {match.group(1) for match in _CALL_SITE.finditer(line)}
        for name in called:
            key = (name, scope, REF_CALL)
            if key not in seen:
                seen.add(key)
                refs.append(Reference(name=name, line=number, kind=REF_CALL, scope=scope))
        if number in definition_lines:
            # The declaration line itself is a definition, not a use.
            continue
        for match in _IDENTIFIER.finditer(line):
            name = match.group(0)
            if name in called:
                continue
            key = (name, scope, REF_NAME)
            if key in seen:
                continue
            seen.add(key)
            refs.append(Reference(name=name, line=number, kind=REF_NAME, scope=scope))
            if len(refs) >= _MAX_REFS_PER_FILE:
                break

    return refs


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _tree_sitter_extract(
    language: str, rel_path: str, text: str
) -> tuple[list[Symbol], list[str], list[Reference]] | None:
    """Exact symbols from the native tree-sitter parsers, when available.

    Returns ``None`` when the extension is missing or the language is not
    covered - the caller falls back to the regex pattern table, so this can
    only improve results, never lose them.
    """
    try:
        from navin.utils.native import native

        core = native()
        if core is None:
            return None
        raw = core.ts_extract(language, rel_path, text)
        if not raw:
            return None
        payload = json.loads(raw)
    except BaseException:
        # PyO3 PanicException is BaseException, not Exception - swallowing only
        # Exception left index/metagraph able to kill an entire agent turn.
        return None
    symbols = [Symbol.from_json(s) for s in payload.get("symbols", [])]
    imports = [str(i) for i in payload.get("imports", [])]
    refs = [
        ref
        for item in payload.get("refs", [])
        if (ref := Reference.from_json(item)) is not None
    ]
    return symbols, imports, refs


def extract(rel_path: str, text: str, *, mtime_ns: int = 0, size: int = 0) -> FileEntry:
    """Build the :class:`FileEntry` for one file's contents."""
    language = language_for(rel_path)
    entry = FileEntry(
        path=rel_path,
        language=language,
        mtime_ns=mtime_ns,
        size=size or len(text.encode("utf-8", errors="ignore")),
        lines=text.count("\n") + 1 if text else 0,
    )
    if not language:
        return entry

    spec = language_table().get(language, {})
    comment_prefixes = [str(c) for c in spec.get("line_comments", [])]
    if spec.get("parser") == "ast":
        symbols, imports, error, module_doc, refs = _python_symbols(text)
        entry.symbols = symbols
        entry.imports = imports
        entry.refs = refs
        entry.parse_error = error
        entry.doc = module_doc or _leading_comment_summary(text, comment_prefixes or ["#"])
        if error:
            # A file being edited is often momentarily unparseable; degrade to
            # line patterns so navigation keeps working instead of going blank.
            entry.symbols, entry.imports = _pattern_symbols(
                text, _PY_FALLBACK_KEY, _PY_FALLBACK_SPEC
            )
            entry.refs = _pattern_refs(text, _PY_FALLBACK_SPEC, entry.symbols)
        return entry

    native_result = _tree_sitter_extract(language, rel_path, text)
    if native_result is not None:
        entry.symbols, entry.imports, entry.refs = native_result
        entry.doc = _leading_comment_summary(text, comment_prefixes)
        return entry

    entry.symbols, entry.imports = _pattern_symbols(text, language, spec)
    entry.refs = _pattern_refs(text, spec, entry.symbols)
    entry.doc = _leading_comment_summary(text, comment_prefixes)
    return entry


def extract_from_bytes(
    rel_path: str, data: bytes, *, mtime_ns: int, size: int
) -> FileEntry:
    """Index one file from bytes already read (and already size-checked).

    Shared by the per-file path below and the native batch reader in
    ``navin.index.service``; decoding matches ``read_text(errors="replace")``
    byte for byte, so both paths produce identical entries.
    """
    text = data.decode("utf-8", errors="replace")
    if "\x00" in text[:4096]:
        return FileEntry(
            path=rel_path,
            language="",
            mtime_ns=mtime_ns,
            size=size,
            lines=0,
            parse_error="binary file",
        )
    return extract(rel_path, text, mtime_ns=mtime_ns, size=size)


def read_and_extract(root: Path, rel_path: str) -> FileEntry | None:
    """Read a project file and index it. Returns ``None`` when unreadable."""
    full = root / rel_path
    try:
        stat = full.stat()
    except OSError:
        return None
    if stat.st_size > MAX_PARSE_BYTES:
        return FileEntry(
            path=rel_path,
            language=language_for(rel_path),
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            lines=0,
            parse_error="file too large to index",
        )
    try:
        data = full.read_bytes()
    except OSError:
        return None
    return extract_from_bytes(
        rel_path, data, mtime_ns=stat.st_mtime_ns, size=stat.st_size
    )


def supported_extensions() -> list[str]:
    return sorted(_extension_map())
