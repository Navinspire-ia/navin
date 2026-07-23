"""Metagraph tool: query the project file map and dependency graph.

Gives the agent direct access to the same graph the Dev workbench renders in
the Graph tab: files as nodes (classified front/back/sql/config/test/docs),
imports as edges, enriched with ``.metadata/index.json`` roles maintained via
the ``project-metadata`` skill. Lets the agent answer "which files handle X,
what depends on Y" without grepping the whole tree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.agent.tools.base import ToolResult
from navin.agent.tools.filesystem import _FsTool

_FIND_LIMIT = 40
_HUB_LIMIT = 15


def _format_node(node: dict[str, Any]) -> str:
    parts = [f"{node['id']} [{node['kind']}]"]
    role = node.get("role")
    if role:
        parts.append(f"— {role}")
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
            "Query the project metagraph (files as nodes, imports/dependencies "
            "as edges, roles from .metadata/index.json). Prefer this over grep "
            "to locate files and understand relations: 'overview' summarizes "
            "the project, 'file' shows one file's role, dependencies, and "
            "dependents, 'find' searches files by path fragment, role text, "
            "or kind (front/back/sql/config/test/docs)."
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
                    "enum": ["overview", "file", "find"],
                    "description": (
                        "overview: kinds, counts, main hubs; "
                        "file: role + dependencies + dependents of one file; "
                        "find: search nodes by query/kind"
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "Project-relative file path (for action=file)",
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
        **kwargs: Any,
    ) -> str:
        from navin.webui.metagraph import build_metagraph

        try:
            root = self._project_root()
            if not root.is_dir():
                return ToolResult.error(f"Error: project root not found: {root}")
            graph = build_metagraph(root)
        except Exception as e:
            return ToolResult.error(f"Error building metagraph: {e}")

        nodes: list[dict[str, Any]] = graph["nodes"]
        edges: list[dict[str, str]] = graph["edges"]
        by_id = {node["id"]: node for node in nodes}

        if action == "overview":
            lines = [
                f"Project: {graph['project_path']}",
                f"Files: {len(nodes)}" + (" (truncated)" if graph["truncated"] else ""),
                f"Edges: {len(edges)}",
                "Kinds: " + ", ".join(
                    f"{name}={count}" for name, count in sorted(graph["kinds"].items())
                ),
                (
                    f".metadata: {graph['annotated']} annotated files"
                    if graph["has_metadata"]
                    else ".metadata: absent — run /atlas or use the project-metadata "
                    "skill to build it"
                ),
            ]
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
            cleaned = (path or "").strip().lstrip("./")
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
                    return f"Ambiguous path '{cleaned}' — candidates:\n{listing}"
                else:
                    return ToolResult.error(
                        f"Error: file not in metagraph: {cleaned}"
                    )
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

        return ToolResult.error(f"Error: unknown action: {action}")
