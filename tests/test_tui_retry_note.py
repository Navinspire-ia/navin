# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The retry-wait note must not outlive the turn it describes.

A "Connection interrupted; resuming automatically" note describes a pause.
Once the recovered turn starts again, the note is gone instead of reading as
if the connection were still broken while the work already resumed.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from navin.bus.queue import MessageBus
from navin.tui.app import NavinApp
from navin.tui.prefs import TuiPrefs
from navin.tui.runtime import UiRetryWait, UiTurnStarted
from navin.tui.widgets import Sidebar, SystemNote, Transcript


class InteractionHost(NavinApp):
    async def on_mount(self, event):
        event.prevent_default()
        self.runtime.bus = MessageBus()
        self._engine_ready = True
        self.query_one(Sidebar).display = False

    async def on_unmount(self, event):
        event.prevent_default()
        self.runtime._closed = True

    def _refresh_side(self):
        self._set_status()

    def _load_account(self, *args, **kwargs):
        pass


class RetryWaitNoteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        prefs = TuiPrefs(sidebar=False, mode="chat", mode_explicit=True)
        prefs.save = lambda: None
        self.app = InteractionHost(
            SimpleNamespace(workspace_path=Path(directory.name)), prefs=prefs
        )

    async def test_retry_note_is_removed_when_the_turn_starts_again(self):
        app = self.app
        async with app.run_test(size=(100, 32)) as pilot:
            await app._on_runtime_event(UiRetryWait(text="Connection interrupted. Progress saved; resuming automatically in 5s."))
            await pilot.pause()
            transcript = app.query_one(Transcript)
            notes = [w for w in transcript.walk_children() if isinstance(w, SystemNote)]
            self.assertEqual(len(notes), 1)
            tracked = app._retry_wait_note
            self.assertIsNotNone(tracked)

            await app._on_runtime_event(UiTurnStarted(text="working"))
            await pilot.pause()
            # The note must be gone (or at least invisible) once the turn runs.
            self.assertFalse(tracked.display)

    async def test_turn_start_without_a_retry_note_is_a_no_op(self):
        app = self.app
        async with app.run_test(size=(100, 32)) as pilot:
            await app._on_runtime_event(UiTurnStarted(text="working"))
            await pilot.pause()
            self.assertIsNone(getattr(app, "_retry_wait_note", None))


if __name__ == "__main__":
    unittest.main()
