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

    def catalog_token(self):
        # Stable for this fake: its catalog never changes.
        return (self._entries, frozenset())

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


class CatalogCacheTests(unittest.TestCase):
    def test_graph_and_search_are_kept_per_catalog_and_follow_a_skill_edit(self) -> None:
        import tempfile
        from pathlib import Path

        from navin.agent import skills as skills_module
        from navin.agent.skills import SkillsLoader

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / ".navin" / "skills" / "alpha-tool"
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text("---\nname: alpha-tool\ndescription: Alpha zebra helper\n---\nbody\n")
            loader = SkillsLoader(root, builtin_skills_dir=root / "none")
            first = build_skills_graph(loader)
            again = build_skills_graph(loader)
            self.assertEqual(set(first.nodes), {"alpha-tool"})
            self.assertEqual(first.edges, again.edges)
            self.assertEqual([row["name"] for row in loader.search_skills("zebra")], ["alpha-tool"])
            folder2 = root / ".navin" / "skills" / "beta-tool"
            folder2.mkdir()
            (folder2 / "SKILL.md").write_text("---\nname: beta-tool\ndescription: Beta quokka helper\n---\nbody\n")
            loader.invalidate_scan()
            skills_module._SKILL_META_TTL.clear()
            self.assertEqual(set(build_skills_graph(loader).nodes), {"alpha-tool", "beta-tool"})
            self.assertEqual([row["name"] for row in loader.search_skills("quokka")], ["beta-tool"])
