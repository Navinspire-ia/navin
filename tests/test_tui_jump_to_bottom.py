# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Scrolling up shows "Jump to bottom"; one click returns to the latest messages."""

from __future__ import annotations

import unittest

from textual.app import App, ComposeResult
from textual.widgets import Static

from navin.tui.widgets import JumpToBottom, Transcript


class Host(App):
    def compose(self) -> ComposeResult:
        yield Transcript(id="transcript")
        yield JumpToBottom(id="jump-bottom")

    def on_transcript_follow_changed(self, event: Transcript.FollowChanged) -> None:
        self.query_one(JumpToBottom).show(not event.following, "↓ Jump to bottom")

    def on_jump_to_bottom_pressed(self, _event: JumpToBottom.Pressed) -> None:
        transcript = self.query_one(Transcript)
        transcript.scroll_end(animate=False)
        transcript.auto_follow = True


class JumpToBottomTest(unittest.IsolatedAsyncioTestCase):
    async def test_scrolling_up_offers_the_jump_and_a_click_returns_to_the_tail(self):
        app = Host()
        async with app.run_test(size=(60, 12)) as pilot:
            transcript = app.query_one(Transcript)
            for index in range(60):
                await transcript.mount(Static(f"line {index}"))
            await pilot.pause()
            jump = app.query_one(JumpToBottom)
            self.assertFalse(jump.has_class("-visible"))

            transcript.scroll_to(y=0, animate=False)
            await pilot.pause()
            self.assertFalse(transcript.auto_follow)
            self.assertTrue(jump.has_class("-visible"))

            await pilot.click(JumpToBottom)
            await pilot.pause()
            self.assertTrue(transcript.auto_follow)
            self.assertTrue(transcript.is_vertical_scroll_end)
            self.assertFalse(jump.has_class("-visible"))


class LiveChipHost(App):
    calls = 0

    def compose(self) -> ComposeResult:
        from navin.tui.widgets import ComposerMeta

        yield ComposerMeta(id="composer-meta")

    def action_toggle_live(self) -> None:
        type(self).calls += 1


class LiveChipTest(unittest.IsolatedAsyncioTestCase):
    async def test_live_comes_first_and_a_click_starts_the_call(self):

        from unittest.mock import patch

        from navin.tui import live_voice

        app = LiveChipHost()
        with patch.object(live_voice, "CLI_LIVE_ENABLED", True):
            await self._run(app)

    async def _run(self, app: LiveChipHost) -> None:
        from navin.tui.widgets import ComposerMeta

        async with app.run_test(size=(80, 4)) as pilot:
            meta = app.query_one(ComposerMeta)
            meta.set_meta(mode="Agent", model="glm-5.3-flash")
            await pilot.pause()
            first = next(iter(meta.children))
            self.assertEqual(first.id, "meta-live")
            self.assertEqual(str(first.content), "◉ Live")
            await pilot.click("#meta-live")
            await pilot.pause()
            self.assertEqual(LiveChipHost.calls, 1)
            meta.set_live(True)
            await pilot.pause()
            live = app.query_one("#meta-live", Static)
            self.assertEqual(str(live.content), "● Live")
            self.assertTrue(live.has_class("-on"))
