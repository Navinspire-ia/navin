"""The `notes` agent tool: search, read, list and write over the user's notes."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.agent.tools.notes import NotesTool
from navin.notes import store


class NotesToolTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        patcher = mock.patch(
            "navin.notes.store.notes_root", return_value=self.root
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        self.tool = NotesTool()

    def run_tool(self, **kwargs):
        return asyncio.run(self.tool.execute(**kwargs))


class SearchActionTest(NotesToolTestCase):
    def test_search_reports_passages_with_ids(self) -> None:
        async def fake_ask(root, query, *, limit=8):
            return {
                "passages": [
                    {
                        "note_id": "abc123",
                        "title": "Pricing",
                        "folder": "produit",
                        "heading": "Decision",
                        "line": 3,
                        "text": "abonnement mensuel vingt-neuf euros",
                        "score": 0.9,
                    }
                ],
                "semantic": True,
                "syncing": False,
            }

        with mock.patch("navin.notes.memory.ask_notes", side_effect=fake_ask):
            out = self.run_tool(action="search", query="pricing")
        self.assertIn("Pricing [produit] › Decision", out)
        self.assertIn("id=abc123", out)
        self.assertIn("semantic+lexical", out)

    def test_search_says_lexical_when_degraded(self) -> None:
        async def fake_ask(root, query, *, limit=8):
            return {"passages": [], "semantic": False, "syncing": False}

        with mock.patch("navin.notes.memory.ask_notes", side_effect=fake_ask):
            out = self.run_tool(action="search", query="introuvable")
        self.assertIn("No notes matched", out)

    def test_search_without_query_is_an_error(self) -> None:
        out = self.run_tool(action="search")
        self.assertTrue(out.is_error)


class ReadActionTest(NotesToolTestCase):
    def test_read_by_id_returns_markdown_with_header(self) -> None:
        created = store.create_note(
            self.root, title="Pricing", markdown="decision de juillet"
        )
        out = self.run_tool(action="read", id=created["note"]["id"])
        self.assertIn("# Pricing", out)
        self.assertIn("decision de juillet", out)

    def test_read_by_title_resolves_the_id(self) -> None:
        store.create_note(self.root, title="Plan Q3", markdown="objectifs")
        out = self.run_tool(action="read", title="plan q3")
        self.assertIn("objectifs", out)

    def test_read_unknown_title_is_a_clear_error(self) -> None:
        out = self.run_tool(action="read", title="inexistante")
        self.assertTrue(out.is_error)
        self.assertIn("inexistante", str(out))

    def test_read_unknown_id_maps_store_error(self) -> None:
        out = self.run_tool(action="read", id="zzz")
        self.assertTrue(out.is_error)


class ListActionTest(NotesToolTestCase):
    def test_list_filters_by_tag(self) -> None:
        tagged = store.create_note(self.root, title="Taggee", markdown="x")
        store.update_note(self.root, tagged["note"]["id"], tags=["projet"])
        store.create_note(self.root, title="Autre", markdown="y")
        out = self.run_tool(action="list", tag="projet")
        self.assertIn("Taggee", out)
        self.assertNotIn("Autre", out)

    def test_unknown_action_names_the_valid_ones(self) -> None:
        out = self.run_tool(action="explode")
        self.assertTrue(out.is_error)
        self.assertIn("search", str(out))


class WriteActionsTest(NotesToolTestCase):
    def test_reads_are_flagged_read_only_but_writes_are_not(self) -> None:
        self.assertFalse(self.tool.read_only)
        self.assertTrue(self.tool.call_read_only({"action": "search"}))
        self.assertTrue(self.tool.call_read_only({"action": "read"}))
        self.assertFalse(self.tool.call_read_only({"action": "append"}))
        self.assertFalse(self.tool.call_concurrency_safe({"action": "create"}))

    def test_create_writes_a_note_with_tags_in_a_folder(self) -> None:
        out = self.run_tool(
            action="create",
            title="Decision pricing",
            markdown="# Pricing\n\n29 EUR / mois",
            folder="produit",
            tags=["#decision", "pricing"],
        )
        self.assertIn("Created note 'Decision pricing'", out)
        page = store.list_notes(self.root, folder="produit")
        self.assertEqual(len(page["notes"]), 1)
        note = page["notes"][0]
        self.assertEqual(note["tags"], ["decision", "pricing"])
        self.assertIn("29 EUR", store.get_note(self.root, note["id"])["markdown"])

    def test_create_requires_a_title(self) -> None:
        out = self.run_tool(action="create", markdown="x")
        self.assertTrue(out.is_error)

    def test_append_adds_a_separated_block_and_keeps_history(self) -> None:
        created = store.create_note(self.root, title="Journal", markdown="lundi: kickoff\n")
        note_id = created["note"]["id"]
        out = self.run_tool(action="append", id=note_id, markdown="mardi: revue")
        self.assertIn("Appended 1 line(s)", out)
        body = store.get_note(self.root, note_id)["markdown"]
        self.assertEqual(body, "lundi: kickoff\n\nmardi: revue\n")
        self.assertTrue(store.list_history(self.root, note_id))

    def test_append_by_title_into_an_empty_note(self) -> None:
        store.create_note(self.root, title="Idees", markdown="")
        self.run_tool(action="append", title="idees", markdown="- une idee")
        note = store.list_notes(self.root)["notes"][0]
        self.assertEqual(store.get_note(self.root, note["id"])["markdown"], "- une idee\n")

    def test_append_requires_markdown(self) -> None:
        created = store.create_note(self.root, title="Vide", markdown="a")
        out = self.run_tool(action="append", id=created["note"]["id"], markdown="  ")
        self.assertTrue(out.is_error)

    def test_update_replaces_body_and_renames(self) -> None:
        created = store.create_note(self.root, title="Brouillon", markdown="v1")
        note_id = created["note"]["id"]
        out = self.run_tool(
            action="update", id=note_id, title="Version finale", markdown="v2", tags=["ok"]
        )
        self.assertIn("Updated note 'Version finale'", out)
        page = store.get_note(self.root, note_id)
        self.assertEqual(page["markdown"], "v2")
        self.assertEqual(page["note"]["title"], "Version finale")
        self.assertEqual(page["note"]["tags"], ["ok"])

    def test_update_without_changes_is_an_error(self) -> None:
        created = store.create_note(self.root, title="Stable", markdown="v1")
        out = self.run_tool(action="update", id=created["note"]["id"])
        self.assertTrue(out.is_error)

    def test_ambiguous_title_lists_candidates(self) -> None:
        store.create_note(self.root, title="Reunion", folder="a", markdown="x")
        store.create_note(self.root, title="Reunion", folder="b", markdown="y")
        out = self.run_tool(action="append", title="reunion", markdown="z")
        self.assertTrue(out.is_error)
        self.assertIn("Several notes", str(out))
        self.assertIn("[a]", str(out))

    def test_add_task_to_a_note_and_to_the_inbox(self) -> None:
        created = store.create_note(self.root, title="Projet", markdown="# Projet\n")
        out = self.run_tool(action="add_task", id=created["note"]["id"], text="  relancer   Ana ")
        self.assertIn("relancer Ana", out)
        self.assertIn("Projet", out)
        tasks = store.list_tasks(self.root)["tasks"]
        self.assertEqual([t["text"] for t in tasks], ["relancer Ana"])

        out = self.run_tool(action="add_task", text="acheter du cafe")
        self.assertIn("Tasks", out)
        self.assertEqual(store.list_tasks(self.root)["total"], 2)

    def test_add_task_requires_text(self) -> None:
        out = self.run_tool(action="add_task", text="")
        self.assertTrue(out.is_error)


if __name__ == "__main__":
    unittest.main()
