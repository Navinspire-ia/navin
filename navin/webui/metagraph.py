"""Project metagraph: files as nodes, imports/references as edges.

Builds the dependency graph for the Dev workbench Graph tab on top of
:mod:`navin.index`, which supplies gitignore-aware file discovery, resolved
imports across every supported language, and symbol counts - served from the
incremental cache rather than rescanning the tree per request.

When ``navin_core`` is available, build / layout / query / diff run in Rust.
Python keeps a full fallback so the graph works without the native extension.

``.navin/metadata/index.json`` format::

    {
      "files": {
        "webui/src/App.tsx": {
          "kind": "front",
          "role": "Root React component: routing and global state",
          "tags": ["entry"],
          "depends_on": ["webui/src/lib/api.ts"],
          "fingerprint": "8934:241"
        }
      }
    }

``fingerprint`` is stamped by :func:`annotate_metadata`, never by the model: it
is ``size:lines`` at the moment the role was written, which is how a later build
can tell that a role no longer describes its file.
"""

from __future__ import annotations

import json
import tempfile
import threading
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from navin.security.workspace_access import WorkspaceScope
from navin.utils.native import native

# Display ceiling for the Graph tab. Well above typical project sizes; when it
# is hit, the most connected files are kept (see build_metagraph).
MAX_NODES = 6000
MAX_METADATA_BYTES = 1024 * 1024

_FRONT_HINT_DIRS = {"webui", "frontend", "client", "ui", "www", "components", "pages"}
_TEST_HINT_DIRS = {"test", "tests", "__tests__", "spec", "e2e"}

_KINDS = ("front", "back", "sql", "config", "test", "docs", "asset", "other")

_JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

# Last built GraphState JSON per (root, view), for diffs and sticky layout.
_state_cache: dict[tuple[str, str], dict[str, Any]] = {}
_generation: dict[str, int] = {}
_cache_lock = threading.Lock()


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


def _metadata_path(root: Path) -> Path:
    new_dir = root / ".navin" / "metadata"
    # One-time migration: the knowledge base used to live at <root>/.metadata.
    legacy = root / ".metadata"
    if legacy.is_dir() and not new_dir.exists():
        try:
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            legacy.replace(new_dir)
        except OSError:
            return legacy / "index.json"
    return new_dir / "index.json"


def _load_metadata(root: Path) -> dict[str, Any]:
    index = _metadata_path(root)
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


def fingerprint_for(entry: Any) -> str:
    """Cheap stamp of a file's current shape, as ``size:lines``."""
    if entry is None:
        return ""
    return f"{getattr(entry, 'size', 0)}:{getattr(entry, 'lines', 0)}"


_MAX_ANNOTATE_ENTRIES = 200
_MAX_TAGS = 12


def annotate_metadata(root: Path, entries: dict[str, Any]) -> dict[str, Any]:
    """Merge ``entries`` into ``.navin/metadata/index.json`` and stamp fingerprints."""
    from navin.index import get_index

    if not entries:
        raise MetagraphError(400, "files must contain at least one entry")
    if len(entries) > _MAX_ANNOTATE_ENTRIES:
        raise MetagraphError(
            400,
            f"too many entries at once ({len(entries)} > {_MAX_ANNOTATE_ENTRIES}); "
            "annotate in batches",
        )

    index = get_index(root)
    index.ensure()
    known = set(index.all_files)

    merged = dict(_load_metadata(root))
    written: list[str] = []
    unknown: list[str] = []

    for raw_path, raw_meta in entries.items():
        rel = str(raw_path).replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        if rel not in known:
            unknown.append(str(raw_path))
            continue
        if not isinstance(raw_meta, dict):
            raise MetagraphError(400, f"{rel}: entry must be an object")

        entry: dict[str, Any] = dict(merged.get(rel) or {})
        role = raw_meta.get("role")
        if role is not None:
            if not isinstance(role, str) or not role.strip():
                raise MetagraphError(400, f"{rel}: role must be a non-empty string")
            entry["role"] = role.strip()[:300]
        kind = raw_meta.get("kind")
        if kind is not None:
            if kind not in _KINDS:
                raise MetagraphError(
                    400, f"{rel}: kind must be one of {', '.join(_KINDS)}"
                )
            entry["kind"] = kind
        tags = raw_meta.get("tags")
        if tags is not None:
            if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
                raise MetagraphError(400, f"{rel}: tags must be a list of strings")
            entry["tags"] = [t.strip() for t in tags if t.strip()][:_MAX_TAGS]
        deps = raw_meta.get("depends_on")
        if deps is not None:
            if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
                raise MetagraphError(400, f"{rel}: depends_on must be a list of strings")
            entry["depends_on"] = [
                d.replace("\\", "/") for d in deps if d.replace("\\", "/") in known
            ][:64]

        if "role" not in entry:
            raise MetagraphError(400, f"{rel}: a new entry needs a role")
        entry["fingerprint"] = fingerprint_for(index.entries.get(rel))
        merged[rel] = entry
        written.append(rel)

    if written:
        _write_metadata(root, merged)
        rebuild_and_notify(root)

    return {
        "written": sorted(written),
        "unknown": sorted(unknown),
        "total_annotated": len(merged),
        "total_files": len(known),
    }


