"""Graph engine: packages view, queries, diffs, and notify fan-out."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.index import get_index
from navin.index.warmer import note_file_written
from navin.webui.metagraph import (
    build_metagraph,
    query_metagraph,
    rebuild_and_notify,
)


class _Tree(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def write(self, rel: str, text: str = "x = 1\n") -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        note_file_written(path)
        return path


class GraphViewsTest(_Tree):
    def test_files_view_has_generation(self) -> None:
        self.write("pkg/a.py", "import pkg.b\n")
        self.write("pkg/b.py", "y = 2\n")
        get_index(self.root).ensure()
        graph = build_metagraph(self.root, view="files")
        self.assertGreaterEqual(graph.get("generation", 0), 1)
        self.assertEqual(graph.get("view"), "files")
        ids = {n["id"] for n in graph["nodes"]}
        self.assertIn("pkg/a.py", ids)
        self.assertIn("pkg/b.py", ids)

    def test_packages_view_aggregates(self) -> None:
        self.write("pkg/a.py", "import pkg.b\n")
        self.write("pkg/b.py", "y = 2\n")
        self.write("web/ui.py", "from pkg import a\n")
        get_index(self.root).ensure()
        graph = build_metagraph(self.root, view="packages")
        self.assertEqual(graph.get("view"), "packages")
        ids = {n["id"] for n in graph["nodes"]}
        self.assertIn("pkg", ids)
        self.assertIn("web", ids)
        self.assertTrue(graph.get("clusters"))

    def test_query_path_and_impact(self) -> None:
        self.write("a.py", "import b\n")
        self.write("b.py", "import c\n")
        self.write("c.py", "z = 3\n")
        get_index(self.root).ensure()
        build_metagraph(self.root)
        path = query_metagraph(
            self.root, "path", args={"from": "a.py", "to": "c.py"}
        )
        self.assertTrue(path.get("found"))
        self.assertEqual(path.get("path"), ["a.py", "b.py", "c.py"])
        impact = query_metagraph(self.root, "impact", args={"path": "c.py"})
        deps = impact.get("dependents") or []
        self.assertIn("b.py", deps)
        self.assertIn("a.py", deps)

    def test_query_hubs_and_cluster(self) -> None:
        self.write("hub.py", "import leaf\n")
        self.write("leaf.py", "v = 1\n")
        self.write("other/x.py", "from hub import leaf\n")
        get_index(self.root).ensure()
        build_metagraph(self.root)
        hubs = query_metagraph(self.root, "hubs", args={"limit": 5})
        self.assertTrue(hubs.get("hubs"))
        cluster = query_metagraph(self.root, "cluster", args={"id": "other"})
        self.assertIn("other/x.py", cluster.get("member_ids") or [])


class LayoutOnDemandTest(_Tree):
    """The agent reads degrees and kinds; only the Graph tab needs positions.

    The force layout was ~85% of every build and it ran before every model
    call through the runtime-context provider.
    """

    def setUp(self) -> None:
        super().setUp()
        self.write("a.py", "import b\n")
        self.write("b.py", "v = 1\n")
        get_index(self.root).ensure()

    def test_no_layout_still_answers_degrees_and_kinds(self) -> None:
        from navin.utils.native import native

        core = native()
        if core is None:
            self.skipTest("native module not built")
        with mock.patch.object(core, "graph_layout", side_effect=AssertionError("layout ran")):
            graph = build_metagraph(self.root, layout=False)
        by_id = {n["id"]: n for n in graph["nodes"]}
        self.assertEqual(by_id["a.py"]["out_degree"], 1)
        self.assertEqual(by_id["b.py"]["in_degree"], 1)
        self.assertTrue(graph.get("kinds"))
        self.assertNotIn("positions", graph)

    def test_no_layout_keeps_the_positions_the_tab_already_drew(self) -> None:
        drawn = build_metagraph(self.root)
        if not drawn.get("positions"):
            self.skipTest("no layout engine available")
        again = build_metagraph(self.root, layout=False)
        self.assertEqual(again.get("positions"), drawn["positions"])
        # And a later drawn build starts from those positions (sticky).
        redrawn = build_metagraph(self.root)
        self.assertEqual(set(redrawn["positions"]), set(drawn["positions"]))

    def test_queries_do_not_pay_for_a_layout(self) -> None:
        from navin.utils.native import native
        from navin.webui import metagraph as mg

        core = native()
        if core is None:
            self.skipTest("native module not built")
        with mg._cache_lock:
            mg._state_cache.clear()
        with mock.patch.object(core, "graph_layout", side_effect=AssertionError("layout ran")):
            impact = query_metagraph(self.root, "impact", args={"path": "b.py"})
        self.assertIn("a.py", impact.get("dependents") or [])


class GraphNotifyTest(_Tree):
    def test_rebuild_and_notify_publishes_event(self) -> None:
        self.write("a.py", "x = 1\n")
        get_index(self.root).ensure()
        bus = mock.Mock()
        bus.outbound = mock.Mock()
        payload = rebuild_and_notify(self.root, bus=bus)
        self.assertGreaterEqual(payload.get("generation", 0), 1)
        bus.outbound.put_nowait.assert_called()
        msg = bus.outbound.put_nowait.call_args[0][0]
        event = msg.event
        self.assertEqual(getattr(event, "project_path", None), str(self.root))
        self.assertGreaterEqual(getattr(event, "generation", 0), 1)


class GraphParityTest(_Tree):
    """Native and Python engines agree on degrees for a tiny tree."""

    def test_python_fallback_builds(self) -> None:
        self.write("a.py", "import b\n")
        self.write("b.py", "y = 1\n")
        get_index(self.root).ensure()
        prev = os.environ.get("NAVIN_DISABLE_NATIVE")
        os.environ["NAVIN_DISABLE_NATIVE"] = "1"
        try:
            # Force native() to re-read the env flag.
            import navin.utils.native as native_mod

            native_mod._loaded = False
            native_mod._module = None
            graph = build_metagraph(self.root)
            self.assertEqual(graph.get("engine"), "python")
            by_id = {n["id"]: n for n in graph["nodes"]}
            self.assertEqual(by_id["a.py"]["out_degree"], 1)
            self.assertEqual(by_id["b.py"]["in_degree"], 1)
        finally:
            if prev is None:
                os.environ.pop("NAVIN_DISABLE_NATIVE", None)
            else:
                os.environ["NAVIN_DISABLE_NATIVE"] = prev
            import navin.utils.native as native_mod

            native_mod._loaded = False
            native_mod._module = None


if __name__ == "__main__":
    unittest.main()
