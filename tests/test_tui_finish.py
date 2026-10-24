# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Completion boundaries and prompt editing through actual terminal widgets."""

import asyncio

import pytest
from textual import on
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Markdown
from textual.widgets._markdown import (
    MarkdownFence,
    MarkdownH2,
    MarkdownH3,
    MarkdownParagraph,
    MarkdownTable,
)

from navin.agent.code_validation import CodeValidationState
from navin.tui.copy_chip import COPIED_LABEL, COPY_LABEL, CopyChip
from navin.tui.markdown_stream import CopyableFence
from navin.tui.settled import SettledMarkdown, render_markdown_lines
from navin.tui.theme import NAVIN_THEMES
from navin.tui.widgets import AssistantMessage, Composer, ComposerMeta, ComposerShell


class FinishHost(App):
    def __init__(self, theme="navin"):
        super().__init__()
        self.block = AssistantMessage()
        self.prefixes = []
        self.submitted = []
        for registered in NAVIN_THEMES:
            self.register_theme(registered)
        self.theme = theme

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield self.block
        with ComposerShell():
            yield Composer(placeholder="Ask anything...")
            yield ComposerMeta()

    @on(Composer.SlashTyping)
    def slash(self, event):
        self.prefixes.append(event.prefix)

    @on(Composer.Submitted)
    def submit(self, event):
        self.submitted.append(event.text)


