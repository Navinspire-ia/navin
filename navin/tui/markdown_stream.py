# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Incremental transcript rendering without application-wide paint locks."""

from __future__ import annotations

import asyncio

from markdown_it import MarkdownIt
from textual.await_complete import AwaitComplete
from textual.strip import Strip
from textual.widgets import Markdown
from textual.widgets._markdown import (
    MarkdownBullet,
    MarkdownBulletList,
    MarkdownHeader,
    MarkdownOrderedList,
    MarkdownParagraph,
    MarkdownTable,
    MarkdownTableCellContents,
    MarkdownTableContent,
)

_COLLECTIONS = (MarkdownBulletList, MarkdownOrderedList, MarkdownTable)


def _token_key(token):
    # Source coordinates change as a stream grows; rendered content is the key.
    return (token.type, token.tag, token.markup, token.info, token.content,
            tuple(token.attrs.items()), tuple(_token_key(child) for child in token.children or ()))


def _collection(tokens):
    """Find direct list items or table body rows before constructing widgets."""
    root = tokens[0]
    if root.type not in {"bullet_list_open", "ordered_list_open", "table_open"}:
        return None
    table = root.type == "table_open"
    row_type, level = ("tr_open", 2) if table else ("list_item_open", 1)
    body = not table
    start = None
    spans = []
    keys = []
    header = [_token_key(root)]
    for index, token in enumerate(tokens[1:], 1):
        if token.level == 0:
            break
        if table and token.type == "tbody_open":
            body = True
        if not body:
            header.append(_token_key(token))
        if body and token.type == row_type and token.level == level:
            start = index
        elif start is not None and token.type == row_type.replace("_open", "_close") and token.level == level:
            spans.append((start, index + 1))
            keys.append(tuple(_token_key(part) for part in tokens[start:index + 1]))
            start = None
    return tuple(header), keys, spans


async def _mount_batches(parent, widgets) -> None:
    for index in range(0, len(widgets), 16):
        await parent.mount_all(widgets[index:index + 16])
        await asyncio.sleep(0)


async def _patch_collection(existing, replacement, keep: int) -> None:
    """Keep rendered rows stable; only the unfinished tail can change."""
    if isinstance(existing, MarkdownTable):
        content = existing.query_one(MarkdownTableContent)
        headers, added = replacement._get_headers_and_rows()
        rows = existing._rows[:keep] + added
        for index in range(keep, len(existing._rows)):
            await content.query_children(f".cell.row{index + 1}").remove()
        cells = [MarkdownTableCellContents(cell, classes=f"row{index} cell").with_tooltip(cell.plain)
                 for index, row in enumerate(added, keep + 1) for cell in row]
        await _mount_batches(content, cells)
        existing._headers, existing._rows = headers, rows
        content.headers, content.rows, content.last_row = headers.copy(), rows.copy(), len(rows)
    else:
        await existing.remove_children(list(existing.children)[keep:])
        start = int(replacement._token.attrGet("start") or 1)
        rows = list(replacement.compose()) if replacement._blocks else []
        if isinstance(existing, MarkdownOrderedList):
            width = len(f"{start + keep + len(rows) - 1}. ") + 1
            for index, row in enumerate([*existing.children, *rows], start):
                bullet = row.children[0] if row.is_mounted else row._pending_children[0]
                if isinstance(bullet, MarkdownBullet):
                    bullet.symbol = f"{index}. ".rjust(width)
                    bullet.refresh(layout=True)
        await _mount_batches(existing, rows)
    existing._copy_context(replacement)


class TranscriptMarkdown(Markdown):
    """Parse the unfinished block; reuse earlier blocks and collection rows."""

    def render_line(self, y: int) -> Strip:
        # outer_size is the last layout; size would rebuild the full map.
        width = self.outer_size.width - self.styles.gutter.width
        return Strip.blank(max(0, width), self.visual_style.rich_style)

    def _parser(self):
        return MarkdownIt("gfm-like") if self._parser_factory is None else self._parser_factory()

    def _blocks_with_rows(self, tokens, offset=0):
        collections = {id(token): _collection(tokens[index:]) for index, token in enumerate(tokens)
                       if token.level == 0 and token.type in {"bullet_list_open", "ordered_list_open", "table_open"}}
        if offset:
            for token in tokens:
                if token.map is not None:
                    token.map = [line + offset for line in token.map]
        for block in self._parse_markdown(tokens):
            block._stream_rows = collections.get(id(block._token))
            yield block

    def update(self, markdown: str) -> AwaitComplete:
        async def update_document():
            async with self.lock:
                self._theme = self.app.theme
                self._markdown = markdown
                parser = self._parser()
                tokens = await asyncio.to_thread(parser.parse, markdown)
                await self.remove_children()
                batch = []
                for block in self._blocks_with_rows(tokens):
                    batch.append(block)
                    if len(batch) == 16:
                        await _mount_batches(self, batch)
                        batch = []
                await _mount_batches(self, batch)
                self._last_parsed_line = self.children[-1].source_range[0] if self.children else 0
                self._table_of_contents = None
                self.post_message(Markdown.TableOfContentsUpdated(self, self.table_of_contents))
        return AwaitComplete(update_document())

    def append(self, markdown: str) -> AwaitComplete:
        async def append_document():
            async with self.lock:
                last = self.children[-1] if self.children else None
                start_line = last.source_range[0] if last is not None else 0
                self._markdown = self.source + markdown
                fragment = "".join(self.source.splitlines(keepends=True)[start_line:])
                parser = self._parser()
                tokens = (await asyncio.to_thread(parser.parse, fragment) if len(fragment) > 16000
                          else parser.parse(fragment))
                if not tokens:
                    return
                collection = _collection(tokens)
                previous = getattr(last, "_stream_rows", None)
                keep = None
                if collection and previous and previous[0] == collection[0]:
                    keep = 0
                    for old, new in zip(previous[1], collection[1]):
                        if old != new:
                            break
                        keep += 1
                    if keep:
                        first, _ = collection[2][0]
                        _, end = collection[2][keep - 1]
                        tokens = tokens[:first] + tokens[end:]
                blocks = list(self._blocks_with_rows(tokens, start_line))
                if last is not None and blocks:
                    replacement = blocks.pop(0)
                    if keep is not None and isinstance(last, _COLLECTIONS) and type(last) is type(replacement):
                        await _patch_collection(last, replacement, keep)
                        last.source_range = replacement.source_range
                        last._stream_rows = collection
                    elif isinstance(last, MarkdownParagraph) and isinstance(replacement, MarkdownParagraph):
                        if not last._content.is_same(replacement._content):
                            await last._update_from_block(replacement)
                        last.source_range = replacement.source_range
                        last._copy_context(replacement)
                    elif type(last) is type(replacement) and not isinstance(last, _COLLECTIONS):
                        await last._update_from_block(replacement)
                        if last.is_attached:
                            last.source_range = replacement.source_range
                    else:
                        await last.remove()
                        await self.mount(replacement)
                await _mount_batches(self, blocks)
                self._last_parsed_line = self.children[-1].source_range[0] if self.children else 0
                if any(isinstance(block, MarkdownHeader) for block in self.children[-len(blocks) - 1:]):
                    self._table_of_contents = None
                    self.post_message(Markdown.TableOfContentsUpdated(self, self.table_of_contents))
        return AwaitComplete(append_document())
