"""The project map: what the index can see, and whether its roles are current.

The bug these cover was not an empty map, it was a confident wrong one. An agent
workspace whose .gitignore commits only ``memory/`` reported seven files and full
coverage while a 27-file project sat beside it, unseen, because discovery had
inherited a whitelist written for commit hygiene.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from navin.index import get_index
from navin.index.store import _uses_optin_gitignore, discover_files
from navin.index.warmer import note_file_written
from navin.webui.metagraph import (
    MetagraphError,
    annotate_metadata,
    build_metagraph,
)


def _git_init(root: Path) -> bool:
    """Initialise a repo, or report that git is unavailable."""
    try:
        for args in (
            ["init", "-q"],
            ["config", "user.email", "t@example.com"],
            ["config", "user.name", "t"],
        ):
            subprocess.run(  # noqa: S603
                ["git", "-C", str(root), *args],
                check=True,
                capture_output=True,
            )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


class _Tree(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def write(self, rel: str, text: str = "x = 1\n") -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        # Agent writes funnel through record_file_before, which marks any
        # loaded index dirty; mirror that so the revalidation TTL never
        # serves this test a pre-write snapshot.
        note_file_written(path)
        return path

    def graph(self) -> dict:
        get_index(self.root).ensure()
        return build_metagraph(self.root)


class DiscoveryVisibilityTest(_Tree):
    """A .gitignore that ignores everything is not describing build artifacts."""

    def test_an_optin_whitelist_is_recognised(self) -> None:
        self.write(".gitignore", "/*\n!memory/\n!SOUL.md\n")
        self.assertTrue(_uses_optin_gitignore(self.root))

    def test_an_ordinary_ignore_list_is_not(self) -> None:
        self.write(".gitignore", "node_modules/\n*.pyc\ndist\n")
        self.assertFalse(_uses_optin_gitignore(self.root))

    def test_a_commented_out_rule_does_not_count(self) -> None:
        self.write(".gitignore", "# /*\nbuild/\n")
        self.assertFalse(_uses_optin_gitignore(self.root))

    def test_a_missing_gitignore_is_not_a_whitelist(self) -> None:
        self.assertFalse(_uses_optin_gitignore(self.root))

    def test_the_project_beside_the_memory_dir_stays_visible(self) -> None:
        """The regression itself, reproduced in miniature."""
        if not _git_init(self.root):
            self.skipTest("git unavailable")
        self.write(".gitignore", "/*\n!memory/\n!SOUL.md\n")
        self.write("SOUL.md", "# soul\n")
        self.write("memory/MEMORY.md", "# memory\n")
        for i in range(4):
            self.write(f"projects/deck/slide_{i}.py")

        files, method, _ = discover_files(self.root)
        self.assertEqual(method, "walk")
        self.assertEqual(
            sum(1 for f in files if f.startswith("projects/deck/")),
            4,
            "the whitelist must not decide what the index may see",
        )

    def test_the_agents_own_session_state_is_not_project_content(self) -> None:
        """Session files are base64 of the session key, so they read as noise.

        Thirty-odd blobs named ``d2Vic29ja2V0Oj…`` crowded out the eight real
        documents beside them in the graph. They are navin's bookkeeping, not the
        user's project, and they were only ever visible because the walk was
        added to stop trusting an opt-in .gitignore.
        """
        self.write("SOUL.md", "# soul\n")
        self.write("memory/MEMORY.md", "# memory\n")
        self.write("memory/history.jsonl", '{"t": 1}\n')
        self.write("sessions/d2Vic29ja2V0OjBkOTIwZTI.json", "{}")
        self.write("README.md", "# readme\n")

        files, _, _ = discover_files(self.root)
        self.assertIn("README.md", files)
        self.assertIn("memory/MEMORY.md", files)
        self.assertNotIn("memory/history.jsonl", files)
        self.assertEqual([f for f in files if f.startswith("sessions/")], [])

    def test_a_project_inside_the_workspace_keeps_its_own_sessions_dir(self) -> None:
        """``sessions/`` is a Django app directory as often as it is agent state."""
        self.write("SOUL.md", "# soul\n")
        self.write("memory/MEMORY.md", "# memory\n")
        self.write("sessions/d2Vic29ja2V0OjA.json", "{}")
        self.write("projects/site/sessions/views.py", "def index():\n    return 1\n")

        files, _, _ = discover_files(self.root)
        self.assertIn("projects/site/sessions/views.py", files)
        self.assertNotIn("sessions/d2Vic29ja2V0OjA.json", files)

    def test_a_plain_project_named_sessions_is_untouched(self) -> None:
        """Without the workspace markers, nothing is treated as agent state."""
        self.write("sessions/views.py", "def index():\n    return 1\n")
        self.write("memory/history.jsonl", '{"t": 1}\n')

        files, _, _ = discover_files(self.root)
        self.assertIn("sessions/views.py", files)
        self.assertIn("memory/history.jsonl", files)

    def test_a_normal_repo_still_honours_gitignore(self) -> None:
        """The whole point of preferring git is not sacrificed to fix the above."""
        if not _git_init(self.root):
            self.skipTest("git unavailable")
        self.write(".gitignore", "secret.py\n")
        self.write("kept.py")
        self.write("secret.py")

        files, method, _ = discover_files(self.root)
        self.assertEqual(method, "git")
        self.assertIn("kept.py", files)
        self.assertNotIn("secret.py", files)


class AnnotateTest(_Tree):
    """Roles are written through Python so the stamp is real and the schema holds."""

    def setUp(self) -> None:
        super().setUp()
        self.write("app.py", "import helper\n")
        self.write("helper.py")

    def _index(self) -> dict:
        raw = json.loads(
            (self.root / ".navin" / "metadata" / "index.json").read_text()
        )
        return raw["files"]

    def test_a_role_is_stored_with_a_fingerprint(self) -> None:
        result = annotate_metadata(self.root, {"app.py": {"role": "Entry point"}})
        self.assertEqual(result["written"], ["app.py"])
        entry = self._index()["app.py"]
        self.assertEqual(entry["role"], "Entry point")
        self.assertTrue(entry["fingerprint"], "Python must stamp what the model cannot")

    def test_a_second_call_merges_instead_of_replacing(self) -> None:
        annotate_metadata(self.root, {"app.py": {"role": "Entry point"}})
        annotate_metadata(self.root, {"helper.py": {"role": "Helpers"}})
        self.assertEqual(sorted(self._index()), ["app.py", "helper.py"])

    def test_a_partial_update_keeps_the_existing_role(self) -> None:
        annotate_metadata(self.root, {"app.py": {"role": "Entry point"}})
        annotate_metadata(self.root, {"app.py": {"tags": ["entry"]}})
        entry = self._index()["app.py"]
        self.assertEqual(entry["role"], "Entry point")
        self.assertEqual(entry["tags"], ["entry"])

    def test_a_path_the_index_cannot_see_is_reported_not_stored(self) -> None:
        """Storing it would inflate coverage with an entry no graph can show."""
        result = annotate_metadata(
            self.root, {"app.py": {"role": "Entry"}, "ghost.py": {"role": "Nope"}}
        )
        self.assertEqual(result["written"], ["app.py"])
        self.assertEqual(result["unknown"], ["ghost.py"])
        self.assertNotIn("ghost.py", self._index())

    def test_a_leading_dot_slash_is_tolerated(self) -> None:
        result = annotate_metadata(self.root, {"./app.py": {"role": "Entry"}})
        self.assertEqual(result["written"], ["app.py"])

    def test_a_dotted_directory_is_not_mangled(self) -> None:
        """``lstrip("./")`` would turn .github/ci.yml into github/ci.yml."""
        self.write(".github/ci.yml", "on: push\n")
        get_index(self.root).refresh()
        result = annotate_metadata(self.root, {".github/ci.yml": {"role": "CI"}})
        self.assertEqual(result["written"], [".github/ci.yml"])

    def test_a_new_entry_without_a_role_is_refused(self) -> None:
        with self.assertRaises(MetagraphError):
            annotate_metadata(self.root, {"app.py": {"tags": ["entry"]}})

    def test_an_unknown_kind_is_refused(self) -> None:
        with self.assertRaises(MetagraphError) as caught:
            annotate_metadata(self.root, {"app.py": {"role": "E", "kind": "backend"}})
        self.assertIn("kind", caught.exception.message)

    def test_an_empty_role_is_refused(self) -> None:
        with self.assertRaises(MetagraphError):
            annotate_metadata(self.root, {"app.py": {"role": "   "}})

    def test_a_dependency_on_an_unknown_file_is_dropped(self) -> None:
        annotate_metadata(
            self.root,
            {"app.py": {"role": "E", "depends_on": ["helper.py", "ghost.py"]}},
        )
        self.assertEqual(self._index()["app.py"]["depends_on"], ["helper.py"])

    def test_an_empty_request_is_refused(self) -> None:
        with self.assertRaises(MetagraphError):
            annotate_metadata(self.root, {})

    def test_the_file_stays_readable_json(self) -> None:
        """It is committed and reviewed, so a one-role change is a one-line diff."""
        annotate_metadata(self.root, {"app.py": {"role": "Entry point"}})
        text = (self.root / ".navin" / "metadata" / "index.json").read_text(
            encoding="utf-8"
        )
        self.assertIn("\n  ", text)
        self.assertTrue(text.endswith("\n"))


class StalenessTest(_Tree):
    """A role that no longer describes its file has to say so on its own."""

    def setUp(self) -> None:
        super().setUp()
        self.write("app.py", "import helper\n")
        self.write("helper.py")
        annotate_metadata(self.root, {"app.py": {"role": "Entry point"}})

    def test_a_fresh_annotation_is_not_stale(self) -> None:
        graph = self.graph()
        self.assertEqual(graph["annotated_stale"], 0)
        self.assertEqual(graph["annotated_manual"], 1)

    def test_editing_the_file_marks_its_role_stale(self) -> None:
        self.write("app.py", "import helper\n# now it does something else\n")
        graph = self.graph()
        self.assertEqual(graph["annotated_stale"], 1)
        node = next(n for n in graph["nodes"] if n["id"] == "app.py")
        self.assertTrue(node["role_stale"])

    def test_re_annotating_clears_it(self) -> None:
        self.write("app.py", "import helper\n# now it does something else\n")
        get_index(self.root).ensure()
        annotate_metadata(self.root, {"app.py": {"role": "Entry point, revised"}})
        self.assertEqual(self.graph()["annotated_stale"], 0)

    def test_an_entry_without_a_stamp_is_not_called_stale(self) -> None:
        """Hand-written and pre-upgrade entries would otherwise all cry at once."""
        path = self.root / ".navin" / "metadata" / "index.json"
        path.write_text(
            json.dumps({"files": {"app.py": {"role": "Hand written"}}}),
            encoding="utf-8",
        )
        graph = self.graph()
        self.assertEqual(graph["annotated_manual"], 1)
        self.assertEqual(graph["annotated_stale"], 0)

    def test_the_payload_names_how_files_were_found(self) -> None:
        """"100% coverage" is only meaningful next to the root and the method."""
        self.assertIn(self.graph()["discovery"], {"git", "walk"})


class UpkeepContextTest(_Tree):
    """The upkeep nudge has to be silent unless it has something to say."""

    def _lines(self) -> list[str]:
        from navin.agent.tools.metagraph import _staleness_lines

        get_index(self.root).ensure()
        return _staleness_lines(self.root)

    def test_a_project_without_a_map_is_left_alone(self) -> None:
        """Nagging a project that never opted in would spend tokens every turn."""
        self.write("app.py")
        self.assertEqual(self._lines(), [])

    def test_a_complete_map_says_nothing(self) -> None:
        self.write("app.py")
        annotate_metadata(self.root, {"app.py": {"role": "Entry"}})
        self.assertEqual(self._lines(), [])

    def test_a_stale_role_is_named(self) -> None:
        self.write("app.py")
        annotate_metadata(self.root, {"app.py": {"role": "Entry"}})
        self.write("app.py", "# replaced\nx = 2\n")
        lines = self._lines()
        self.assertTrue(any("app.py" in line for line in lines))

    def test_an_unannotated_code_file_is_counted(self) -> None:
        self.write("app.py")
        self.write("other.py")
        annotate_metadata(self.root, {"app.py": {"role": "Entry"}})
        self.assertTrue(any("other.py" in line for line in self._lines()))

    def test_assets_are_not_demanded(self) -> None:
        """A role for every png would make the debt permanent and the line noise."""
        self.write("app.py")
        self.write("logo.png", "not really a png")
        annotate_metadata(self.root, {"app.py": {"role": "Entry"}})
        self.assertEqual(self._lines(), [])


class RepoMapContextTest(_Tree):
    """The standing map speaks only on repos big enough to get lost in."""

    def _lines(self) -> list[str]:
        from navin.agent.tools.metagraph import _runtime_context_lines

        get_index(self.root).ensure()
        return _runtime_context_lines(self.root)

    def test_a_small_project_gets_no_map(self) -> None:
        self.write("app.py", "import helper\n")
        self.write("helper.py")
        self.assertEqual(self._lines(), [])

    def test_a_large_project_gets_a_compact_map_with_hubs(self) -> None:
        from navin.agent.tools.metagraph import _MAP_MIN_FILES

        self.write("core.py")
        for i in range(_MAP_MIN_FILES + 5):
            self.write(f"mod_{i}.py", "import core\n")
        lines = self._lines()
        self.assertTrue(any(line.startswith("Project map") for line in lines))
        self.assertTrue(any("core.py" in line for line in lines))

    def test_map_and_upkeep_lines_coexist(self) -> None:
        from navin.agent.tools.metagraph import _MAP_MIN_FILES

        self.write("core.py")
        for i in range(_MAP_MIN_FILES + 5):
            self.write(f"mod_{i}.py", "import core\n")
        annotate_metadata(self.root, {"core.py": {"role": "Hub"}})
        lines = self._lines()
        self.assertTrue(any(line.startswith("Project map") for line in lines))
        self.assertTrue(any(".navin/metadata map upkeep" in line for line in lines))


class CursorRenameTest(unittest.TestCase):
    """``memory/.cursor`` read as Cursor IDE config to everyone who saw it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def _store(self):
        from navin.agent.memory import MemoryStore

        return MemoryStore(self.root)

    def test_an_existing_counter_is_carried_over(self) -> None:
        # Legacy layout: root memory/ folder migrates into .navin/memory.
        legacy = self.root / "memory"
        legacy.mkdir(parents=True)
        (legacy / ".cursor").write_text("7", encoding="utf-8")

        store = self._store()
        memory = self.root / ".navin" / "memory"
        self.assertFalse(legacy.exists())
        self.assertFalse((memory / ".cursor").exists())
        self.assertEqual((memory / ".log_navin").read_text(encoding="utf-8"), "7")
        self.assertEqual(store.get_latest_cursor(), 7)

    def test_a_fresh_install_uses_the_new_name_only(self) -> None:
        store = self._store()
        store.append_history("hello")
        memory = self.root / ".navin" / "memory"
        self.assertTrue((memory / ".log_navin").exists())
        self.assertFalse((memory / ".cursor").exists())
        self.assertFalse((self.root / "memory").exists())

    def test_the_new_name_wins_when_both_exist(self) -> None:
        memory = self.root / "memory"
        memory.mkdir(parents=True)
        (memory / ".cursor").write_text("3", encoding="utf-8")
        (memory / ".log_navin").write_text("9", encoding="utf-8")

        self.assertEqual(self._store().get_latest_cursor(), 9)


if __name__ == "__main__":
    unittest.main()