def _write_metadata(root: Path, files: dict[str, Any]) -> None:
    """Atomically replace ``.navin/metadata/index.json``."""
    path = _metadata_path(root)
    payload = {"files": {rel: files[rel] for rel in sorted(files)}}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
        ) as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            temp = Path(handle.name)
        temp.replace(path)
    except OSError as exc:
        raise MetagraphError(500, f"could not write .navin/metadata/index.json: {exc}") from exc


def metagraph_payload(
    scope: WorkspaceScope,
    *,
    view: str = "files",
    aspect: float = 16 / 9,
) -> dict[str, Any]:
    root = scope.project_path
    if not root.is_dir():
        raise MetagraphError(404, "project directory not found")
    return build_metagraph(root, view=view, aspect=aspect)


def _bump_generation(root: Path) -> int:
    key = str(root.resolve(strict=False))
    with _cache_lock:
        _generation[key] = _generation.get(key, 0) + 1
        return _generation[key]


def _cache_key(root: Path, view: str) -> tuple[str, str]:
    return (str(root.resolve(strict=False)), view)


def _collect_snapshot(root: Path, *, max_nodes: int) -> dict[str, Any]:
    """Build the engine snapshot from the code index + .navin/metadata."""
    from navin.index import get_index

    index = get_index(root)
    index.ensure()

    rel_paths = list(index.all_files)
    rel_set = set(rel_paths)
    metadata = _load_metadata(root)

    edges: set[tuple[str, str]] = {
        (source, target) for source, target in index.edges()
    }

    files: list[dict[str, Any]] = []
    for rel in rel_paths:
        entry = index.entries.get(rel)
        try:
            size = entry.size if entry is not None else (root / rel).stat().st_size
        except OSError:
            size = 0
        file_row: dict[str, Any] = {
            "id": rel,
            "kind": _classify(rel),
            "size": int(size),
        }
        if entry is not None:
            if entry.symbols:
                file_row["symbols"] = len(entry.symbols)
            if entry.doc:
                file_row["role"] = entry.doc[:300]
                file_row["role_source"] = "auto"
        files.append(file_row)

    files_by_id = {f["id"]: f for f in files}
    for rel, meta in metadata.items():
        node = files_by_id.get(rel)
        if node is None or not isinstance(meta, dict):
            continue
        role = meta.get("role")
        if isinstance(role, str) and role.strip():
            node["role"] = role.strip()[:300]
            node["role_source"] = "manual"
            stamped = meta.get("fingerprint")
            if isinstance(stamped, str) and stamped:
                current = fingerprint_for(index.entries.get(rel))
                if current and current != stamped:
                    node["role_stale"] = True
        meta_kind = meta.get("kind")
        if isinstance(meta_kind, str) and meta_kind in _KINDS:
            node["kind"] = meta_kind
        deps = meta.get("depends_on")
        if isinstance(deps, list):
            for dep in deps[:64]:
                if isinstance(dep, str) and dep in rel_set and dep != rel:
                    edges.add((rel, dep))

    return {
        "project_path": str(root),
        "max_nodes": max_nodes,
        "files": files,
        "edges": [[s, t] for s, t in sorted(edges)],
        "discovery": index.stats.discovery if index.stats else "",
        "total_files": len(rel_paths),
        "symbols": index.symbol_count(),
        "has_metadata": bool(metadata),
    }


