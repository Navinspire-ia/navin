# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared helpers for the Navin Security code analyzers.

One bounded walk of the tree (file count, file size and total bytes are
capped), Python trees parsed once, and the AST helpers every analyzer uses to
name calls and recognize web routes. A file that cannot be read or parsed is
listed in ``skipped`` with the reason: it is never silently treated as clean.
"""

from __future__ import annotations

import ast
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "env", "dist", "build", "target", "__pycache__",
    ".next", ".nuxt", "coverage", ".tox", ".mypy_cache", ".pytest_cache", "vendor", "site-packages",
})
PY_EXTS = frozenset({".py"})
JS_EXTS = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"})
SQL_EXTS = frozenset({".sql"})
MAX_FILES = 3000
MAX_BYTES = 400_000
MAX_TOTAL_BYTES = 40_000_000

TENANT_RE = re.compile(r"\b(?:tenant|organization|org|workspace|account|company)_?id\b", re.I)
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass
class SourceFile:
    rel: str
    text: str
    _starts: list[int] | None = field(default=None, repr=False, compare=False)

    def segment(self, node: ast.AST) -> str:
        """The whole lines a node spans: a cheap stand-in for ``ast.unparse`` when only searching text."""
        if self._starts is None:
            self._starts = [0, *(match.end() for match in re.finditer("\n", self.text))]
        first = max(1, getattr(node, "lineno", 1))
        last = max(first, getattr(node, "end_lineno", None) or first)
        begin = self._starts[min(first, len(self._starts)) - 1]
        end = self._starts[last] if last < len(self._starts) else len(self.text)
        return self.text[begin:end]


@dataclass
class SourceSet:
    """The files an analysis reads, collected in one bounded walk."""

    root: Path
    python: list[SourceFile] = field(default_factory=list)
    scripts: list[SourceFile] = field(default_factory=list)
    sql: list[SourceFile] = field(default_factory=list)
    prisma: list[SourceFile] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    _trees: dict[str, ast.Module | None] = field(default_factory=dict)

    @property
    def files_scanned(self) -> int:
        return len(self.python) + len(self.scripts) + len(self.sql) + len(self.prisma)

    def python_trees(self) -> Iterator[tuple[SourceFile, ast.Module]]:
        """Each parsable Python file with its tree; parse failures are recorded once."""
        for src in self.python:
            if src.rel not in self._trees:
                self._trees[src.rel] = self._parse(src)
            tree = self._trees[src.rel]
            if tree is not None:
                yield src, tree

    def _parse(self, src: SourceFile) -> ast.Module | None:
        try:
            return ast.parse(src.text, filename=src.rel)
        except SyntaxError as exc:
            self.skipped.append(f"{src.rel}: not parsed ({exc.msg}, line {exc.lineno})")
        except (ValueError, RecursionError) as exc:
            self.skipped.append(f"{src.rel}: not parsed ({type(exc).__name__})")
        return None


def collect_sources(root: str | Path, *, max_files: int = MAX_FILES, max_bytes: int = MAX_BYTES,
                    max_total_bytes: int = MAX_TOTAL_BYTES) -> SourceSet:
    """Read the Python, JS/TS, SQL and Prisma files under *root*, within the caps."""
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise NotADirectoryError(f"not a directory: {base}")
    found = SourceSet(root=base)
    total = 0
    count = 0
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            full = Path(dirpath) / name
            ext = full.suffix.lower()
            bucket = _bucket(found, name, ext)
            if bucket is None:
                continue
            rel = full.relative_to(base).as_posix()
            try:
                if full.is_symlink():
                    continue
                size = full.stat().st_size
                if size > max_bytes:
                    found.skipped.append(f"{rel}: larger than {max_bytes // 1000} KB, not read")
                    continue
                if total + size > max_total_bytes:
                    found.notes.append(f"stopped reading after {max_total_bytes // 1_000_000} MB; later files were not analyzed")
                    return found
                text = full.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                found.skipped.append(f"{rel}: not read ({exc.strerror or type(exc).__name__})")
                continue
            total += size
            bucket.append(SourceFile(rel=rel, text=text))
            count += 1
            if count >= max_files:
                found.notes.append(f"stopped after {max_files} files; later files were not analyzed")
                return found
    return found


def _bucket(found: SourceSet, name: str, ext: str) -> list[SourceFile] | None:
    if ext in PY_EXTS:
        return found.python
    if ext in JS_EXTS and not name.endswith((".min.js", ".d.ts")):
        return found.scripts
    if ext in SQL_EXTS:
        return found.sql
    if ext == ".prisma":
        return found.prisma
    return None


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def dotted(node: ast.AST | None) -> str:
    """``cursor.execute`` for ``cursor.execute(...)``'s func; empty when not a plain name chain."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif isinstance(node, ast.Call):
        inner = dotted(node.func)
        if not inner:
            return ""
        parts.append(inner)
    else:
        return ""
    return ".".join(reversed(parts))


def root_name(node: ast.AST | None) -> str:
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call, ast.Await)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else ""


def short(node: ast.AST | None, limit: int = 140) -> str:
    if node is None:
        return ""
    try:
        text = ast.unparse(node)
    except (ValueError, RecursionError):
        return type(node).__name__
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def keyword(call: ast.Call, name: str) -> ast.expr | None:
    for item in call.keywords:
        if item.arg == name:
            return item.value
    return None


def functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def all_args(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[ast.arg, ast.expr | None]]:
    """Every parameter with its default value (None when it has none)."""
    positional = [*func.args.posonlyargs, *func.args.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(func.args.defaults)) + list(func.args.defaults)
    pairs = list(zip(positional, defaults))
    pairs.extend(zip(func.args.kwonlyargs, func.args.kw_defaults))
    return pairs


_ROUTE_ATTRS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "route", "api_route", "websocket"})


@dataclass(frozen=True)
class Route:
    methods: tuple[str, ...]
    path: str
    line: int
    decorator: ast.Call = field(compare=False, repr=False)

    @property
    def endpoint(self) -> str:
        return f"{self.methods[0]} {self.path}"

    @property
    def mutating(self) -> bool:
        return bool(MUTATING & set(self.methods))


def route_of(func: ast.FunctionDef | ast.AsyncFunctionDef) -> Route | None:
    """The web route a handler serves (Flask, FastAPI, Starlette, Sanic, Quart style decorators)."""
    for deco in func.decorator_list:
        if not isinstance(deco, ast.Call) or not isinstance(deco.func, ast.Attribute):
            continue
        attr = deco.func.attr.lower()
        if attr not in _ROUTE_ATTRS or not root_name(deco.func):
            continue
        path = const_str(deco.args[0]) if deco.args else const_str(keyword(deco, "path") or keyword(deco, "rule"))
        if not path or not path.startswith("/"):
            continue
        if attr in {"route", "api_route"}:
            listed = keyword(deco, "methods")
            names = [const_str(e) for e in listed.elts] if isinstance(listed, (ast.List, ast.Tuple, ast.Set)) else []
            methods = tuple(n.upper() for n in names if n) or ("GET",)
        elif attr == "websocket":
            methods = ("WEBSOCKET",)
        else:
            methods = (attr.upper(),)
        return Route(methods=methods, path=path, line=deco.lineno, decorator=deco)
    return None


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def line_text(text: str, line: int) -> str:
    lines = text.splitlines()
    return lines[line - 1].strip()[:300] if 0 < line <= len(lines) else ""
