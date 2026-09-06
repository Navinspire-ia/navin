"""The Notes store: markdown files with frontmatter behaving like a notebook."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.notes import store


class NotesStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class CreateAndReadTest(NotesStoreTestCase):
    def test_create_writes_frontmatter_and_body(self) -> None:
        created = store.create_note(
            self.root, title="Idee produit", folder="produit", markdown="Un plan."
        )
        note = created["note"]
        self.assertEqual(note["title"], "Idee produit")
        self.assertEqual(note["folder"], "produit")
        path = self.root / note["path"]
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("Un plan.", text)

    def test_get_returns_markdown_without_frontmatter(self) -> None:
        created = store.create_note(self.root, title="A", markdown="corps")
        got = store.get_note(self.root, created["note"]["id"])
        self.assertEqual(got["markdown"], "corps")

    def test_slug_collisions_get_suffixes(self) -> None:
        first = store.create_note(self.root, title="Meme titre")
        second = store.create_note(self.root, title="Meme titre")
        self.assertNotEqual(first["note"]["path"], second["note"]["path"])

    def test_external_file_without_frontmatter_is_listed(self) -> None:
        (self.root / "brut.md").write_text("# Juste du texte", encoding="utf-8")
        listing = store.list_notes(self.root)
        titles = [note["title"] for note in listing["notes"]]
        self.assertIn("brut", titles)

    def test_snippet_strips_inline_html_from_colored_text(self) -> None:
        body = (
            '<span style="color: rgb(59, 130, 246);">'
            '<mark data-color="#ef44443d">Priorite haute</mark></span> reste.'
        )
        created = store.create_note(self.root, title="Couleurs", markdown=body)
        listing = store.list_notes(self.root)
        note = next(
            entry
            for entry in listing["notes"]
            if entry["id"] == created["note"]["id"]
        )
        self.assertEqual(note["snippet"], "Priorite haute reste.")


class ListPaginationTest(NotesStoreTestCase):
    def test_cursor_pagination_walks_everything_once(self) -> None:
        for index in range(7):
            store.create_note(self.root, title=f"Note {index}")
        seen: list[str] = []
        cursor: str | None = None
        while True:
            page = store.list_notes(self.root, limit=3, cursor=cursor)
            seen.extend(note["id"] for note in page["notes"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(len(seen), 7)
        self.assertEqual(len(set(seen)), 7)

    def test_filters_folder_tag_query_and_archived(self) -> None:
        kept = store.create_note(self.root, title="Roadmap", folder="produit")
        store.update_note(self.root, kept["note"]["id"], tags=["vision"])
        other = store.create_note(self.root, title="Courses", folder="perso")
        store.update_note(self.root, other["note"]["id"], archived=True)

        by_folder = store.list_notes(self.root, folder="produit")
        self.assertEqual([n["title"] for n in by_folder["notes"]], ["Roadmap"])
        by_tag = store.list_notes(self.root, tag="#vision")
        self.assertEqual(by_tag["total"], 1)
        archived = store.list_notes(self.root, archived=True)
        self.assertEqual([n["title"] for n in archived["notes"]], ["Courses"])
        by_query = store.list_notes(self.root, query="road")
        self.assertEqual(by_query["total"], 1)

    def test_pinned_notes_float_to_the_top(self) -> None:
        store.create_note(self.root, title="Ancienne")
        pinned = store.create_note(self.root, title="Epingle")
        store.update_note(self.root, pinned["note"]["id"], pinned=True)
        store.create_note(self.root, title="Recente")
        listing = store.list_notes(self.root)
        self.assertEqual(listing["notes"][0]["title"], "Epingle")


class UpdateTest(NotesStoreTestCase):
    def test_conflict_when_note_changed_since_load(self) -> None:
        created = store.create_note(self.root, title="A", markdown="v1")
        stale = created["note"]["updated"]
        store.update_note(self.root, created["note"]["id"], markdown="v2")
        with self.assertRaises(store.NoteConflictError):
            store.update_note(
                self.root, created["note"]["id"], markdown="v3", base_updated=stale
            )

    def test_rename_rewrites_incoming_wikilinks(self) -> None:
        target = store.create_note(self.root, title="Pricing Juillet")
        linker = store.create_note(
            self.root,
            title="Reunion",
            markdown="Decision dans [[Pricing Juillet]] et [[Pricing Juillet|le doc]].",
        )
        store.update_note(self.root, target["note"]["id"], title="Pricing V2")
        body = store.get_note(self.root, linker["note"]["id"])["markdown"]
        self.assertIn("[[Pricing V2]]", body)
        self.assertIn("[[Pricing V2|le doc]]", body)

    def test_move_to_another_folder(self) -> None:
        created = store.create_note(self.root, title="A", folder="inbox")
        moved = store.update_note(self.root, created["note"]["id"], folder="archive1")
        self.assertEqual(moved["note"]["folder"], "archive1")
        self.assertEqual(store.list_notes(self.root, folder="inbox")["total"], 0)

    def test_update_snapshots_history(self) -> None:
        created = store.create_note(self.root, title="A", markdown="v1")
        store.update_note(self.root, created["note"]["id"], markdown="v2")
        history = store.list_history(self.root, created["note"]["id"])
        self.assertEqual(len(history), 1)
        snapshot = store.get_history_snapshot(
            self.root, created["note"]["id"], history[0]["stamp"]
        )
        self.assertEqual(snapshot["markdown"], "v1")


class TrashTest(NotesStoreTestCase):
    def test_delete_then_restore_roundtrip(self) -> None:
        created = store.create_note(self.root, title="A", folder="inbox", markdown="x")
        store.delete_note(self.root, created["note"]["id"])
        self.assertEqual(store.list_notes(self.root)["total"], 0)
        trash = store.list_trash(self.root)
        self.assertEqual(len(trash), 1)
        restored = store.restore_note(self.root, created["note"]["id"])
        self.assertEqual(restored["note"]["folder"], "inbox")
        self.assertEqual(store.list_notes(self.root)["total"], 1)

    def test_purge_removes_the_note_and_its_history(self) -> None:
        created = store.create_note(self.root, title="A", markdown="v1")
        note_id = created["note"]["id"]
        store.update_note(self.root, note_id, markdown="v2")
        store.delete_note(self.root, note_id)
        self.assertTrue((self.root / store.TRASH_DIR / f"{note_id}.md").is_file())
        store.purge_note(self.root, note_id)
        self.assertEqual(store.list_trash(self.root), [])
        self.assertFalse((self.root / store.HISTORY_DIR / note_id).exists())
        with self.assertRaises(store.NotesError):
            store.purge_note(self.root, note_id)

    def test_empty_trash_purges_everything(self) -> None:
        for title in ("A", "B", "C"):
            created = store.create_note(self.root, title=title)
            store.delete_note(self.root, created["note"]["id"])
        result = store.empty_trash(self.root)
        self.assertEqual(result["purged"], 3)
        self.assertEqual(store.list_trash(self.root), [])


class FoldersTest(NotesStoreTestCase):
    def test_folder_tree_with_counts(self) -> None:
        store.create_note(self.root, title="A", folder="travail/projets")
        store.create_note(self.root, title="B", folder="travail")
        tree = store.list_folders(self.root)
        travail = next(node for node in tree if node["name"] == "travail")
        self.assertEqual(travail["count"], 2)
        self.assertEqual(travail["children"][0]["name"], "projets")
        self.assertEqual(travail["children"][0]["count"], 1)

    def test_reserved_and_hostile_folders_are_rejected(self) -> None:
        for bad in ("../escape", ".trash", "_files", ".hidden", "a/../../b"):
            with self.assertRaises(store.NotesError, msg=bad):
                store.create_folder(self.root, bad)

    def test_delete_refuses_non_empty_folder(self) -> None:
        store.create_note(self.root, title="A", folder="plein")
        with self.assertRaises(store.NotesError):
            store.delete_folder(self.root, "plein")
        store.create_folder(self.root, "vide")
        self.assertEqual(store.delete_folder(self.root, "vide"), {"ok": True})

    def test_rename_folder(self) -> None:
        store.create_note(self.root, title="A", folder="ancien")
        store.rename_folder(self.root, "ancien", "nouveau")
        self.assertEqual(store.list_notes(self.root, folder="nouveau")["total"], 1)

    def test_force_delete_moves_contained_notes_to_trash(self) -> None:
        store.create_note(self.root, title="A", folder="plein")
        store.create_note(self.root, title="B", folder="plein/sous")
        self.assertEqual(store.delete_folder(self.root, "plein", force=True), {"ok": True})
        self.assertEqual(store.list_notes(self.root)["total"], 0)
        # Recoverable: both notes are in the trash, not gone.
        self.assertEqual(len(store.list_trash(self.root)), 2)
        self.assertFalse((self.root / "plein").exists())


class TasksTest(NotesStoreTestCase):
    def test_extract_tasks_with_metadata_and_fences(self) -> None:
        body = (
            "- [ ] Appeler le client @due(2026-09-01) !p1 #ventes\n"
            "- [x] Fini\n"
            "```\n- [ ] pas une tache (code)\n```\n"
        )
        tasks = store.extract_tasks(body)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["due"], "2026-09-01")
        self.assertEqual(tasks[0]["priority"], 1)
        self.assertIn("ventes", tasks[0]["tags"])

    def test_list_tasks_across_notes_and_toggle(self) -> None:
        created = store.create_note(
            self.root, title="Todo", markdown="- [ ] tache un\n- [ ] tache deux\n"
        )
        listing = store.list_tasks(self.root)
        self.assertEqual(listing["total"], 2)
        first = listing["tasks"][0]
        store.toggle_task(self.root, created["note"]["id"], first["line"], True)
        self.assertEqual(store.list_tasks(self.root, state="open")["total"], 1)
        self.assertEqual(store.list_tasks(self.root, state="done")["total"], 1)

    def test_toggle_rejects_a_line_that_is_not_a_task(self) -> None:
        created = store.create_note(self.root, title="A", markdown="du texte\n")
        with self.assertRaises(store.NotesError):
            store.toggle_task(self.root, created["note"]["id"], 1, True)

    def test_add_task_creates_the_inbox_note_on_first_use(self) -> None:
        result = store.add_task(self.root, text="acheter du cafe", inbox_title="Taches")
        self.assertEqual(result["note_title"], "Taches")
        listing = store.list_tasks(self.root)
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["tasks"][0]["text"], "acheter du cafe")
        # Second add reuses the same note (title matched case-insensitively).
        again = store.add_task(self.root, text="deuxieme", inbox_title="taches")
        self.assertEqual(again["note_id"], result["note_id"])
        self.assertEqual(store.list_tasks(self.root)["total"], 2)

    def test_add_task_appends_to_an_existing_note(self) -> None:
        created = store.create_note(self.root, title="Projet", markdown="Contexte.\n")
        result = store.add_task(
            self.root, text="ecrire les specs", note_id=created["note"]["id"]
        )
        self.assertEqual(result["note_id"], created["note"]["id"])
        body = store.get_note(self.root, created["note"]["id"])["markdown"]
        self.assertIn("Contexte.\n- [ ] ecrire les specs\n", body)

    def test_add_task_rejects_empty_text(self) -> None:
        with self.assertRaises(store.NotesError):
            store.add_task(self.root, text="   ")


class GraphTest(NotesStoreTestCase):
    def test_graph_resolves_wikilinks_and_tags(self) -> None:
        a = store.create_note(
            self.root, title="Alpha", markdown="Voir [[Beta]] et [[surnom]]."
        )
        b = store.create_note(self.root, title="Beta", markdown="")
        store.update_note(self.root, b["note"]["id"], tags=["projet"])
        c = store.create_note(self.root, title="Gamma", markdown="")
        # "surnom" is an alias of Gamma.
        path = self.root / c["note"]["path"]
        meta, body = store.parse_note_text(path.read_text(encoding="utf-8"))
        meta["aliases"] = ["surnom"]
        text = store.serialize_note(meta, body)
        path.write_text(text, encoding="utf-8")

        graph = store.notes_graph(self.root)
        ids = {node["id"] for node in graph["nodes"]}
        self.assertIn(a["note"]["id"], ids)
        self.assertIn("tag:projet", ids)
        link_edges = [e for e in graph["edges"] if e["kind"] == "link"]
        self.assertEqual(
            {(e["source"], e["target"]) for e in link_edges},
            {
                (a["note"]["id"], b["note"]["id"]),
                (a["note"]["id"], c["note"]["id"]),
            },
        )
        tag_edges = [e for e in graph["edges"] if e["kind"] == "tag"]
        self.assertEqual(
            tag_edges, [{"source": b["note"]["id"], "target": "tag:projet", "kind": "tag"}]
        )

    def test_graph_drops_dangling_links_and_archived_notes(self) -> None:
        a = store.create_note(self.root, title="A", markdown="[[Inconnu]]")
        hidden = store.create_note(self.root, title="Cache", markdown="")
        store.update_note(self.root, hidden["note"]["id"], archived=True)
        graph = store.notes_graph(self.root)
        self.assertEqual([n["id"] for n in graph["nodes"]], [a["note"]["id"]])
        self.assertEqual(graph["edges"], [])


class BacklinksTest(NotesStoreTestCase):
    def test_backlinks_and_unlinked_mentions(self) -> None:
        target = store.create_note(self.root, title="Navin Pricing")
        store.create_note(
            self.root, title="Lien", markdown="voir [[Navin Pricing]]"
        )
        store.create_note(
            self.root, title="Mention", markdown="on a parle de Navin Pricing hier"
        )
        result = store.note_backlinks(self.root, target["note"]["id"])
        self.assertEqual([b["title"] for b in result["backlinks"]], ["Lien"])
        self.assertEqual(
            [m["title"] for m in result["unlinked_mentions"]], ["Mention"]
        )

    def test_wikilink_extraction_variants(self) -> None:
        links = store.extract_wikilinks(
            "[[Simple]] [[Avec|Label]] [[Ancre#section]] [[simple]]"
        )
        self.assertEqual(links, ["Simple", "Avec", "Ancre"])

    def test_unlinked_mentions_ignore_linked_notes_and_partial_words(self) -> None:
        target = store.create_note(self.root, title="Budget 2027")
        store.create_note(self.root, title="Both", markdown="[[Budget 2027]] et Budget 2027")
        store.create_note(self.root, title="Words", markdown="budget prevu pour 2027 separement")
        store.create_note(self.root, title="Exact", markdown="le BUDGET 2027 est valide")
        result = store.note_backlinks(self.root, target["note"]["id"])
        self.assertEqual([b["title"] for b in result["backlinks"]], ["Both"])
        self.assertEqual([m["title"] for m in result["unlinked_mentions"]], ["Exact"])

    def test_external_edit_shows_up_in_mentions_without_rebuild(self) -> None:
        target = store.create_note(self.root, title="Roadmap Q4")
        other = store.create_note(self.root, title="Other", markdown="rien")
        self.assertEqual(store.note_backlinks(self.root, target["note"]["id"])["unlinked_mentions"], [])
        path = self.root / other["note"]["path"]
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("rien", "voir Roadmap Q4 demain"), encoding="utf-8")
        import os
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
        result = store.note_backlinks(self.root, target["note"]["id"])
        self.assertEqual([m["title"] for m in result["unlinked_mentions"]], ["Other"])


class ReadAvoidanceTest(NotesStoreTestCase):
    """Listing, searching, tasks and backlinks must not re-read unchanged notes."""

    def _count_reads(self, fn):
        from unittest import mock

        original = Path.read_text
        calls = {"n": 0}

        def counting(path_self, *args, **kwargs):
            calls["n"] += 1
            return original(path_self, *args, **kwargs)

        with mock.patch.object(Path, "read_text", counting):
            out = fn()
        return out, calls["n"]

    def test_warm_operations_do_not_open_note_files(self) -> None:
        for index in range(12):
            store.create_note(
                self.root,
                title=f"Note {index}",
                markdown=f"pricing decision {index}\n- [ ] task {index}\nvoir [[Note 0]]",
            )
        target = store.scan_notes(self.root)[0]
        # Warm everything once (also syncs the search index).
        store.search_notes(self.root, "pricing")
        store.note_backlinks(self.root, target["id"])

        _, reads = self._count_reads(lambda: store.list_notes(self.root, limit=50))
        self.assertEqual(reads, 0)
        result, reads = self._count_reads(lambda: store.search_notes(self.root, "pricing decision 7"))
        self.assertEqual(reads, 0)
        self.assertEqual(result["results"][0]["title"], "Note 7")
        tasks, reads = self._count_reads(lambda: store.list_tasks(self.root))
        self.assertEqual(reads, 0)
        self.assertEqual(tasks["total"], 12)
        # Backlinks read the target note itself (get-style), nothing else.
        _, reads = self._count_reads(lambda: store.note_backlinks(self.root, target["id"]))
        self.assertLessEqual(reads, 1)

    def test_search_index_sync_reads_only_changed_notes(self) -> None:
        from navin.notes.search_index import NotesSearchIndex

        for index in range(5):
            store.create_note(self.root, title=f"N{index}", markdown=f"corps {index}")
        index = NotesSearchIndex(self.root)
        loaded: list[str] = []

        def loader(document):
            loaded.append(document["id"])
            return store._load_document_body(document)

        index.sync(store._search_documents(self.root), body_loader=loader)
        self.assertEqual(loaded, [])  # create_note already indexed each note
        note = store.scan_notes(self.root)[0]
        path = self.root / note["path"]
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("corps", "nouveau corps"), encoding="utf-8")
        import os
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
        index.sync(store._search_documents(self.root), body_loader=loader)
        self.assertEqual(loaded, [note["id"]])
        self.assertEqual(store.search_notes(self.root, "nouveau")["results"][0]["id"], note["id"])

    def test_plain_edit_does_not_rewrite_the_manifest(self) -> None:
        created = store.create_note(self.root, title="Stable", markdown="v1")
        manifest = store._manifest_path(self.root)
        before = manifest.stat().st_mtime_ns
        import time
        time.sleep(0.01)
        store.update_note(self.root, created["note"]["id"], markdown="v2")
        self.assertEqual(manifest.stat().st_mtime_ns, before)
        store.update_note(self.root, created["note"]["id"], folder="ailleurs")
        self.assertNotEqual(manifest.stat().st_mtime_ns, before)


class AttachmentsTest(NotesStoreTestCase):
    def test_save_list_and_read_attachment(self) -> None:
        import base64

        payload = base64.b64encode(b"PNGDATA").decode()
        saved = store.save_attachment(self.root, "photo.png", payload)
        self.assertTrue(saved["path"].startswith("_files/"))
        data, content_type = store.read_attachment(self.root, saved["path"])
        self.assertEqual(data, b"PNGDATA")
        self.assertEqual(content_type, "image/png")
        listing = store.list_attachments(self.root)
        self.assertEqual(listing["total"], 1)

    def test_attachment_reference_tracking(self) -> None:
        import base64

        saved = store.save_attachment(
            self.root, "doc.pdf", base64.b64encode(b"PDF").decode()
        )
        note = store.create_note(
            self.root, title="Ref", markdown=f"![doc]({saved['path']})"
        )
        listing = store.list_attachments(self.root)
        self.assertEqual(listing["files"][0]["referenced_by"], [note["note"]["id"]])

    def test_hostile_attachment_paths_are_rejected(self) -> None:
        for bad in ("../secret", "_files/../../etc/passwd", "autre/x.png"):
            with self.assertRaises(store.NotesError, msg=bad):
                store.read_attachment(self.root, bad)


class SearchTest(NotesStoreTestCase):
    def test_search_returns_line_matches_and_title_hits_first(self) -> None:
        store.create_note(self.root, title="Recette", markdown="pates au pesto")
        store.create_note(self.root, title="Pesto maison", markdown="basilic")
        result = store.search_notes(self.root, "pesto")
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(result["results"][0]["title"], "Pesto maison")
        line_match = next(r for r in result["results"] if r["title"] == "Recette")
        self.assertEqual(line_match["matches"][0]["line"], 1)


class PropsTest(NotesStoreTestCase):
    def test_props_roundtrip_in_frontmatter(self) -> None:
        created = store.create_note(self.root, title="Fiche")
        updated = store.update_note(
            self.root,
            created["note"]["id"],
            props={"status": "En cours", "priority": 2, "done": False},
        )
        self.assertEqual(updated["note"]["props"]["status"], "En cours")
        self.assertEqual(updated["note"]["props"]["priority"], 2)
        self.assertIs(updated["note"]["props"]["done"], False)
        listing = store.list_notes(self.root)
        self.assertEqual(listing["notes"][0]["props"]["status"], "En cours")

    def test_props_merge_and_delete_with_null(self) -> None:
        created = store.create_note(self.root, title="Fiche")
        store.update_note(
            self.root, created["note"]["id"], props={"status": "A faire", "owner": "aymen"}
        )
        updated = store.update_note(
            self.root, created["note"]["id"], props={"status": "Fini", "owner": None}
        )
        self.assertEqual(updated["note"]["props"], {"status": "Fini"})

    def test_reserved_keys_and_junk_are_ignored(self) -> None:
        created = store.create_note(self.root, title="Fiche")
        updated = store.update_note(
            self.root,
            created["note"]["id"],
            props={"id": "hack", "title": "hack", "": "x", "ok": "oui"},
        )
        self.assertEqual(updated["note"]["props"], {"ok": "oui"})

    def test_list_prop_values_are_flattened_strings(self) -> None:
        created = store.create_note(self.root, title="Fiche")
        updated = store.update_note(
            self.root, created["note"]["id"], props={"labels": ["a", "", 3]}
        )
        self.assertEqual(updated["note"]["props"]["labels"], ["a", "3"])


class ViewsTest(NotesStoreTestCase):
    def test_save_list_update_delete_view(self) -> None:
        saved = store.save_view(
            self.root,
            {
                "name": "Roadmap",
                "kind": "board",
                "group_by": "status",
                "columns": ["status", "priority"],
            },
        )
        view = saved["view"]
        self.assertEqual(view["kind"], "board")
        self.assertEqual(store.list_views(self.root), [view])

        renamed = store.save_view(self.root, {**view, "name": "Roadmap Q3"})
        self.assertEqual(renamed["view"]["id"], view["id"])
        self.assertEqual(store.list_views(self.root)[0]["name"], "Roadmap Q3")

        store.delete_view(self.root, view["id"])
        self.assertEqual(store.list_views(self.root), [])

    def test_invalid_views_are_rejected(self) -> None:
        with self.assertRaises(store.NotesError):
            store.save_view(self.root, {"name": "", "kind": "table"})
        with self.assertRaises(store.NotesError):
            store.save_view(self.root, {"name": "X", "kind": "gantt"})
        with self.assertRaises(store.NotesError):
            store.delete_view(self.root, "missing")

    def test_views_file_is_not_listed_as_a_note(self) -> None:
        store.save_view(self.root, {"name": "V", "kind": "table"})
        store.create_note(self.root, title="Seule note")
        listing = store.list_notes(self.root)
        self.assertEqual([n["title"] for n in listing["notes"]], ["Seule note"])

    def test_corrupt_views_file_degrades_to_empty(self) -> None:
        (self.root / store.VIEWS_FILE).write_text("{pas du json", encoding="utf-8")
        self.assertEqual(store.list_views(self.root), [])


if __name__ == "__main__":
    unittest.main()
