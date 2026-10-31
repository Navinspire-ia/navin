# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A long chat's transcript rotates at turn end while the UI reads it.

Rotation moves old turns into segment files and rewrites the active file. It
runs on the thread that ends a turn and on every reader thread (the history
endpoint, a share guest). Two rotations at once, or a rotation racing an
append, once corrupted the segments and the active file: the conversation
came back empty after a reload. Every record must survive, exactly once.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from navin.webui import transcript as tr


class RotationRaceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="navin-rotation-")
        root = Path(self._tmp.name)
        for target, value in (
            (tr, "get_webui_dir"),
        ):
            patcher = mock.patch.object(target, value, return_value=root)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Small files: a rotation every few turns.
        for name, size in (("_MAX_TRANSCRIPT_FILE_BYTES", 4096), ("_TARGET_ACTIVE_TRANSCRIPT_BYTES", 2048)):
            patcher = mock.patch.object(tr, name, size)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(tr._flush_all_append_buffers)
        tr._append_buffers.clear()
        self.key = "websocket:chat-rotation"

    def test_turns_survive_rotations_racing_readers(self):
        turns = 100
        errors: list[BaseException] = []
        done = threading.Event()

        def writer():
            try:
                for i in range(turns):
                    tr.append_transcript_object(self.key, {"event": "user", "chat_id": "c", "text": f"question {i}"})
                    tr.append_transcript_object(self.key, {"event": "message", "chat_id": "c", "text": "r" * 300})
                    tr.append_transcript_object(self.key, {"event": "turn_end", "chat_id": "c"})
            except BaseException as exc:  # noqa: BLE001 - reported below
                errors.append(exc)
            finally:
                done.set()

        def reader():
            try:
                while not done.is_set():
                    tr.read_transcript_lines(self.key)
            except BaseException as exc:  # noqa: BLE001 - reported below
                errors.append(exc)

        threads = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(errors, [])
        questions = [line["text"] for line in tr.read_transcript_lines(self.key) if line.get("event") == "user"]
        self.assertEqual(questions, [f"question {i}" for i in range(turns)])

    def test_turn_end_on_event_loop_rotates_off_the_loop(self):
        import asyncio

        loop_thread = threading.get_ident()
        rotation_threads: list[int] = []
        real_rotate = tr._rotate_active_transcript_if_needed

        def spy(session_key):
            rotation_threads.append(threading.get_ident())
            real_rotate(session_key)

        async def scenario():
            with mock.patch.object(tr, "_rotate_active_transcript_if_needed", spy):
                for i in range(40):
                    tr.append_transcript_object(self.key, {"event": "user", "chat_id": "c", "text": f"question {i}"})
                    tr.append_transcript_object(self.key, {"event": "message", "chat_id": "c", "text": "r" * 300})
                    tr.append_transcript_object(self.key, {"event": "turn_end", "chat_id": "c"})
                    await asyncio.sleep(0)
                while tr._background_rotations:
                    await asyncio.gather(*tr._background_rotations.values())

        asyncio.run(scenario())
        self.assertTrue(rotation_threads)
        self.assertNotIn(loop_thread, rotation_threads)
        self.assertTrue(tr._read_segment_ids(self.key))
        questions = [line["text"] for line in tr.read_transcript_lines(self.key) if line.get("event") == "user"]
        self.assertEqual(questions, [f"question {i}" for i in range(40)])

    def test_the_prompt_list_names_every_prompt_with_the_page_offsets(self):
        """The thread is paged and a tool-heavy turn fills a page: the first
        page of a long chat held two of its prompts, and the prompt list
        showed only those. Its index counts prompts like a page's offset."""
        for i in range(30):
            tr.append_transcript_object(self.key, {"event": "user", "chat_id": "c", "text": f"question {i}"})
            for _ in range(6):
                tr.append_transcript_object(self.key, {"event": "message", "chat_id": "c", "text": "r" * 300})
            tr.append_transcript_object(self.key, {"event": "turn_end", "chat_id": "c"})
        tr._flush_all_append_buffers()
        self.assertTrue(tr._read_segment_ids(self.key), "the chat must span several segments")

        prompts = tr.transcript_user_prompts(self.key)
        self.assertEqual([p["text"] for p in prompts], [f"question {i}" for i in range(30)])
        self.assertEqual([p["index"] for p in prompts], list(range(30)))

        # Walk the pages back from the latest, as the chat does on scroll:
        # each page starts at the prompt its offset names.
        before = None
        pages = 0
        while True:
            lines, page = tr._select_transcript_page(self.key, limit=12, before=before)
            first = next(line for line in lines if line.get("event") == "user")
            self.assertEqual(first["text"], prompts[page["user_message_offset"]]["text"])
            pages += 1
            if not page["has_more_before"]:
                break
            before = page["before_cursor"]
        self.assertGreater(pages, 3)

    def test_the_prompts_route(self):
        import asyncio
        import json
        from types import SimpleNamespace
        from urllib.parse import quote

        from navin.webui.ws_http import GatewayHTTPHandler

        tr.append_transcript_object(self.key, {"event": "user", "chat_id": "c", "text": "/forge ship it"})
        tr.append_transcript_object(self.key, {"event": "turn_end", "chat_id": "c"})
        handler = object.__new__(GatewayHTTPHandler)
        handler.check_api_token = lambda request: request.authorized

        def call(key: str, *, authorized: bool = True):
            path = f"/api/sessions/{quote(key, safe='')}/webui-prompts"
            request = SimpleNamespace(path=path, headers={}, authorized=authorized)
            response = asyncio.run(handler._dispatch_session_routes(request, path))
            body = json.loads(response.body) if response.status_code == 200 else None
            return response.status_code, body

        status, body = call(self.key)
        self.assertEqual(status, 200)
        self.assertEqual([(p["index"], p["text"]) for p in body["prompts"]], [(0, "/forge ship it")])
        self.assertEqual(call(self.key, authorized=False)[0], 401)
        self.assertEqual(call("cli:direct")[0], 404)
        self.assertEqual(call("websocket:empty")[1]["prompts"], [])


if __name__ == "__main__":
    unittest.main()