@pytest.mark.parametrize("theme,width", [("navin", 100), ("navin-light", 48)])
@pytest.mark.parametrize("saved_legacy_message", [False, True])
def test_validation_finish_is_structured_colored_and_copyable(theme, width, saved_legacy_message, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    async def run():
        state = CodeValidationState(revision=1, needs_tests=True, paths={
            "backend/tests/test_support_agent_disabled.py",
            "backend/gemini_live/support_ws_routes.py",
            "frontend/src/app/admin/tours/agent-runs/[runId]/page.tsx",
        })
        message = state.completion_message()
        if saved_legacy_message:
            message = (
                "The changes are saved, but the task is not validated. "
                "The agent repeatedly tried to finish without resolving these checks.\n\n"
                + state.missing()
            )
        app = FinishHost(theme)
        async with app.run_test(size=(width, 42)) as pilot:
            await app.block.set_text(message)
            await app.block.finish(latency_ms=10, model=None, preset=None)
            await pilot.pause()
            heading = app.block.query_one(MarkdownH2)
            table = app.block.query_one(MarkdownTable)
            command = app.block.query_one(MarkdownFence)
            assert app.block.has_class("-validation-pending")
            assert heading.styles.color != app.block.query_one(MarkdownParagraph).styles.color
            assert len(app.block.query(MarkdownH3)) == 3
            assert table.region.y > heading.region.y
            assert command.region.y > table.region.bottom
            assert table.region.right <= app.block.region.right
            assert command.query_one("#code-content").region.right <= command.region.right
            if width < 60:
                assert command.query_one("#code-content").content_size.height > 1
            assert [cell.content.plain for cell in table.query(".header, .cell")] == [
                "Item", "Status", "Changes", "Saved", "Tests", "No current result",
            ]
            source = app.block.query_one(Markdown).source
            assert "```bash\npython -m pytest backend/tests/test_support_agent_disabled.py\n```" in source
            assert "- `frontend/src/app/admin/tours/agent-runs/[runId]/page.tsx`" in source
            assert app.block.copy_text() == message
            composer = app.query_one(Composer)
            composer.focus()
            await pilot.press(*"Reprendre")
            assert composer.text == "Reprendre"
            await app.block.set_text("Validation completed.")
            assert not app.block.has_class("-validation-pending")
    asyncio.run(run())


@pytest.mark.parametrize("theme,width", [("navin", 92), ("navin-light", 48)])
def test_finish_shows_answer_directly_without_a_heading(theme, width):
    async def run():
        app = FinishHost(theme)
        async with app.run_test(size=(width, 28)) as pilot:
            block = app.block
            assert not block.query(".assistant-head, .assistant-finish")
            await block.tool_event("run", "exec", "end", {"command": "pytest -q"}, "2 passed", None, None)
            await block.delta("Validated. Run `npm install` for `src/app.ts`.")
            assert not block.query(".assistant-head, .assistant-finish")
            await block.finish(latency_ms=12, model=None, preset=None)
            await block.finish(latency_ms=12, model=None, preset=None)
            await pilot.pause()
            assert not block.query(".assistant-head, .assistant-finish")
            assert block.query_one(Markdown).display
            assert "finish" not in block.copy_text()
            assert "npm install" in block.copy_text()
            await block.delta(" Another detail.")
            assert not block.query(".assistant-head, .assistant-finish")
            await block.finish(latency_ms=12, model=None, preset=None)
            await block.set_text("Updated final answer.")
            assert not block.query(".assistant-head, .assistant-finish")
            assert block.text == "Updated final answer."
            await pilot.pause()
    asyncio.run(run(), debug=True)


def test_events_on_removed_block_do_not_crash():
    """Session switch removes transcript children; late events must be ignored."""
    async def run():
        app = FinishHost()
        async with app.run_test(size=(80, 24)) as pilot:
            block = app.block
            await block.set_text("Answer before the switch.")
            await pilot.pause()
            await block.remove()
            await pilot.pause()
            # The exact crash from the field report: NoMatches on a removed block.
            await block.tool_event("run", "exec", "end", {"command": "pytest"}, "ok", None, None)
            await block.note_file_edit("src/app.ts", 3, 1, diff="-a\n+b")
            await block.progress("working...")
            await block.subagent("t-1", "audit", "end", "done", None, 1, True, None)
            await block.delta(" more")
            await block.set_text("Late text.")
            await block.finish(latency_ms=5, model=None, preset=None)
            await pilot.pause()
    asyncio.run(run(), debug=True)


def test_finish_survives_missing_chrome_while_still_attached():
    """History replay can finish a block after session switch tore its children."""
    async def run():
        app = FinishHost()
        async with app.run_test(size=(80, 24)) as pilot:
            block = app.block
            await block.tool_event("run", "exec", "end", {"command": "pytest"}, "ok", None, None)
            await block.set_text("Answer before the switch.")
            await pilot.pause()
            for node in list(block.query(".assistant-foot, .assistant-preview")):
                await node.remove()
            await block.finish(latency_ms=5, model=None, preset=None)
            await pilot.pause()
    asyncio.run(run(), debug=True)


def test_ready_helpers_return_none_when_detached():
    async def run():
        app = FinishHost()
        async with app.run_test(size=(80, 24)) as pilot:
            block = app.block
            await pilot.pause()
            assert await block._ready_preview() is not None
            assert await block._ready_body() is not None
            await block.remove()
            await pilot.pause()
            assert await block._ready_preview() is None
            assert await block._ready_body() is None
    asyncio.run(run(), debug=True)


def test_typing_keeps_cursor_inside_prompt_without_rebuilding_slash_menu():
    async def run():
        app = FinishHost()
        async with app.run_test(size=(60, 24)) as pilot:
            composer = app.query_one(Composer)
            composer.focus()
            await pilot.press(*"Bonjour tout le monde")
            assert composer.text == "Bonjour tout le monde"
            assert app.prefixes == []
            shell = app.query_one(ComposerShell)
            assert composer.region.y > shell.region.y
            assert composer.region.bottom <= shell.region.bottom
            await pilot.press("ctrl+j", *"suite", "enter")
            assert app.submitted == ["Bonjour tout le monde\nsuite"]
            composer.clear_text()
            await pilot.press("/", "h", "e", "l", "p", "space", "x")
            assert app.prefixes == ["/", "/h", "/he", "/hel", "/help", None]
            assert composer.text == "/help x"
            await pilot.pause()
    asyncio.run(run(), debug=True)


def test_a_finished_answer_has_a_copy_icon_for_the_whole_report():
    report = "## Report\n\nAll **done**.\n\n- `src/app.ts` changed\n- tests pass"

    async def run():
        app = FinishHost()
        copied: list[str] = []
        app.copy_to_clipboard = copied.append
        async with app.run_test(size=(90, 40)) as pilot:
            block = app.block
            await block.tool_event("run", "exec", "end", {"command": "pytest -q"}, "2 passed", None, None)
            await block.delta(report)
            await pilot.pause()
            chip = block.query_one(".assistant-copy", CopyChip)
            # No icon while the answer is still being written.
            assert not chip.display
            await block.finish(latency_ms=1, model=None, preset=None)
            await pilot.pause()
            assert chip.display
            body = block.query_one(Markdown)
            assert chip.region.y >= body.region.bottom
            assert str(chip.content) == COPY_LABEL
            await pilot.click(chip)
            await pilot.pause()
            # The answer as written, not the activity rows above it.
            assert copied == [report]
            assert chip.has_class("-copied") and str(chip.content) == COPIED_LABEL
            # The click neither folded the answer nor selected text.
            assert body.display and not app.screen.get_selected_text()
            chip._reset()
            assert str(chip.content) == COPY_LABEL and not chip.has_class("-copied")

            # A turn that streams again loses the icon until it ends again.
            await block.delta("\n\nOne more line.")
            assert not chip.display
            await block.finish(latency_ms=1, model=None, preset=None)
            await pilot.pause()
            assert chip.display

            # Built back from its record (scrolled far away and back).
            record = block.snapshot_record()
            assert record is not None
            rebuilt = record.build()
            await app.query_one(VerticalScroll).mount(rebuilt)
            await pilot.pause()
            again = rebuilt.query_one(".assistant-copy", CopyChip)
            assert again.display
            assert again.copy()
            assert copied[-1] == report + "\n\nOne more line."

            # A turn with only tool rows has no answer to copy: no icon.
            tools_only = AssistantMessage()
            await app.query_one(VerticalScroll).mount(tools_only)
            await tools_only.tool_event("run", "exec", "end", {"command": "ls"}, "a\nb", None, None)
            await tools_only.finish(latency_ms=1, model=None, preset=None)
            await pilot.pause()
            assert not tools_only.query_one(".assistant-copy", CopyChip).display
    asyncio.run(run())


def test_streaming_and_finishing_a_long_turn_never_restyles_the_whole_message():
    # A class toggle on the message restyled every tool row and Markdown
    # block under it: up to a second per toggle in the CLI freeze log, and
    # 220 ms for the copy icon at every finish.
    async def run():
        app = FinishHost()
        async with app.run_test(size=(90, 40)) as pilot:
            block = app.block
            for index in range(25):
                await block.tool_event(
                    f"t{index}", "exec", "end", {"command": f"pytest -q t{index}"}, "ok\n" * 5, None, None
                )
            await pilot.pause()
            whole: list[int] = []
            original = app.stylesheet.update_nodes

            def watched(nodes, animate=False):
                nodes = list(nodes)
                if block in nodes:
                    whole.append(len(nodes))
                return original(nodes, animate=animate)

            app.stylesheet.update_nodes = watched
            await block.delta("Checking the last files.")
            await pilot.pause()
            assert block.has_class("-open")
            assert block.query_one(".assistant-body.-answer", Markdown).display
            await block.stream_end(resuming=True)
            assert not block.has_class("-open")
            report = "## Report\n\n" + "All **done**.\n\n" * 20
            await block.delta(report)
            await pilot.pause()
            chip = block.query_one(".assistant-copy", CopyChip)
            assert not chip.display
            await block.finish(latency_ms=1, model=None, preset=None)
            await pilot.pause()
            assert chip.display and block.has_class("-copyable")
            await block.delta("\n\nOne more line.")
            assert not chip.display
            block.toggle_body()
            block.toggle_body()
            await pilot.pause()
            assert len(list(block.walk_children())) > 100
            assert whole == [], f"whole message restyled {len(whole)} times"
    asyncio.run(run())


FENCED = (
    "Run this first:\n\n```bash\ngit push forgejo prod-v2\n```\n\n"
    "Then:\n\n- build\n\n  ```\n  make build\n  make check\n  ```\n"
)


def test_each_live_code_block_copies_only_its_own_code():
    async def run():
        app = FinishHost()
        copied: list[str] = []
        app.copy_to_clipboard = copied.append
        async with app.run_test(size=(80, 40)) as pilot:
            await app.block.delta(FENCED)
            await app.block.finish(latency_ms=1, model=None, preset=None)
            await pilot.pause()
            fences = list(app.block.query(CopyableFence))
            assert len(fences) == 2
            for fence, lines in zip(fences, (1, 2)):
                chip = fence.query_one(".fence-copy", CopyChip)
                code = fence.query_one("#code-content")
                # The icon sits on the top row, right-aligned, and the block
                # keeps the settled renderer's height: icon, code, blank row.
                assert chip.region.y == fence.region.y
                assert code.region.y == fence.region.y + 1
                assert fence.region.height == 1 + lines + 1
                # Two cells from the edge, like the settled renderer's icon.
                assert chip.region.right == fence.region.right - 2
                assert chip.region.width == len(COPY_LABEL)
            await pilot.click(fences[0].query_one(".fence-copy", CopyChip))
            await pilot.click(fences[1].query_one(".fence-copy", CopyChip))
            await pilot.pause()
            assert copied == ["git push forgejo prod-v2", "make build\nmake check"]
            # The rest of the icon row is not a copy target.
            await pilot.click(fences[0], offset=(1, 0))
            await pilot.pause()
            assert len(copied) == 2
            # The answer stays open under the clicks.
            assert app.block.query_one(Markdown).display
    asyncio.run(run())


def test_a_streamed_code_block_copies_the_code_it_shows_now():
    async def run():
        app = FinishHost()
        copied: list[str] = []
        app.copy_to_clipboard = copied.append
        async with app.run_test(size=(80, 30)) as pilot:
            await app.block.delta("```bash\nmake")
            await pilot.pause(0.3)
            await app.block.delta(" build\nmake check\n```\n")
            await app.block.finish(latency_ms=1, model=None, preset=None)
            await pilot.pause()
            fence = app.block.query_one(CopyableFence)
            assert fence.query_one(".fence-copy", CopyChip).copy()
            assert copied == ["make build\nmake check"]
    asyncio.run(run())


class SettledHost(App):
    def __init__(self, markdown: str):
        super().__init__()
        self.settled = SettledMarkdown(markdown)
        for registered in NAVIN_THEMES:
            self.register_theme(registered)
        self.theme = "navin"

    def compose(self) -> ComposeResult:
        yield self.settled


def test_each_settled_code_block_copies_only_its_own_code():
    async def run():
        app = SettledHost(FENCED)
        copied: list[str] = []
        app.copy_to_clipboard = copied.append
        async with app.run_test(size=(70, 30)) as pilot:
            await pilot.pause()
            width = app.settled.size.width
            rows = [line.plain for line in render_markdown_lines(FENCED, width)]
            icons = [(y, row.index(COPY_LABEL)) for y, row in enumerate(rows) if COPY_LABEL in row]
            assert len(icons) == 2
            # Right-aligned two cells from the edge, then the code below it.
            assert all(x == width - len(COPY_LABEL) - 2 for _y, x in icons)
            assert rows[icons[0][0] + 1].strip() == "git push forgejo prod-v2"
            await pilot.click(app.settled, offset=(icons[1][1] + 1, icons[1][0]))
            await pilot.pause()
            assert copied == ["make build\nmake check"]
            shown = app.settled.render().plain.split("\n")
            assert COPIED_LABEL in shown[icons[1][0]] and COPY_LABEL in shown[icons[0][0]]
            await pilot.click(app.settled, offset=(icons[0][1] + 1, icons[0][0]))
            await pilot.pause()
            assert copied[-1] == "git push forgejo prod-v2"
            # Text around the icons copies nothing.
            await pilot.click(app.settled, offset=(1, 0))
            await pilot.pause()
            assert len(copied) == 2
            app.settled._show_copied(None)
            assert app.settled.render().plain.count(COPY_LABEL) == 2
            # Narrow layouts drop the icon rather than overlap the code.
            narrow = render_markdown_lines(FENCED, 8)
            assert not any(COPY_LABEL in line.plain for line in narrow)
    asyncio.run(run())
