# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the skills graph (navin.agent.skills_graph)."""

from __future__ import annotations

import unittest

from navin.agent.skills_graph import SkillEdge, SkillNode, SkillsGraph, build_skills_graph


class _FakeLoader:
    """Minimal SkillsLoader surface used by build_skills_graph."""

    def __init__(self, entries, metas, descriptions):
        self._entries = entries
        self._meta = metas
        self._descriptions = descriptions

    def list_skills(self, filter_unavailable: bool = True):
        return self._entries

    def _get_skill_meta(self, name):
        return self._meta.get(name, {})

    def _get_skill_description(self, name):
        return self._descriptions.get(name, "")

    def _check_requirements(self, meta):
        return True

    def _get_missing_requirements(self, meta):
        return ""

    def search_skills(self, query, *, limit=10):
        rows = [
            {"name": name, "description": self._descriptions.get(name, ""), "source": "builtin"}
            for name in self._descriptions
            if any(w in name or w in self._descriptions[name] for w in query.lower().split())
        ]
        return rows[:limit]


class SkillsGraphTests(unittest.TestCase):
    def _loader(self):
        entries = [
            {"name": "pdf-generator", "source": "builtin"},
            {"name": "pdf-ocr-extractor", "source": "builtin"},
            {"name": "invoice-reader", "source": "builtin"},
            {"name": "docker-operator", "source": "builtin"},
        ]
        descriptions = {
            "pdf-generator": "Generate PDF documents from HTML layouts",
            "pdf-ocr-extractor": "Extract text from scanned PDF documents",
            "invoice-reader": "Read invoices and receipts from files",
            "docker-operator": "Operate docker containers and compose stacks",
        }
        metas = {
            # Shape returned by SkillsLoader._get_skill_meta: the parsed
            # navin payload, not the raw frontmatter.
            "invoice-reader": {"related": ["pdf-ocr-extractor"]},
        }
        return _FakeLoader(entries, metas, descriptions)

    def test_explicit_metadata_edges_are_kept(self) -> None:
        graph = build_skills_graph(self._loader())
        pairs = {(e.src, e.dst) for e in graph.edges if e.kind == "explicit"}
        self.assertIn(("invoice-reader", "pdf-ocr-extractor"), pairs)

    def test_keyword_overlap_creates_edges(self) -> None:
        graph = build_skills_graph(self._loader())
        neighbours = {e.dst for e in graph.related("pdf-generator", limit=10)}
        self.assertIn("pdf-ocr-extractor", neighbours)
        self.assertNotIn("docker-operator", neighbours)

    def test_related_ranks_explicit_first(self) -> None:
        graph = build_skills_graph(self._loader())
        edges = graph.related("pdf-ocr-extractor", limit=10)
        self.assertEqual(edges[0].kind, "explicit")
        self.assertEqual(edges[0].dst, "invoice-reader")

    def test_suggest_boosts_graph_neighbours(self) -> None:
        graph = build_skills_graph(self._loader())
        rows = graph.suggest("extract text from scanned pdf", limit=5)
        names = [row["name"] for row in rows]
        self.assertIn("pdf-ocr-extractor", names)
        # invoice-reader never mentions "pdf" in its own text; it must surface
        # through its explicit relation, with the boosting skill named.
        self.assertIn("invoice-reader", names)
        row = next(r for r in rows if r["name"] == "invoice-reader")
        self.assertEqual(row["via"], "pdf-ocr-extractor")

    def test_suggest_empty_query_returns_empty(self) -> None:
        graph = build_skills_graph(self._loader())
        self.assertEqual(graph.suggest("zzz-no-match-zzz"), [])

    def test_related_on_unknown_node_is_empty(self) -> None:
        graph = SkillsGraph({"a": SkillNode("a")}, [SkillEdge("a", "b", "explicit", 1.0)])
        self.assertEqual(graph.related("missing"), [])


if __name__ == "__main__":
    unittest.main()
