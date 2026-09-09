# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Metagraph tool: query the project file map and dependency graph.

Gives the agent direct access to the same graph the Dev workbench renders in
the Graph tab: files as nodes (classified front/back/sql/config/test/docs),
imports as edges, enriched with ``.navin/metadata/index.json`` roles maintained via
the ``project-metadata`` skill. Lets the agent answer "which files handle X,
what depends on Y" without grepping the whole tree.
"""

from __future__ import annotations

import asyncio
import difflib
from pathlib import Path
from typing import Any

from navin.agent.tools.base import ToolResult
from navin.agent.tools.context import RequestContext
from navin.agent.tools.filesystem import _FsTool
from navin.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines
from navin.utils.path import normalize_relative_path

_FIND_LIMIT = 40
_HUB_LIMIT = 15
# How many stale paths to name before switching to a count. Naming a few is what
# makes the line actionable; naming eighty would cost more than the map is worth.
_STALE_SAMPLE = 8
# Below this size the model can orient itself by listing the tree; injecting a
# map every turn would cost tokens without telling it anything new. Above it,
# a standing overview is what keeps long missions from re-exploring the repo
# from scratch after every context compaction.
_MAP_MIN_FILES = 40
_MAP_HUBS = 6


def _staleness_lines(root: Path) -> list[str]:
    """Runtime lines describing what ``.navin/metadata`` no longer covers."""
    from navin.webui.metagraph import build_metagraph

    return _staleness_lines_from_graph(build_metagraph(root, layout=False))


def _staleness_lines_from_graph(graph: dict[str, Any]) -> list[str]:
    if not graph.get("has_metadata"):
        return []

    stale = [node["id"] for node in graph["nodes"] if node.get("role_stale")]
    described = {
        node["id"] for node in graph["nodes"] if node.get("role_source") == "manual"
    }
    # Assets and generated output are not what a project map is for; demanding a
    # role for every png would make the debt permanent and the line ignorable.
    missing = [
        node["id"]
        for node in graph["nodes"]
        if node["id"] not in described and node["kind"] in {"front", "back", "sql"}
    ]
    if not stale and not missing:
        return []

    lines = [".navin/metadata map upkeep (use metagraph action=annotate):"]
    if stale:
        lines.append(
            f"- {len(stale)} recorded role(s) describe a file that changed since: "
            + ", ".join(stale[:_STALE_SAMPLE])
            + (" ..." if len(stale) > _STALE_SAMPLE else "")
        )
    if missing:
        lines.append(
            f"- {len(missing)} code file(s) have no recorded role"
            + (
                ", including " + ", ".join(missing[:_STALE_SAMPLE])
                if len(missing) <= _STALE_SAMPLE
                else ""
            )
        )
    lines.append(
        "- Annotate the files you touch this turn; do not stop your task to "
        "annotate the backlog."
    )
    return lines


def _repo_map_lines(graph: dict[str, Any]) -> list[str]:
    """A standing compact orientation map, for repos big enough to need one."""
    nodes: list[dict[str, Any]] = graph.get("nodes") or []
    if len(nodes) < _MAP_MIN_FILES:
        return []
    kinds = ", ".join(
        f"{name}={count}" for name, count in sorted((graph.get("kinds") or {}).items())
    )
    lines = [
        f"Project map ({len(nodes)} files"
        + (", truncated" if graph.get("truncated") else "")
        + (f"; {kinds}" if kinds else "")
        + "):",
    ]
    hubs = sorted(
        nodes, key=lambda n: n["in_degree"] + n["out_degree"], reverse=True
    )
    hubs = [h for h in hubs if h["in_degree"] + h["out_degree"] > 0][:_MAP_HUBS]
    for hub in hubs:
        role = hub.get("role")
        lines.append(
            f"- {hub['id']} [{hub['kind']}]" + (f": {role}" if role else "")
        )
    lines.append(
        "- Use metagraph (overview/find/impact) or code_index to navigate "
        "instead of re-exploring the tree."
    )
    return lines


def _runtime_context_lines(root: Path) -> list[str]:
    """Everything the metagraph tells the model unprompted, one graph build."""
    from navin.webui.metagraph import build_metagraph

    # No positions: these lines only read degrees and kinds, and this runs
    # before every model call.
    graph = build_metagraph(root, layout=False)
    lines = _repo_map_lines(graph)
    upkeep = _staleness_lines_from_graph(graph)
    if upkeep:
        if lines:
            lines.append("")
        lines.extend(upkeep)
    return lines


def _format_node(node: dict[str, Any]) -> str:
    parts = [f"{node['id']} [{node['kind']}]"]
    role = node.get("role")
    if role:
        parts.append(f"- {role}")
    return " ".join(parts)


class MetagraphTool(_FsTool):
    """Query the project metagraph: file roles, kinds, and dependency edges."""

    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "metagraph"

    @property
    def description(self) -> str:
        return (
            "Query the project map at FILE level (files as nodes, imports as "
            "edges, roles from docstrings): overview, file (role + "
            "dependencies/dependents), find, hubs, path (dependency chain "
            "A to B), impact (transitive dependents), cluster. Use it to "
            "orient in an unfamiliar project; use code_index for "
            "SYMBOL-level answers. 'annotate' records what a file is for - "
            "after creating a file or changing its responsibility, annotate "
            "it in the same turn."
        )

    @property
    def read_only(self) -> bool:
        """Every action but ``annotate`` is a query; see call_concurrency_safe."""
        return True

    def call_concurrency_safe(self, arguments: Any) -> bool:
        """Keep an annotating call out of a parallel batch.

        The queries stay batchable. ``annotate`` reads ``.navin/metadata/index.json``,
        merges and writes it back, so two of them running side by side would race
        and one set of roles would vanish.
        """
        if isinstance(arguments, dict) and arguments.get("action") == "annotate":
            return False
        return super().call_concurrency_safe(arguments)

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "overview",
                        "file",
                        "find",
                        "annotate",
                        "hubs",
                        "path",
                        "impact",
                        "cluster",
                    ],
                    "description": (
                        "overview: kinds, counts, main hubs, stale roles; "
                        "file: role + dependencies + dependents of one file; "
                        "find: search nodes by query/kind; "
                        "hubs: most connected files; "
                        "path: shortest dependency chain from 'from' to 'to'; "
                        "impact: files that depend on 'path' (optional radius); "
                        "cluster: members of a package/folder id; "
                        "annotate: record the role of one or more files"
                    ),
                },
                "files": {
                    "type": "object",
                    "description": (
                        "For action=annotate. Maps project-relative paths to "
                        '{"role": "one sentence on what this file is for", '
                        '"kind": "front|back|sql|config|test|docs|asset|other", '
                        '"tags": ["entry"], "depends_on": ["path/it/needs.py"]}. '
                        "'role' is required on a first annotation; the other keys "
                        "are optional and merge into any existing entry."
                    ),
                    "additionalProperties": {"type": "object"},
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Project-relative file path (for action=file / impact) "
                        "or package id (for action=cluster)"
                    ),
                },
                "from": {
                    "type": "string",
                    "description": "Source path for action=path",
                },
                "to": {
                    "type": "string",
                    "description": "Target path for action=path",
                },
                "radius": {
                    "type": "integer",
                    "description": "Max hop depth for action=impact (optional)",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Case-insensitive search over paths, roles, and tags "
                        "(for action=find)"
                    ),
                },
                "kind": {
                    "type": "string",
                    "enum": ["front", "back", "sql", "config", "test", "docs", "asset", "other"],
                    "description": "Optional kind filter (for action=find)",
                },
            },
            "required": ["action"],
        }

    def runtime_context_provider(self):
        return self._provide_runtime_context

    async def _provide_runtime_context(
        self,
        request: RequestContext,
    ) -> RuntimeContextBlock | None:
        """Standing orientation and upkeep lines, every turn, unprompted.

        Two things, one graph build:

        - A compact repo map (kind counts + main hubs) on projects large enough
          that re-exploring the tree after each context compaction costs more
          than the standing lines do. Small projects stay silent.
        - Upkeep debt for ``.navin/metadata``: the rule lived only in the
          project-metadata skill, so the model read it during ``/atlas`` and
          never again - which is why the index only moved when a human pressed
          the button. Silent when the map is clean or the project never opted
          into ``.navin/metadata``.
        """
        root = request.workspace or self._workspace
        if root is None:
            return None
        try:
            lines = await asyncio.to_thread(_runtime_context_lines, Path(root))
        except BaseException:
            # Orientation metadata is never worth failing a turn over.
            # Include BaseException: a Rust PanicException from navin_core
            # would otherwise abort the whole agent turn before any reply.
            return None
        content = wrap_runtime_context_lines(lines)
        if not content:
            return None
        return RuntimeContextBlock(source="metagraph", content=content)

    def _project_root(self) -> Path:
        root = self._display_workspace() or self._workspace
        if root is None:
            raise ValueError("no workspace configured")
        return Path(root).expanduser().resolve(strict=False)

    async def execute(
        self,
        action: str,
        path: str | None = None,
        query: str | None = None,
        kind: str | None = None,
        files: dict[str, Any] | None = None,
        radius: int | None = None,
        **kwargs: Any,
    ) -> str:
        from navin.webui.metagraph import (
            MetagraphError,
            annotate_metadata,
            build_metagraph,
            query_metagraph,
        )

        try:
            root = self._project_root()
            if not root.is_dir():
                return ToolResult.error(f"Error: project root not found: {root}")
        except Exception as e:
            return ToolResult.error(f"Error resolving project root: {e}")

        if action == "annotate":
            if not isinstance(files, dict) or not files:
                return ToolResult.error(
                    "Error: action=annotate requires 'files' mapping paths to "
                    '{"role": ...}'
                )
            try:
                result = await asyncio.to_thread(annotate_metadata, root, files)
            except MetagraphError as e:
                return ToolResult.error(f"Error: {e.message}")
            except Exception as e:
                return ToolResult.error(f"Error writing .navin/metadata/index.json: {e}")
            lines = [
                f"Annotated {len(result['written'])} file(s) in .navin/metadata/index.json "
                f"({result['total_annotated']}/{result['total_files']} files now described)."
            ]
            if result["unknown"]:
                lines.append(
                    "Not in the index, so not recorded: "
                    + ", ".join(result["unknown"][:10])
                    + ". Check the path, or the file may be gitignored."
                )
            return "\n".join(lines)

        if action in {"hubs", "path", "impact", "cluster"}:
            args: dict[str, Any] = {}
            if action == "hubs":
                args["limit"] = _HUB_LIMIT
            elif action == "path":
                source = kwargs.get("from") or kwargs.get("source")
                target = kwargs.get("to") or kwargs.get("target")
                if not source or not target:
                    return ToolResult.error(
                        "Error: action=path requires 'from' and 'to'"
                    )
                args["from"] = normalize_relative_path(str(source)) or str(source)
                args["to"] = normalize_relative_path(str(target)) or str(target)
            elif action == "impact":
                cleaned = normalize_relative_path(path)
                if not cleaned:
                    return ToolResult.error("Error: action=impact requires 'path'")
                args["path"] = cleaned
                if radius is not None:
                    args["radius"] = int(radius)
            else:
                cleaned = normalize_relative_path(path) if path else None
                cid = cleaned or (path or "").strip()
                if not cid:
                    return ToolResult.error("Error: action=cluster requires 'path'")
                args["id"] = cid
            try:
                result = await asyncio.to_thread(query_metagraph, root, action, args=args)
            except Exception as e:
                return ToolResult.error(f"Error querying metagraph: {e}")
            if action == "hubs":
                hubs = result.get("hubs") or []
                if not hubs:
                    return "No connected hubs in the metagraph."
                lines = ["Main hubs (most connected):"]
                for hub in hubs:
                    lines.append(
                        f"  {hub.get('id')} [{hub.get('kind')}] "
                        f"(in={hub.get('in_degree')}, out={hub.get('out_degree')})"
                        + (f" - {hub['role']}" if hub.get("role") else "")
                    )
                return "\n".join(lines)
            if action == "path":
                if not result.get("found"):
                    return (
                        f"No dependency path from {args.get('from')} "
                        f"to {args.get('to')}."
                    )
                chain = result.get("path") or []
                return (
                    f"Path ({result.get('length', 0)} hop(s)):\n"
                    + "\n".join(f"  {step}" for step in chain)
                )
            if action == "impact":
                deps = result.get("dependents") or []
                if not deps:
                    return f"Nothing depends on {args.get('path')}."
                lines = [
                    f"Impact of {args.get('path')}: {len(deps)} dependent file(s)"
                ]
                lines.extend(f"  {d}" for d in deps[:40])
                if len(deps) > 40:
                    lines.append(f"  … {len(deps) - 40} more")
                return "\n".join(lines)
            members = result.get("member_ids") or []
            if not members:
                return f"No cluster members for {args.get('id')}."
            lines = [
                f"Cluster {result.get('id')} "
                f"({result.get('count', len(members))} files):"
            ]
            lines.extend(f"  {m}" for m in members[:60])
            if len(members) > 60:
                lines.append(f"  … {len(members) - 60} more")
            return "\n".join(lines)

        try:
            graph = await asyncio.to_thread(build_metagraph, root, layout=False)
        except Exception as e:
            return ToolResult.error(f"Error building metagraph: {e}")

        nodes: list[dict[str, Any]] = graph["nodes"]
        edges: list[dict[str, str]] = graph["edges"]
        by_id = {node["id"]: node for node in nodes}

        if action == "overview":
            # State the root and how files were found. "7/7 files, 100% coverage"
            # once described an agent workspace whose .gitignore hid the 27-file
            # project next to it, and nothing in the report said so.
            discovery = graph.get("discovery") or "unknown"
            method = {
                "git": "git ls-files, so .gitignore is honored",
                "walk": "directory walk, so gitignored files are included",
            }.get(discovery, discovery)
            lines = [
                f"Project root: {graph['project_path']}",
                f"Discovery: {method}",
                f"Files: {len(nodes)}" + (" (truncated)" if graph["truncated"] else ""),
                f"Edges: {len(edges)}",
                "Kinds: " + ", ".join(
                    f"{name}={count}" for name, count in sorted(graph["kinds"].items())
                ),
                f"Roles: {graph['annotated']} files described "
                f"({graph.get('annotated_manual', 0)} curated in .navin/metadata/index.json, "
                f"the rest derived from docstrings and leading comments)",
            ]
            stale = graph.get("annotated_stale", 0)
            if stale:
                lines.append(
                    f"Stale: {stale} curated role(s) describe a file that changed "
                    "since; re-annotate them with action=annotate"
                )
            hubs = sorted(
                nodes, key=lambda n: n["in_degree"] + n["out_degree"], reverse=True
            )[:_HUB_LIMIT]
            hubs = [h for h in hubs if h["in_degree"] + h["out_degree"] > 0]
            if hubs:
                lines.append("")
                lines.append("Main hubs (most connected):")
                for hub in hubs:
                    lines.append(
                        f"  {_format_node(hub)} "
                        f"(in={hub['in_degree']}, out={hub['out_degree']})"
                    )
            return "\n".join(lines)

        if action == "file":
            cleaned = normalize_relative_path(path)
            if not cleaned:
                return ToolResult.error("Error: action=file requires 'path'")
            node = by_id.get(cleaned)
            if node is None:
                # Suffix match rescue: "App.tsx" → "webui/src/App.tsx".
                suffix_matches = [
                    n for n in nodes if n["id"].endswith("/" + cleaned) or n["id"] == cleaned
                ]
                if len(suffix_matches) == 1:
                    node = suffix_matches[0]
                elif suffix_matches:
                    listing = "\n".join(f"  {n['id']}" for n in suffix_matches[:10])
                    return f"Ambiguous path '{cleaned}' - candidates:\n{listing}"
                else:
                    close = difflib.get_close_matches(
                        cleaned, [n["id"] for n in nodes], n=3, cutoff=0.6,
                    )
                    parts = [f"Error: file not in metagraph: {cleaned}"]
                    if close:
                        parts.append("Did you mean: " + ", ".join(close) + "?")
                    elif (root / cleaned).is_file():
                        parts.append(
                            "The file exists but carries no dependency edges "
                            "(it may be ignored by git or of an unindexed type); "
                            "read it directly instead."
                        )
                    return ToolResult.error("\n".join(parts))
            file_id = node["id"]
            deps = sorted(e["target"] for e in edges if e["source"] == file_id)
            dependents = sorted(e["source"] for e in edges if e["target"] == file_id)
            lines = [_format_node(node), f"size: {node['size']} bytes"]
            lines.append(f"depends on ({len(deps)}):")
            if deps:
                lines.extend(f"  {d}" for d in deps)
            else:
                lines.append("  (none)")
            lines.append(f"depended on by ({len(dependents)}):")
            if dependents:
                lines.extend(f"  {d}" for d in dependents)
            else:
                lines.append("  (none)")
            return "\n".join(lines)

        if action == "find":
            needle = (query or "").strip().lower()
            if not needle and not kind:
                return ToolResult.error(
                    "Error: action=find requires 'query' and/or 'kind'"
                )
            matches: list[dict[str, Any]] = []
            for node in nodes:
                if kind and node["kind"] != kind:
                    continue
                if needle:
                    haystack = node["id"].lower()
                    role = node.get("role")
                    if role:
                        haystack += " " + role.lower()
                    terms = needle.split()
                    if not all(term in haystack for term in terms):
                        continue
                matches.append(node)
            if not matches:
                return "No matching files in the metagraph."
            # Most connected first: hubs are usually the interesting answer.
            matches.sort(
                key=lambda n: n["in_degree"] + n["out_degree"], reverse=True
            )
            shown = matches[:_FIND_LIMIT]
            lines = [f"{len(matches)} matching files:"]
            lines.extend(f"  {_format_node(node)}" for node in shown)
            if len(matches) > len(shown):
                lines.append(f"  … {len(matches) - len(shown)} more (refine the query)")
            return "\n".join(lines)

        return self.unknown_action(action)
