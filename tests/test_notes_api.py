# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The /api/notes/* HTTP layer: auth, transport quirks, error mapping."""

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from navin.webui.notes_api import dispatch_notes_route


class FakeRequest:
    def __init__(self, path: str, *, body: str | None = None) -> None:
        self.path = path
        self.headers: dict[str, str] = {}
        if body is not None:
            encoded = base64.b64encode(body.encode("utf-8")).decode("ascii")
            for index in range(0, len(encoded), 4000):
                self.headers[f"x-navin-file-body-{index // 4000}"] = encoded[
                    index : index + 4000
                ]


def _json_body(response) -> dict:
    return json.loads(bytes(response.body).decode("utf-8"))


class NotesApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        patcher = mock.patch(
            "navin.webui.notes_api.notes_root", return_value=self.root
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def call(self, path: str, *, body: str | None = None, authorized: bool = True):
        return asyncio.run(
            dispatch_notes_route(
                path.split("?", 1)[0],
                FakeRequest(path, body=body),
                check_token=lambda _request: authorized,
            )
        )


class RoutingTest(NotesApiTestCase):
    def test_non_notes_paths_are_ignored(self) -> None:
        self.assertIsNone(self.call("/api/webui/sidebar-state"))

    def test_unknown_notes_route_is_404(self) -> None:
        self.assertEqual(self.call("/api/notes/nope").status_code, 404)

    def test_unauthorized_is_401(self) -> None:
        response = self.call("/api/notes/list", authorized=False)
        self.assertEqual(response.status_code, 401)


class CrudOverHttpTest(NotesApiTestCase):
    def test_create_list_get_update_roundtrip(self) -> None:
        created = _json_body(
            self.call(
                "/api/notes/create?title=Essai&folder=inbox", body="# Bonjour"
            )
        )
        note_id = created["note"]["id"]
        self.assertEqual(created["markdown"], "# Bonjour")

        listing = _json_body(self.call("/api/notes/list?folder=inbox"))
        self.assertEqual(listing["total"], 1)

        got = _json_body(self.call(f"/api/notes/get?id={note_id}"))
        self.assertEqual(got["markdown"], "# Bonjour")

        updated = _json_body(
            self.call(
                f"/api/notes/update?id={note_id}&pinned=1", body="# Bonjour v2"
            )
        )
        self.assertTrue(updated["note"]["pinned"])
        self.assertEqual(updated["markdown"], "# Bonjour v2")

    def test_update_without_body_keeps_markdown(self) -> None:
        created = _json_body(
            self.call("/api/notes/create?title=A", body="contenu")
        )
        note_id = created["note"]["id"]
        _json_body(self.call(f"/api/notes/update?id={note_id}&pinned=1"))
        got = _json_body(self.call(f"/api/notes/get?id={note_id}"))
        self.assertEqual(got["markdown"], "contenu")

    def test_conflict_maps_to_409(self) -> None:
        created = _json_body(self.call("/api/notes/create?title=A", body="v1"))
        note_id = created["note"]["id"]
        stale = created["note"]["updated"]
        self.call(f"/api/notes/update?id={note_id}", body="v2")
        response = self.call(
            f"/api/notes/update?id={note_id}&base_updated={stale}", body="v3"
        )
        self.assertEqual(response.status_code, 409)

    def test_tags_travel_as_json(self) -> None:
        created = _json_body(self.call("/api/notes/create?title=A"))
        note_id = created["note"]["id"]
        updated = _json_body(
            self.call(
                f"/api/notes/update?id={note_id}&tags=%5B%22projet%22%2C%22vision%22%5D"
            )
        )
        self.assertEqual(updated["note"]["tags"], ["projet", "vision"])


class TasksAndFoldersOverHttpTest(NotesApiTestCase):
    def test_tasks_listing_and_toggle(self) -> None:
        created = _json_body(
            self.call("/api/notes/create?title=Todo", body="- [ ] faire un truc\n")
        )
        note_id = created["note"]["id"]
        tasks = _json_body(self.call("/api/notes/tasks"))
        self.assertEqual(tasks["total"], 1)
        line = tasks["tasks"][0]["line"]
        toggled = _json_body(
            self.call(f"/api/notes/tasks/toggle?id={note_id}&line={line}&done=1")
        )
        self.assertTrue(toggled["done"])
        self.assertEqual(_json_body(self.call("/api/notes/tasks"))["total"], 0)

    def test_tasks_add_quick_capture(self) -> None:
        added = _json_body(
            self.call("/api/notes/tasks/add?text=appeler%20le%20client&inbox_title=Taches")
        )
        self.assertEqual(added["note_title"], "Taches")
        tasks = _json_body(self.call("/api/notes/tasks"))
        self.assertEqual(tasks["total"], 1)
        self.assertEqual(tasks["tasks"][0]["text"], "appeler le client")

    def test_tasks_add_empty_text_maps_to_400(self) -> None:
        response = self.call("/api/notes/tasks/add?text=")
        self.assertEqual(response.status_code, 400)

    def test_folder_lifecycle(self) -> None:
        self.call("/api/notes/folders/create?path=projets")
        folders = _json_body(self.call("/api/notes/folders"))["folders"]
        self.assertEqual(folders[0]["path"], "projets")
        self.call("/api/notes/folders/rename?path=projets&name=archives")
        folders = _json_body(self.call("/api/notes/folders"))["folders"]
        self.assertEqual(folders[0]["path"], "archives")
        response = self.call("/api/notes/folders/delete?path=archives")
        self.assertEqual(_json_body(response), {"ok": True})

    def test_invalid_folder_maps_to_400(self) -> None:
        response = self.call("/api/notes/folders/create?path=..%2Fescape")
        self.assertEqual(response.status_code, 400)


class AttachmentsOverHttpTest(NotesApiTestCase):
    def test_upload_then_serve_binary(self) -> None:
        raw = base64.b64encode(b"\x89PNG fake").decode("ascii")
        saved = _json_body(
            self.call("/api/notes/attachments/upload?name=img.png", body=raw)
        )
        served = self.call(f"/api/notes/file?path={saved['path']}")
        self.assertEqual(served.status_code, 200)
        self.assertEqual(bytes(served.body), b"\x89PNG fake")
        content_type = dict(served.headers.raw_items())["Content-Type"]
        self.assertEqual(content_type, "image/png")

    def test_upload_without_body_is_400(self) -> None:
        response = self.call("/api/notes/attachments/upload?name=x.bin")
        self.assertEqual(response.status_code, 400)


class PropsAndViewsOverHttpTest(NotesApiTestCase):
    def test_props_travel_as_json(self) -> None:
        created = _json_body(self.call("/api/notes/create?title=Fiche"))
        note_id = created["note"]["id"]
        props = quote(json.dumps({"status": "En cours", "priority": 1}))
        updated = _json_body(
            self.call(f"/api/notes/update?id={note_id}&props={props}")
        )
        self.assertEqual(updated["note"]["props"]["status"], "En cours")
        self.assertEqual(updated["note"]["props"]["priority"], 1)

    def test_views_lifecycle_over_http(self) -> None:
        payload = quote(
            json.dumps({"name": "Board", "kind": "board", "group_by": "status"})
        )
        saved = _json_body(self.call(f"/api/notes/views/save?view={payload}"))
        view_id = saved["view"]["id"]
        views = _json_body(self.call("/api/notes/views"))["views"]
        self.assertEqual(views[0]["name"], "Board")
        deleted = _json_body(self.call(f"/api/notes/views/delete?id={view_id}"))
        self.assertEqual(deleted, {"ok": True})

    def test_views_save_without_payload_is_400(self) -> None:
        response = self.call("/api/notes/views/save")
        self.assertEqual(response.status_code, 400)


class AskOverHttpTest(NotesApiTestCase):
    def test_ask_returns_memory_payload(self) -> None:
        async def fake_ask(root, query, *, limit=8):
            return {
                "passages": [{"note_id": "n1", "title": "Pricing", "text": query}],
                "semantic": True,
                "syncing": False,
            }

        with mock.patch("navin.notes.memory.ask_notes", side_effect=fake_ask):
            payload = _json_body(self.call("/api/notes/ask?q=pricing+juillet"))
        self.assertTrue(payload["semantic"])
        self.assertEqual(payload["passages"][0]["title"], "Pricing")

    def test_ask_without_query_returns_empty(self) -> None:
        payload = _json_body(self.call("/api/notes/ask"))
        self.assertEqual(payload["passages"], [])
        self.assertFalse(payload["semantic"])


class SearchAndTrashOverHttpTest(NotesApiTestCase):
    def test_search_and_trash_flow(self) -> None:
        created = _json_body(
            self.call("/api/notes/create?title=Pricing", body="decision de juillet")
        )
        note_id = created["note"]["id"]
        results = _json_body(self.call("/api/notes/search?q=juillet"))["results"]
        self.assertEqual(results[0]["id"], note_id)
        self.call(f"/api/notes/delete?id={note_id}")
        trash = _json_body(self.call("/api/notes/trash"))["notes"]
        self.assertEqual(trash[0]["id"], note_id)
        restored = _json_body(self.call(f"/api/notes/restore?id={note_id}"))
        self.assertEqual(restored["note"]["title"], "Pricing")

    def test_graph_over_http(self) -> None:
        self.call("/api/notes/create?title=Alpha", body="Lien vers [[Beta]]")
        self.call("/api/notes/create?title=Beta")
        graph = _json_body(self.call("/api/notes/graph"))
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph["edges"][0]["kind"], "link")

    def test_purge_and_empty_trash(self) -> None:
        first = _json_body(self.call("/api/notes/create?title=A"))["note"]["id"]
        second = _json_body(self.call("/api/notes/create?title=B"))["note"]["id"]
        self.call(f"/api/notes/delete?id={first}")
        self.call(f"/api/notes/delete?id={second}")
        purged = _json_body(self.call(f"/api/notes/purge?id={first}"))
        self.assertEqual(purged, {"ok": True, "id": first})
        remaining = _json_body(self.call("/api/notes/trash"))["notes"]
        self.assertEqual([n["id"] for n in remaining], [second])
        emptied = _json_body(self.call("/api/notes/trash/empty"))
        self.assertEqual(emptied["purged"], 1)
        self.assertEqual(_json_body(self.call("/api/notes/trash"))["notes"], [])

    def test_force_folder_delete_over_http(self) -> None:
        self.call("/api/notes/create?title=A&folder=plein")
        blocked = self.call("/api/notes/folders/delete?path=plein")
        self.assertEqual(blocked.status_code, 409)
        forced = _json_body(self.call("/api/notes/folders/delete?path=plein&force=1"))
        self.assertEqual(forced, {"ok": True})
        trash = _json_body(self.call("/api/notes/trash"))["notes"]
        self.assertEqual(len(trash), 1)

    def test_store_errors_do_not_leak_tracebacks(self) -> None:
        response = self.call("/api/notes/get?id=inconnu")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(bytes(response.body).decode(), "note not found")


if __name__ == "__main__":
    unittest.main()