def _state_to_payload(state: dict[str, Any]) -> dict[str, Any]:
    """Normalize engine state into the HTTP / UI payload shape."""
    positions = state.get("positions") or {}
    payload: dict[str, Any] = {
        "project_path": state.get("project_path", ""),
        "generation": int(state.get("generation") or 0),
        "view": state.get("view") or "files",
        "nodes": state.get("nodes") or [],
        "edges": state.get("edges") or [],
        "kinds": state.get("kinds") or {},
        "has_metadata": bool(state.get("has_metadata")),
        "annotated": int(state.get("annotated") or 0),
        "annotated_manual": int(state.get("annotated_manual") or 0),
        "annotated_stale": int(state.get("annotated_stale") or 0),
        "discovery": state.get("discovery") or "",
        "truncated": bool(state.get("truncated")),
        "total_files": int(state.get("total_files") or 0),
        "symbols": int(state.get("symbols") or 0),
        "layout_width": float(state.get("layout_width") or 0),
        "layout_height": float(state.get("layout_height") or 0),
        "clusters": state.get("clusters") or [],
        "engine": state.get("engine") or "python",
    }
    if positions:
        payload["positions"] = positions
    return payload


def _python_aggregate_packages(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        key = node["id"].split("/", 1)[0] if "/" in node["id"] else "(root)"
        groups[key].append(node)

    package_of: dict[str, str] = {}
    clusters: list[dict[str, Any]] = []
    pkg_nodes: list[dict[str, Any]] = []
    for key in sorted(groups):
        members = groups[key]
        member_ids = sorted(m["id"] for m in members)
        for mid in member_ids:
            package_of[mid] = key
        kind_counts: dict[str, int] = defaultdict(int)
        for m in members:
            kind_counts[m["kind"]] += 1
        kind = max(kind_counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
        pkg_nodes.append(
            {
                "id": key,
                "kind": kind,
                "size": sum(int(m.get("size") or 0) for m in members),
                "role": f"{len(member_ids)} files",
                "role_source": "auto",
                "symbols": sum(int(m.get("symbols") or 0) for m in members) or None,
                "in_degree": 0,
                "out_degree": 0,
            }
        )
        if pkg_nodes[-1]["symbols"] is None:
            del pkg_nodes[-1]["symbols"]
        clusters.append(
            {
                "id": key,
                "label": key,
                "member_ids": member_ids,
                "kind": kind,
            }
        )

    pkg_edges_set: set[tuple[str, str]] = set()
    for edge in edges:
        ps = package_of.get(edge["source"])
        pt = package_of.get(edge["target"])
        if ps and pt and ps != pt:
            pkg_edges_set.add((ps, pt))
    in_degree: dict[str, int] = defaultdict(int)
    out_degree: dict[str, int] = defaultdict(int)
    for source, target in pkg_edges_set:
        out_degree[source] += 1
        in_degree[target] += 1
    for node in pkg_nodes:
        node["in_degree"] = in_degree[node["id"]]
        node["out_degree"] = out_degree[node["id"]]
    pkg_edges = [{"source": s, "target": t} for s, t in sorted(pkg_edges_set)]
    return pkg_nodes, pkg_edges, clusters


def _python_build(
    snapshot: dict[str, Any],
    *,
    view: str,
    generation: int,
) -> dict[str, Any]:
    """Pure-Python graph assembly (no layout - UI falls back to force-layout)."""
    files = list(snapshot.get("files") or [])
    edge_pairs = snapshot.get("edges") or []
    max_nodes = int(snapshot.get("max_nodes") or MAX_NODES)

    nodes_by_id = {f["id"]: dict(f) for f in files}
    edges: set[tuple[str, str]] = set()
    for pair in edge_pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        source, target = str(pair[0]), str(pair[1])
        if source != target and source in nodes_by_id and target in nodes_by_id:
            edges.add((source, target))

    in_degree: dict[str, int] = defaultdict(int)
    out_degree: dict[str, int] = defaultdict(int)
    for source, target in edges:
        out_degree[source] += 1
        in_degree[target] += 1
    for rel, node in nodes_by_id.items():
        node["in_degree"] = in_degree[rel]
        node["out_degree"] = out_degree[rel]
        node.setdefault("kind", "other")
        node.setdefault("size", 0)

    truncated = len(nodes_by_id) > max_nodes
    if truncated:
        ranked = sorted(
            nodes_by_id.values(),
            key=lambda n: (n["in_degree"] + n["out_degree"], n.get("symbols") or 0),
            reverse=True,
        )
        kept = {n["id"] for n in ranked[:max_nodes]}
        nodes_by_id = {k: v for k, v in nodes_by_id.items() if k in kept}
        edges = {e for e in edges if e[0] in kept and e[1] in kept}
        in_degree.clear()
        out_degree.clear()
        for source, target in edges:
            out_degree[source] += 1
            in_degree[target] += 1
        for rel, node in nodes_by_id.items():
            node["in_degree"] = in_degree[rel]
            node["out_degree"] = out_degree[rel]

    nodes = sorted(nodes_by_id.values(), key=lambda n: n["id"])
    edge_list = [{"source": s, "target": t} for s, t in sorted(edges)]
    clusters: list[dict[str, Any]] = []
    if view == "packages":
        nodes, edge_list, clusters = _python_aggregate_packages(nodes, edge_list)
        truncated = False

    kinds: dict[str, int] = defaultdict(int)
    annotated = annotated_manual = annotated_stale = 0
    for node in nodes:
        kinds[node["kind"]] += 1
        if node.get("role"):
            annotated += 1
        if node.get("role_source") == "manual":
            annotated_manual += 1
        if node.get("role_stale"):
            annotated_stale += 1

    return {
        "project_path": snapshot.get("project_path", ""),
        "generation": generation,
        "view": view,
        "nodes": nodes,
        "edges": edge_list,
        "kinds": dict(kinds),
        "has_metadata": bool(snapshot.get("has_metadata")),
        "annotated": annotated,
        "annotated_manual": annotated_manual,
        "annotated_stale": annotated_stale,
        "discovery": snapshot.get("discovery") or "",
        "truncated": truncated,
        "total_files": int(snapshot.get("total_files") or 0),
        "symbols": int(snapshot.get("symbols") or 0),
        "positions": {},
        "layout_width": 0,
        "layout_height": 0,
        "clusters": clusters,
        "engine": "python",
    }


def _python_diff(prev: dict[str, Any], nxt: dict[str, Any]) -> dict[str, Any]:
    prev_nodes = {n["id"]: n for n in prev.get("nodes") or []}
    next_nodes = {n["id"]: n for n in nxt.get("nodes") or []}
    added_nodes = [next_nodes[i] for i in sorted(next_nodes) if i not in prev_nodes]
    removed_nodes = sorted(i for i in prev_nodes if i not in next_nodes)
    updated_nodes = [
        next_nodes[i]
        for i in sorted(next_nodes)
        if i in prev_nodes and prev_nodes[i] != next_nodes[i]
    ]
    prev_edges = {
        (e["source"], e["target"]) for e in prev.get("edges") or []
    }
    next_edges = {
        (e["source"], e["target"]) for e in nxt.get("edges") or []
    }
    return {
        "added_nodes": added_nodes,
        "removed_nodes": removed_nodes,
        "updated_nodes": updated_nodes,
        "added_edges": [
            {"source": s, "target": t} for s, t in sorted(next_edges - prev_edges)
        ],
        "removed_edges": [
            {"source": s, "target": t} for s, t in sorted(prev_edges - next_edges)
        ],
        "generation": int(nxt.get("generation") or 0),
    }


def build_metagraph(
    root: Path,
    *,
    max_nodes: int = MAX_NODES,
    view: str = "files",
    aspect: float = 16 / 9,
    sticky: dict[str, dict[str, float]] | None = None,
    bump: bool = True,
    layout: bool = True,
) -> dict[str, Any]:
    """Return the metagraph payload (nodes, edges, kinds, optional positions).

    ``layout=False`` skips the force-directed placement. Only the Graph tab
    draws positions; the agent's per-turn orientation lines and the
    ``metagraph`` tool read degrees and kinds. Measured 2026-09-02 on this
    repository (3 843 files): snapshot 58 ms, build 21 ms, layout 814 ms.
    Paying the layout on every agent turn was most of the tool's cost.
    """
    view = "packages" if view == "packages" else "files"
    snapshot = _collect_snapshot(root, max_nodes=max_nodes)
    generation = _bump_generation(root) if bump else (
        _generation.get(str(root.resolve(strict=False)), 0) or 1
    )
    snapshot["generation"] = generation
    snapshot["view"] = view

    cache_key = _cache_key(root, view)
    with _cache_lock:
        cached = _state_cache.get(cache_key)
    cached_positions = cached.get("positions") if cached else None
    sticky_positions = sticky if sticky is not None else cached_positions

    core = native()
    state: dict[str, Any] | None = None
    if core is not None:
        try:
            built = json.loads(core.graph_build(json.dumps(snapshot)))
            if layout:
                options = json.dumps(
                    {
                        "area_per_node": 5200,
                        "label_allowance": 104,
                        "min_distance": 30,
                        "aspect": aspect,
                        "padding": 36,
                    }
                )
                sticky_json = json.dumps(sticky_positions) if sticky_positions else None
                state = json.loads(core.graph_layout(json.dumps(built), options, sticky_json))
            else:
                state = built
                # Keep the last drawn positions so the Graph tab's next build
                # still starts from where the user left the nodes.
                if cached_positions:
                    state["positions"] = cached_positions
            state["engine"] = "native"
        except Exception:
            state = None

    if state is None:
        state = _python_build(snapshot, view=view, generation=generation)

    with _cache_lock:
        _state_cache[cache_key] = state
    return _state_to_payload(state)


def query_metagraph(
    root: Path,
    op: str,
    *,
    view: str = "files",
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a graph query (hubs / path / impact / cluster / find)."""
    cache_key = _cache_key(root, view)
    with _cache_lock:
        state = _state_cache.get(cache_key)
    if state is None:
        # Queries read degrees and edges; the Graph tab lays out on its own.
        build_metagraph(root, view=view, layout=False)
        with _cache_lock:
            state = _state_cache.get(cache_key)
    if state is None:
        return {"error": "graph unavailable"}

    core = native()
    if core is not None:
        try:
            return json.loads(
                core.graph_query(json.dumps(state), op, json.dumps(args or {}))
            )
        except Exception:
            pass
    return _python_query(state, op, args or {})


def _python_query(state: dict[str, Any], op: str, args: dict[str, Any]) -> dict[str, Any]:
    nodes = state.get("nodes") or []
    edges = state.get("edges") or []
    if op == "hubs":
        limit = int(args.get("limit") or 15)
        hubs = sorted(
            (n for n in nodes if n["in_degree"] + n["out_degree"] > 0),
            key=lambda n: n["in_degree"] + n["out_degree"],
            reverse=True,
        )[:limit]
        return {
            "hubs": [
                {
                    "id": n["id"],
                    "kind": n["kind"],
                    "in_degree": n["in_degree"],
                    "out_degree": n["out_degree"],
                    "role": n.get("role"),
                }
                for n in hubs
            ]
        }
    if op == "path":
        source = str(args.get("from") or args.get("source") or "")
        target = str(args.get("to") or args.get("target") or "")
        if not source or not target:
            return {"path": [], "error": "from and to are required"}
        adj: dict[str, list[str]] = defaultdict(list)
        for edge in edges:
            adj[edge["source"]].append(edge["target"])
        prev: dict[str, str | None] = {source: None}
        queue: deque[str] = deque([source])
        found = False
        while queue:
            current = queue.popleft()
            if current == target:
                found = True
                break
            for nxt in adj.get(current, []):
                if nxt in prev:
                    continue
                prev[nxt] = current
                queue.append(nxt)
        if not found:
            return {"path": [], "from": source, "to": target, "found": False}
        path = [target]
        cur: str | None = target
        while cur != source:
            cur = prev.get(cur)  # type: ignore[assignment]
            if cur is None:
                break
            path.append(cur)
        path.reverse()
        return {
            "path": path,
            "from": source,
            "to": target,
            "found": True,
            "length": max(0, len(path) - 1),
        }
    if op == "impact":
        path = str(args.get("path") or args.get("id") or "")
        if not path:
            return {"dependents": [], "error": "path is required"}
        radius = args.get("radius")
        max_depth = int(radius) if radius is not None else 10_000
        reverse: dict[str, list[str]] = defaultdict(list)
        for edge in edges:
            reverse[edge["target"]].append(edge["source"])
        seen = {path}
        order: list[str] = []
        queue_d: deque[tuple[str, int]] = deque([(path, 0)])
        while queue_d:
            current, depth = queue_d.popleft()
            if depth > 0:
                order.append(current)
            if depth >= max_depth:
                continue
            for nxt in reverse.get(current, []):
                if nxt in seen:
                    continue
                seen.add(nxt)
                queue_d.append((nxt, depth + 1))
        return {
            "path": path,
            "dependents": order,
            "count": len(order),
            "radius": radius,
        }
    if op == "cluster":
        cid = str(args.get("id") or args.get("package") or "")
        for cluster in state.get("clusters") or []:
            if cluster.get("id") == cid:
                return {
                    "id": cluster["id"],
                    "label": cluster.get("label", cid),
                    "kind": cluster.get("kind"),
                    "member_ids": cluster.get("member_ids") or [],
                    "count": len(cluster.get("member_ids") or []),
                }
        prefix = "" if cid == "(root)" else f"{cid}/"
        members = [
            n["id"]
            for n in nodes
            if (cid == "(root)" and "/" not in n["id"])
            or n["id"] == cid
            or (prefix and n["id"].startswith(prefix))
        ]
        return {
            "id": cid,
            "label": cid,
            "member_ids": members,
            "count": len(members),
            "found": bool(members),
        }
    if op == "find":
        query = str(args.get("query") or "").lower()
        kind = args.get("kind")
        limit = int(args.get("limit") or 40)
        matches = []
        for node in nodes:
            if kind and node["kind"] != kind:
                continue
            if query:
                hay = node["id"].lower()
                if node.get("role"):
                    hay += " " + str(node["role"]).lower()
                if query not in hay:
                    continue
            elif not kind:
                continue
            matches.append(node)
        matches.sort(key=lambda n: n["in_degree"] + n["out_degree"], reverse=True)
        shown = matches[:limit]
        return {
            "matches": [
                {
                    "id": n["id"],
                    "kind": n["kind"],
                    "role": n.get("role"),
                    "in_degree": n["in_degree"],
                    "out_degree": n["out_degree"],
                }
                for n in shown
            ],
            "count": len(shown),
        }
    return {"error": f"unknown graph query op: {op}"}


def rebuild_and_notify(
    root: Path,
    *,
    view: str = "files",
    bus: Any = None,
) -> dict[str, Any]:
    """Rebuild the graph, diff against the previous cache, and broadcast."""
    from navin.webui.metagraph_notify import publish_metagraph_update

    root = Path(root).expanduser().resolve(strict=False)
    cache_key = _cache_key(root, view)
    with _cache_lock:
        previous = _state_cache.get(cache_key)

    payload = build_metagraph(root, view=view, bump=True)
    with _cache_lock:
        current = _state_cache.get(cache_key)

    diff: dict[str, Any] | None = None
    if previous is not None and current is not None:
        core = native()
        if core is not None:
            try:
                diff = json.loads(
                    core.graph_diff(json.dumps(previous), json.dumps(current))
                )
            except Exception:
                diff = _python_diff(previous, current)
        else:
            diff = _python_diff(previous, current)
        # Attach sticky positions for clients applying the diff.
        if current.get("positions"):
            if diff is None:
                diff = {}
            diff["positions"] = current["positions"]
            diff["layout_width"] = current.get("layout_width", 0)
            diff["layout_height"] = current.get("layout_height", 0)

    publish_metagraph_update(
        bus,
        str(root),
        generation=int(payload.get("generation") or 0),
        diff=diff,
        view=view,
    )
    return payload
