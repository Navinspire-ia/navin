"""Project metagraph: files as nodes, imports/references as edges.

Builds a dependency graph for the Dev workbench Graph tab by scanning the
project tree and parsing import statements (Python and JS/TS relative
imports). Annotations from ``.metadata/index.json`` — maintained by the
agent via the ``project-metadata`` skill — are merged on top: file roles,
kind overrides, and explicit ``depends_on`` edges.

``.metadata/index.json`` format::

    {
      "files": {
        "webui/src/App.tsx": {
          "kind": "front",
          "role": "Root React component: routing and global state",
          "tags": ["entry"],
          "depends_on": ["webui/src/lib/api.ts"]
        }
      }
    }
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from navin.security.workspace_access import WorkspaceScope

MAX_NODES = 1200
MAX_PARSE_BYTES = 256 * 1024
MAX_METADATA_BYTES = 1024 * 1024

_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".ruff_cache",
    ".pytest_cache", "dist", "build", ".next", ".cache", ".metadata",
    ".idea", ".vscode", "coverage", "target", ".tox", ".mypy_cache",
    ".checkpoints",
}

_FRONT_HINT_DIRS = {"webui", "frontend", "client", "ui", "www", "components", "pages"}
_TEST_HINT_DIRS = {"test", "tests", "__tests__", "spec", "e2e"}

_KINDS = ("front", "back", "sql", "config", "test", "docs", "asset", "other")

_PY_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE
)
_JS_IMPORT_RE = re.compile(
    r"""(?:import|export)\s+(?:[\w*\s{},$]+\s+from\s+)?["']((?:\.{1,2}|@)/[^"']+)["']"""
    r"""|require\(\s*["']((?:\.{1,2}|@)/[^"']+)["']\s*\)""",
    re.MULTILINE,
)

_JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_JS_RESOLVE_SUFFIXES = (
    "", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    "/index.ts", "/index.tsx", "/index.js", "/index.jsx",
)


class MetagraphError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _classify(rel: str) -> str:
    parts = rel.lower().split("/")
    name = parts[-1]
    suffix = Path(name).suffix

    if any(p in _TEST_HINT_DIRS for p in parts[:-1]) or name.startswith("test_"):
        if suffix in {".py", *_JS_EXTS}:
            return "test"
    if suffix == ".sql" or ".prisma" in name or "migrations" in parts[:-1]:
        return "sql"
    if suffix in {".css", ".scss", ".sass", ".less", ".html", ".vue", ".svelte"}:
        return "front"
    if suffix in {".tsx", ".jsx"}:
        return "front"
    if suffix in _JS_EXTS:
        return "front" if any(p in _FRONT_HINT_DIRS for p in parts[:-1]) else "back"
    if suffix in {".py", ".go", ".rs", ".java", ".rb", ".php", ".cs", ".kt", ".ex", ".exs"}:
        return "back"
    if suffix in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env", ".lock"} or (
        name in {"makefile", "dockerfile", "justfile"}
    ):
        return "config"
    if suffix in {".md", ".rst", ".txt", ".adoc"}:
        return "docs"
    if suffix in {
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
        ".woff", ".woff2", ".ttf", ".otf", ".mp4", ".mp3", ".wav", ".pdf",
    }:
        return "asset"
    return "other"


def _collect_files(root: Path) -> list[Path]:
    out: list[Path] = []
    stack = [root]
    while stack and len(out) < MAX_NODES:
        current = stack.pop()
        try:
            children = sorted(
                current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
            )
        except OSError:
            continue
        for child in children:
            name = child.name
            if name.startswith(".") and name not in {".env", ".metadata"}:
                continue
            try:
                if child.is_symlink():
                    continue
                if child.is_dir():
                    if name in _SKIP_DIRS:
                        continue
                    stack.append(child)
                elif child.is_file():
                    out.append(child)
                    if len(out) >= MAX_NODES:
                        break
            except OSError:
                continue
    return out


def _python_edges(rel: str, text: str, rel_set: set[str], root_name: str) -> set[str]:
    """Resolve python imports to project-relative files."""
    edges: set[str] = set()
    base_dir = Path(rel).parent
    for match in _PY_IMPORT_RE.finditer(text):
        module = (match.group(1) or match.group(2) or "").strip()
        if not module:
            continue
        mod_paths = [module.replace(".", "/")]
        # "from navin.webui.x import y" inside the navin/ directory itself:
        # the top package name matches the project root, so strip it too.
        head, _, tail = module.partition(".")
        if head == root_name and tail:
            mod_paths.append(tail.replace(".", "/"))
        candidates: list[str] = []
        for mod_path in mod_paths:
            candidates.append(f"{mod_path}.py")
            candidates.append(f"{mod_path}/__init__.py")
            if base_dir != Path("."):
                candidates.append((base_dir / f"{mod_path}.py").as_posix())
                candidates.append((base_dir / mod_path / "__init__.py").as_posix())
        for cand in candidates:
            if cand in rel_set and cand != rel:
                edges.add(cand)
                break
    return edges


def _js_edges(rel: str, text: str, rel_set: set[str]) -> set[str]:
    edges: set[str] = set()
    base_dir = Path(rel).parent
    dir_parts = rel.split("/")[:-1]
    if "src" in dir_parts:
        alias_root = "/".join(dir_parts[: len(dir_parts) - dir_parts[::-1].index("src")])
    else:
        alias_root = ""
    for match in _JS_IMPORT_RE.finditer(text):
        target = (match.group(1) or match.group(2) or "").strip()
        if not target:
            continue
        if target.startswith("@/"):
            # Vite/webpack "@" alias → nearest src/ ancestor (or project root).
            joined = f"{alias_root}/{target[2:]}" if alias_root else target[2:]
        else:
            joined = (base_dir / target).as_posix()
        # Normalize ../ segments.
        norm_parts: list[str] = []
        for part in joined.split("/"):
            if part == "..":
                if norm_parts:
                    norm_parts.pop()
            elif part not in {"", "."}:
                norm_parts.append(part)
        norm = "/".join(norm_parts)
        for suffix in _JS_RESOLVE_SUFFIXES:
            cand = f"{norm}{suffix}"
            if cand in rel_set and cand != rel:
                edges.add(cand)
                break
    return edges


def _load_metadata(root: Path) -> dict[str, Any]:
    index = root / ".metadata" / "index.json"
    if not index.is_file():
        return {}
    try:
        if index.stat().st_size > MAX_METADATA_BYTES:
            return {}
        raw = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    files = raw.get("files")
    return files if isinstance(files, dict) else {}


def metagraph_payload(scope: WorkspaceScope) -> dict[str, Any]:
    root = scope.project_path
    if not root.is_dir():
        raise MetagraphError(404, "project directory not found")
    return build_metagraph(root)


def build_metagraph(root: Path) -> dict[str, Any]:
    """Scan ``root`` and return the metagraph payload (nodes, edges, kinds)."""
    files = _collect_files(root)
    rel_paths = [f.relative_to(root).as_posix() for f in files]
    rel_set = set(rel_paths)
    metadata = _load_metadata(root)

    nodes: list[dict[str, Any]] = []
    edges: set[tuple[str, str]] = set()

    for file, rel in zip(files, rel_paths):
        kind = _classify(rel)
        suffix = file.suffix.lower()
        try:
            size = file.stat().st_size
        except OSError:
            size = 0

        parse_kinds = {"front", "back", "test", "sql"}
        if kind in parse_kinds and size <= MAX_PARSE_BYTES:
            try:
                text = file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            if suffix == ".py":
                for target in _python_edges(rel, text, rel_set, root.name):
                    edges.add((rel, target))
            elif suffix in _JS_EXTS or suffix in {".vue", ".svelte"}:
                for target in _js_edges(rel, text, rel_set):
                    edges.add((rel, target))

        node: dict[str, Any] = {"id": rel, "kind": kind, "size": size}
        meta = metadata.get(rel)
        if isinstance(meta, dict):
            role = meta.get("role")
            if isinstance(role, str) and role.strip():
                node["role"] = role.strip()[:300]
            meta_kind = meta.get("kind")
            if isinstance(meta_kind, str) and meta_kind in _KINDS:
                node["kind"] = meta_kind
            deps = meta.get("depends_on")
            if isinstance(deps, list):
                for dep in deps[:64]:
                    if isinstance(dep, str) and dep in rel_set and dep != rel:
                        edges.add((rel, dep))
        nodes.append(node)

    in_degree: dict[str, int] = {}
    out_degree: dict[str, int] = {}
    for source, target in edges:
        out_degree[source] = out_degree.get(source, 0) + 1
        in_degree[target] = in_degree.get(target, 0) + 1
    for node in nodes:
        node["in_degree"] = in_degree.get(node["id"], 0)
        node["out_degree"] = out_degree.get(node["id"], 0)

    kind_counts: dict[str, int] = {}
    for node in nodes:
        kind_counts[node["kind"]] = kind_counts.get(node["kind"], 0) + 1

    return {
        "project_path": str(root),
        "nodes": nodes,
        "edges": [{"source": s, "target": t} for s, t in sorted(edges)],
        "kinds": kind_counts,
        "has_metadata": bool(metadata),
        "annotated": sum(1 for n in nodes if n.get("role")),
        "truncated": len(files) >= MAX_NODES,
    }
