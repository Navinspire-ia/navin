# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Streaming Markdown preserves content and keeps the rest of the UI live."""

import asyncio

import pytest
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Markdown
from textual.widgets._markdown import (
    MarkdownBlock,
    MarkdownBullet,
    MarkdownFence,
    MarkdownTableCellContents,
)

from navin.tui.markdown_stream import TranscriptMarkdown
from navin.tui.widgets import AssistantMessage, Composer


class StreamHost(App):
    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield TranscriptMarkdown(id="stream")
            yield Markdown(id="reference")
            yield AssistantMessage()
        yield Composer()


def rendered_content(widget):
    result = []
    for node in widget.walk_children():
        if isinstance(node, MarkdownBullet):
            result.append((type(node).__name__, node.symbol))
        elif isinstance(node, MarkdownTableCellContents):
            result.append((type(node).__name__, node.content.plain))
        elif isinstance(node, MarkdownFence):
            result.append((type(node).__name__, node.query_one("#code-content").content.plain, node.source))
        elif isinstance(node, MarkdownBlock):
            result.append((type(node).__name__, node._content.plain, node.source))
    return result


@pytest.mark.parametrize("chunks", [
    ["- First\n- Sec", "ond\n", "  - Nested\n", "  - More\n", "- Last\n", "\n", "Done."],
    ["Preamble.\n\n", "- First\n", "  - Nested\n", "- Next\n", "\n", "Done."],
    ["8. Eight\n9. Nine\n", "10. Ten\n", "11. Eleven\n", "\n", "Done."],
    ["| Item | Result |\n", "| --- | --- |\n", "| One | O", "K |\n", "| Two | OK |\n", "\n", "Done."],
    ["### Title\n\n", "Text **par", "tial**.\n\n", "```python\n", "print(1)\n", "```\n\n", "Done."],
])
def test_stream_matches_complete_markdown(chunks):
    async def run():
        app = StreamHost()
        async with app.run_test(size=(100, 40)):
            stream = app.query_one("#stream", TranscriptMarkdown)
            reference = app.query_one("#reference", Markdown)
            source = ""
            for chunk in chunks:
                source += chunk
                await stream.append(chunk)
                await reference.update(source)
                assert stream.source == source
                assert rendered_content(stream) == rendered_content(reference)
            assert [entry[:2] for entry in stream.table_of_contents] == [entry[:2] for entry in reference.table_of_contents]
    asyncio.run(run())


@pytest.mark.parametrize("initial,more,selector", [
    ("- One\n- Two\n", "- Three\n", "MarkdownBulletList > Horizontal"),
    ("8. One\n9. Two\n", "10. Three\n", "MarkdownOrderedList > Horizontal"),
    ("| A | B |\n| --- | --- |\n| One | Two |\n", "| Three | Four |\n", ".cell.row1"),
])
def test_appending_rows_keeps_existing_widgets(initial, more, selector):
    async def run():
        app = StreamHost()
        async with app.run_test(size=(100, 40)):
            stream = app.query_one("#stream", TranscriptMarkdown)
            await stream.append(initial)
            existing = list(stream.query(selector))
            assert existing
            await stream.append(more)
            assert all(node.is_attached for node in existing)
            assert list(stream.query(selector))[:len(existing)] == existing
    asyncio.run(run())


def test_stream_mount_does_not_lock_paint_or_typing(monkeypatch):
    async def run():
        app = StreamHost()
        async with app.run_test(size=(100, 40)) as pilot:
            stream = app.query_one("#stream", TranscriptMarkdown)
            await stream.append("Paragraph.\n\n")
            entered, release = asyncio.Event(), asyncio.Event()
            original = stream.mount_all

            async def delayed_mount(*args, **kwargs):
                entered.set()
                await release.wait()
                await original(*args, **kwargs)

            monkeypatch.setattr(stream, "mount_all", delayed_mount)

            async def append():
                await stream.append("- New list row\n")

            pending = asyncio.create_task(append())
            await asyncio.wait_for(entered.wait(), 2)
            try:
                assert app._batch_count == 0
                composer = app.query_one(Composer)
                composer.focus()
                await pilot.press(*"Continue")
                assert composer.text == "Continue"
                assert not pending.done()
            finally:
                release.set()
                await pending
    asyncio.run(run())


def test_answer_is_visible_before_stream_finishes():
    async def run():
        app = StreamHost()
        async with app.run_test(size=(100, 40)) as pilot:
            block = app.query_one(AssistantMessage)
            await block.delta("First sentence. ")
            await block.delta("The next part must appear before completion.")
            await pilot.pause(0.12)
            markdown = block.query_one(TranscriptMarkdown)
            assert block.has_class("-open")
            assert markdown.visible and markdown.display and markdown.region.height > 0
            assert "before completion" in markdown.source
            assert block.copy_text() == block.text
    asyncio.run(run())
