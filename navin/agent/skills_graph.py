# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Skills graph: nodes are installed skills, edges are their relations.

The system prompt only lists skill names, so the model picks playbooks
blind. This module builds a small graph over the catalog so the agent can
ask "which skills fit this task and how do they connect":

- explicit relations declared in skill frontmatter metadata
  (``navin.related`` / ``navin.tags``);
- implicit edges from keyword overlap between name + description
  (overlap coefficient above a threshold).

``SkillsGraph.suggest(query)`` ranks skills by text match (reusing
``SkillsLoader.search_skills``) then boosts the neighbours of the top hits,
so a skill that never mentions "pdf" but is explicitly related to the
pdf-report skill still surfaces when it should.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Keyword-overlap edges: two skills sharing at least this fraction of the
# smaller token set (overlap coefficient) are related even without metadata.
_MIN_OVERLAP = 0.5
# A neighbour inherits this fraction of the seed skill's text score.
_NEIGHBOUR_BOOST = 0.5
_MAX_NEIGHBOURS_PER_NODE = 6


@dataclass
class SkillNode:
    name: str
    description: str = ""
    source: str = ""
    available: bool = True
    tags: list[str] = field(default_factory=list)


@dataclass
class SkillEdge:
    src: str
    dst: str
    kind: str  # "explicit" (frontmatter) or "keyword" (overlap)
    weight: float


class SkillsGraph:
    """Undirected relation graph over the installed skill catalog."""

    def __init__(
        self,
        nodes: dict[str, SkillNode],
        edges: list[SkillEdge],
    ) -> None:
        self.nodes = nodes
        self.edges = edges
        self._adj: dict[str, list[SkillEdge]] = {name: [] for name in nodes}
        for edge in edges:
            self._adj.setdefault(edge.src, []).append(edge)
            self._adj.setdefault(edge.dst, []).append(
                SkillEdge(src=edge.dst, dst=edge.src, kind=edge.kind, weight=edge.weight)
            )

    def related(self, name: str, *, limit: int = 5) -> list[SkillEdge]:
        """Direct neighbours of a skill, strongest relation first."""
        edges = sorted(
            self._adj.get(name, []),
            key=lambda e: (-e.weight, e.kind != "explicit", e.dst),
        )
        return edges[:limit]

    def suggest(self, query: str, *, limit: int = 5) -> list[dict]:
        """Best skills for a free-text task, boosted by graph relations.

        Returns rows shaped like ``SkillsLoader.search_skills`` plus a
        ``via`` field naming the related skill that boosted the row (empty
        when the skill matched on its own text).
        """
        loader = self._loader
        seeds = loader.search_skills(query, limit=limit) if loader else []
        if not seeds:
            return []
        scores: dict[str, float] = {}
        via: dict[str, str] = {}
        for rank, row in enumerate(seeds):
            base = float(len(seeds) - rank)
            scores[row["name"]] = scores.get(row["name"], 0.0) + base
            for edge in self.related(row["name"], limit=3):
                if edge.dst in scores:
                    continue
                gain = base * _NEIGHBOUR_BOOST * edge.weight
                if gain <= 0:
                    continue
                if gain > scores.get(edge.dst, 0.0):
                    scores[edge.dst] = gain
                    via[edge.dst] = edge.src
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        rows: list[dict] = []
        for name, _score in ranked[:limit]:
            node = self.nodes.get(name)
            row = {
                "name": name,
                "description": node.description if node else "",
                "source": node.source if node else "",
                "available": node.available if node else True,
                "missing": "",
                "via": via.get(name, ""),
            }
            if loader is not None:
                meta = loader._get_skill_meta(name)
                row["available"] = loader._check_requirements(meta)
                row["missing"] = loader._get_missing_requirements(meta)
            rows.append(row)
        return rows


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) >= 3}


def _explicit_relations(meta: dict) -> list[str]:
    relations: list[str] = []
    for key in ("related", "skills", "pairs_with"):
        value = meta.get(key)
        if isinstance(value, str):
            relations.extend(p.strip() for p in value.split(",") if p.strip())
        elif isinstance(value, (list, tuple)):
            relations.extend(v.strip() for v in value if isinstance(v, str) and v.strip())
    return relations


def build_skills_graph(loader) -> SkillsGraph:
    """Build the graph from a SkillsLoader (explicit + keyword edges)."""
    nodes: dict[str, SkillNode] = {}
    token_sets: dict[str, set[str]] = {}
    token_df: dict[str, int] = {}
    for entry in loader.list_skills(filter_unavailable=False):
        name = entry["name"]
        meta = loader._get_skill_meta(name)
        desc = loader._get_skill_description(name)
        tags = [t for t in meta.get("tags", []) if isinstance(t, str)]
        nodes[name] = SkillNode(
            name=name,
            description=desc,
            source=entry.get("source", ""),
            available=loader._check_requirements(meta),
            tags=tags,
        )
        token_sets[name] = toks = _tokens(f"{name} {desc} {' '.join(tags)}")
        for token in toks:
            token_df[token] = token_df.get(token, 0) + 1

    edges: list[SkillEdge] = []
    seen: set[tuple[str, str]] = set()
    names = sorted(nodes)
    for name in names:
        meta = loader._get_skill_meta(name)
        for other in _explicit_relations(meta):
            if other in nodes and other != name and (name, other) not in seen:
                seen.add((name, other))
                seen.add((other, name))
                edges.append(SkillEdge(src=name, dst=other, kind="explicit", weight=1.0))
    for i, a in enumerate(names):
        ta = token_sets[a]
        if not ta:
            continue
        for b in names[i + 1:]:
            tb = token_sets[b]
            if not tb:
                continue
            inter = ta & tb
            if not inter:
                continue
            overlap = len(inter) / min(len(ta), len(tb))
            # A rare shared token (appears in at most 2 skills) is a strong
            # relation signal on its own: "pdf" links the pdf skills even
            # when their descriptions dilute the overlap coefficient.
            rare_shared = any(token_df.get(t, 0) <= 2 for t in inter)
            if overlap >= _MIN_OVERLAP or rare_shared:
                edges.append(SkillEdge(src=a, dst=b, kind="keyword", weight=round(max(overlap, 0.5 if rare_shared else 0.0), 3)))
    # Keep only the strongest keyword edges per node so the graph stays small.
    by_node: dict[str, list[SkillEdge]] = {}
    for edge in edges:
        if edge.kind != "explicit":
            by_node.setdefault(edge.src, []).append(edge)
            by_node.setdefault(edge.dst, []).append(edge)
    kept: set[tuple[str, str, float, str]] = set()
    for name, incident in by_node.items():
        keyword = sorted(
            (e for e in incident if e.kind == "keyword"),
            key=lambda e: (-e.weight, e.dst if e.src == name else e.src),
        )
        for e in keyword[:_MAX_NEIGHBOURS_PER_NODE]:
            kept.add((e.src, e.dst, e.weight, e.kind))
    final = [e for e in edges if e.kind == "explicit" or (e.src, e.dst, e.weight, e.kind) in kept]
    graph = SkillsGraph(nodes, final)
    graph._loader = loader  # noqa: SLF001 - suggest() reuses the loader ranking
    return graph
