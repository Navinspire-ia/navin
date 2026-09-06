"""Buffered transcript appends must never lose or reorder a record.

Streaming appends one record per token. Those records are batched instead of
being written (and fsynced) one at a time, so the guarantees that matter are:
a reader always sees every appended record, order is preserved, and a deleted
transcript does not come back from a pending buffer.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.webui import transcript as tr


class BufferedAppendTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="navin-transcript-")
        self.root = Path(self._tmp.name)
        patcher = mock.patch.object(tr, "get_webui_dir", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(tr._flush_all_append_buffers)
        tr._append_buffers.clear()
        self.key = "websocket:chat-buffer"

    def _events(self) -> list[dict]:
        return tr.read_transcript_lines(self.key)

    def test_reader_sees_buffered_deltas(self):
        for i in range(5):
            tr.append_transcript_object(self.key, {"event": "delta", "text": str(i)})
        # Still buffered: the file itself has not been touched yet.
        self.assertFalse(tr.webui_transcript_path(self.key).is_file())
        # The read is a durability point, so nothing may be missing.
        self.assertEqual([e["text"] for e in self._events()], ["0", "1", "2", "3", "4"])

    def test_non_delta_event_flushes_and_keeps_order(self):
        tr.append_transcript_object(self.key, {"event": "delta", "text": "a"})
        tr.append_transcript_object(self.key, {"event": "delta", "text": "b"})
        tr.append_transcript_object(self.key, {"event": "stream_end", "text": "done"})
        events = self._events()
        self.assertEqual(
            [(e["event"], e["text"]) for e in events],
            [("delta", "a"), ("delta", "b"), ("stream_end", "done")],
        )

    def test_buffer_flushes_when_full(self):
        chunk = "x" * 4096
        for _ in range(40):
            tr.append_transcript_object(self.key, {"event": "delta", "text": chunk})
        # Well past _APPEND_BUFFER_MAX_BYTES, so the batch reached the disk
        # without anyone reading first.
        self.assertTrue(tr.webui_transcript_path(self.key).is_file())
        self.assertEqual(len(self._events()), 40)

    def test_delete_discards_pending_buffer(self):
        tr.append_transcript_object(self.key, {"event": "delta", "text": "ghost"})
        tr.delete_webui_transcript(self.key)
        self.assertEqual(self._events(), [])

    def test_appends_are_not_deep_copied_but_records_are_stable(self):
        payload = {"event": "delta", "text": "one", "nested": {"k": "v"}}
        tr.append_transcript_object(self.key, payload)
        # Mutating the caller's dict afterwards must not rewrite history.
        payload["nested"]["k"] = "mutated"
        payload["text"] = "two"
        stored = self._events()[0]
        self.assertEqual(stored["text"], "one")
        self.assertEqual(stored["nested"], {"k": "v"})


if __name__ == "__main__":
    unittest.main()
