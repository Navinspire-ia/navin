# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A finished Markdown answer drawn by one widget.

Textual's ``Markdown`` builds a widget per paragraph, list row, table cell
and code fence: 27 to 300 widgets for one answer. That is right while the
answer streams (only the unfinished tail changes), but a finished answer
never changes again. When the chat brings an old turn back into view, its
answer is rendered here instead: the same blocks, colors and margins as
the live answer, laid out as text lines for the current width and cached.
Selection, copy and links keep working (it is plain selectable content).
"""

from __future__ import annotations

from functools import lru_cache

from markdown_it import MarkdownIt
from markdown_it.token import Token
from rich.cells import cell_len
from textual.content import Content, Span
from textual.geometry import Size
from textual.style import Style
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets._markdown import MarkdownBlock, MarkdownFence

from navin.tui.copy_chip import COPIED_LABEL, COPIED_SECONDS, COPY_LABEL

BULLETS = ["• ", "▪ ", "‣ ", "⭑ ", "◦ "]

# (top margin, lines, bottom margin); margins of neighbors collapse.
Block = tuple[int, list[Content], int]


@lru_cache(maxsize=1)
def _parser() -> MarkdownIt:
    return MarkdownIt("gfm-like")


def _inline(token: Token | None) -> Content:
    if token is None or token.type != "inline":
        return Content("")
    # The installed path/status accents patch this method (navin.tui.markdown),
    # so inline code, paths, commands and outcome words match the live answer.
    content = MarkdownBlock._token_to_content(None, token)  # type: ignore[arg-type]
    links = [
        Span(span.start, span.end, ".link") for span in content.spans
        if not isinstance(span.style, str) and "@click" in (getattr(span.style, "meta", None) or {})
    ]
    return content.add_spans(links) if links else content


def _wrap(content: Content, width: int) -> list[Content]:
    width = max(1, width)
    lines: list[Content] = []
    for part in content.split("\n", allow_blank=True) if "\n" in content.plain else [content]:
        if not part.plain:
            lines.append(Content(""))
            continue
        lines.extend(part.wrap(width, overflow="fold") or [Content("")])
    return lines or [Content("")]


def _join(blocks: list[Block], *, outer: bool) -> tuple[int, list[Content], int]:
    """Stack blocks, collapsing adjacent margins like a vertical layout."""
    if not blocks:
        return 0, [], 0
    lines: list[Content] = []
    first_top = blocks[0][0]
    previous_bottom: int | None = None
    for top, body, bottom in blocks:
        if previous_bottom is not None:
            lines.extend(Content("") for _ in range(max(previous_bottom, top)))
        lines.extend(body)
        previous_bottom = bottom
    last_bottom = blocks[-1][2]
    if outer:
        return 0, [*(Content("") for _ in range(first_top)), *lines,
                   *(Content("") for _ in range(last_bottom))], 0
    return first_top, lines, last_bottom


class _Renderer:
    def __init__(self, tokens: list[Token], *, copied: int | None = None) -> None:
        self.tokens = tokens
        # Token position of the fence whose icon reads "Copied" just now.
        self.copied = copied

    def render(self, width: int) -> list[Content]:
        blocks, _ = self._blocks(0, len(self.tokens), width, list_depth=0, nested=False)
        return _join(blocks, outer=True)[1]

    # Each _blocks call consumes tokens [start, end) at one nesting level.
    def _blocks(self, start: int, end: int, width: int, *, list_depth: int,
                nested: bool, in_list_item: bool = False) -> tuple[list[Block], int]:
        tokens = self.tokens
        blocks: list[Block] = []
        index = start
        while index < end:
            token = tokens[index]
            kind = token.type
            if kind == "paragraph_open":
                close = self._close(index)
                body = _wrap(_inline(tokens[index + 1] if index + 1 < close else None), width)
                blocks.append((0, body, 0 if in_list_item else 1))
                index = close + 1
            elif kind == "heading_open":
                close = self._close(index)
                level = int(token.tag[1:]) if token.tag[1:].isdigit() else 1
                text = _inline(tokens[index + 1] if index + 1 < close else None)
                style = f".h{min(level, 4)}"
                lines = [line.stylize_before(style) for line in _wrap(text, width)]
                if level == 1:
                    lines = [line.center(width) for line in lines]
                blocks.append((0 if level <= 2 else 1, lines, 1))
                index = close + 1
            elif kind in {"bullet_list_open", "ordered_list_open"}:
                close = self._close(index)
                lines = self._list(index, close, width, list_depth)
                blocks.append((0, lines, 0 if nested else 1))
                index = close + 1
            elif kind in {"fence", "code_block"}:
                blocks.append((0, self._fence(index, token, width), 1))
                index += 1
            elif kind == "table_open":
                close = self._close(index)
                blocks.append((0, self._table(index, close, width), 1))
                index = close + 1
            elif kind == "blockquote_open":
                close = self._close(index)
                inner, _ = self._blocks(index + 1, close, max(1, width - 3), list_depth=list_depth, nested=True)
                _top, body, _bottom = _join(inner, outer=False)
                bar = Content.styled("▌ ", ".quote_bar")
                lines = [bar.append(line.stylize_before(".quote")) for line in body] or [bar]
                blocks.append((1, lines, 1))
                index = close + 1
            elif kind == "hr":
                blocks.append((0, [Content(""), Content.styled("─" * max(1, width), ".rule")], 1))
                index += 1
            elif kind in {"html_block"}:
                blocks.append((0, _wrap(Content(token.content.rstrip("\n")), width), 1))
                index += 1
            elif kind.endswith("_open"):
                index = self._close(index) + 1
            else:
                index += 1
        return blocks, index

    def _close(self, index: int) -> int:
        """Index of the closing token matching the opening token at ``index``."""
        tokens = self.tokens
        opening = tokens[index]
        if opening.nesting != 1:
            return index
        depth = 0
        for position in range(index, len(tokens)):
            nesting = tokens[position].nesting
            depth += nesting
            if depth == 0:
                return position
        return len(tokens) - 1

    def _list(self, start: int, close: int, width: int, depth: int) -> list[Content]:
        tokens = self.tokens
        ordered = tokens[start].type == "ordered_list_open"
        items: list[tuple[int, int, str]] = []
        index = start + 1
        while index < close:
            if tokens[index].type == "list_item_open":
                item_close = self._close(index)
                items.append((index, item_close, tokens[index].info))
                index = item_close + 1
            else:
                index += 1
        if not items:
            return []
        if ordered:
            try:
                first = int(items[0][2] or 1)
            except ValueError:
                first = 1
            size = max(len(f"{number}. ") for number in range(first, first + len(items)))
            symbols = [f"{number}. ".rjust(size + 1) for number in range(first, first + len(items))]
        else:
            symbols = [BULLETS[depth % len(BULLETS)]] * len(items)
        lines: list[Content] = []
        for (item_start, item_close, _info), symbol in zip(items, symbols):
            bullet_width = cell_len(symbol)
            inner_width = max(1, width - bullet_width)
            inner, _ = self._blocks(
                item_start + 1, item_close, inner_width,
                list_depth=depth if ordered else depth + 1, nested=True, in_list_item=True,
            )
            _top, body, _bottom = _join(inner, outer=False)
            body = body or [Content("")]
            bullet = Content.styled(symbol, ".bullet")
            pad = Content(" " * bullet_width)
            lines.append(bullet.append(body[0]))
            lines.extend(pad.append(line) for line in body[1:])
        return lines

    def _fence(self, position: int, token: Token, width: int) -> list[Content]:
        code = token.content.rstrip()
        highlighted = MarkdownFence.highlight(code, token.info.strip().split(" ")[0] if token.info else "")
        # Live fences: panel background, the copy icon on the top row, one
        # blank row below, the code indented three cells (the fence's
        # padding plus its label's).
        inner = max(1, width - 4)
        rows: list[Content] = []
        for line in highlighted.split("\n", allow_blank=True):
            rows.extend(line.fold(inner) if line.cell_length > inner else [line])
        rows.append(Content(""))
        out = [self._fence_copy_row(position, width)]
        for row in rows:
            padded = Content("   ").append(row)
            padded = padded.pad_right(max(0, width - padded.cell_length))
            out.append(padded.stylize_before(".fence"))
        return out

    def _fence_copy_row(self, position: int, width: int) -> Content:
        label = COPIED_LABEL if position == self.copied else COPY_LABEL
        if width < cell_len(label) + 4:
            return Content(" " * width).stylize_before(".fence")
        # Right-aligned two cells from the edge, like the live fence's icon.
        chip = Content(label).stylize(".fence_copied" if position == self.copied else ".fence_copy")
        chip = chip.stylize(Style.from_meta({"@click": f"copy_fence({position})"}))
        row = Content(" " * (width - cell_len(label) - 2)).append(chip).append(Content("  "))
        return row.stylize_before(".fence")

    def _table(self, start: int, close: int, width: int) -> list[Content]:
        tokens = self.tokens
        header: list[Content] = []
        rows: list[list[Content]] = []
        current: list[Content] | None = None
        in_head = False
        for index in range(start, close):
            token = tokens[index]
            if token.type == "thead_open":
                in_head = True
            elif token.type == "thead_close":
                in_head = False
            elif token.type == "tr_open":
                current = []
            elif token.type == "tr_close" and current is not None:
                if in_head:
                    header = current
                else:
                    rows.append(current)
                current = None
            elif token.type in {"th_open", "td_open"} and current is not None:
                inline = tokens[index + 1] if index + 1 < close else None
                current.append(_inline(inline))
        columns = max([len(header), *(len(row) for row in rows)] or [0])
        if not columns:
            return []
        grid = [header + [Content("")] * (columns - len(header))] if header else []
        grid += [row + [Content("")] * (columns - len(row)) for row in rows]
        widths = [max(cell_len(row[column].plain) for row in grid) for column in range(columns)]
        # Each cell has one cell of padding on both sides, plus the rules.
        available = max(columns, width - (3 * columns + 1))
        while sum(widths) > available and max(widths) > 1:
            widest = widths.index(max(widths))
            widths[widest] -= 1
        line_style = ".table_lines"

        def rule(left: str, middle: str, right: str) -> Content:
            return Content.styled(left + middle.join("─" * (size + 2) for size in widths) + right, line_style)

        def row_line(cells: list[Content], *, head: bool) -> Content:
            parts = [Content.styled("│", line_style)]
            for cell, size in zip(cells, widths):
                text = cell.truncate(size, ellipsis=True) if cell.cell_length > size else cell
                text = text.pad_right(size - text.cell_length)
                body = Content(" ").append(text).append(Content(" "))
                if head:
                    body = body.stylize_before(".table_header")
                parts.append(body)
                parts.append(Content.styled("│", line_style))
            out = Content("")
            for part in parts:
                out = out.append(part)
            return out

        lines = [rule("┌", "┬", "┐")]
        for position, cells in enumerate(grid):
            head = bool(header) and position == 0
            lines.append(row_line(cells, head=head))
            if head:
                lines.append(rule("├", "┼", "┤"))
        lines.append(rule("└", "┴", "┘"))
        return lines


def render_markdown_lines(markdown: str, width: int, *, copied_fence: int | None = None) -> list[Content]:
    """Lay out ``markdown`` as styled lines no wider than ``width``.

    ``copied_fence`` is the token position of a fence whose copy icon shows
    its "Copied" state.
    """
    if not markdown.strip():
        return []
    tokens = _parser().parse(markdown)
    return _Renderer(tokens, copied=copied_fence).render(max(1, width))


def fence_code(markdown: str, position: int) -> str:
    """The code of the fence at token ``position`` (as its copy icon names it)."""
    tokens = _parser().parse(markdown)
    if 0 <= position < len(tokens) and tokens[position].type in {"fence", "code_block"}:
        return tokens[position].content.rstrip()
    return ""


class SettledMarkdown(Widget):
    """A finished answer (or rich narration) as one selectable widget."""

    ALLOW_SELECT = True

    COMPONENT_CLASSES = {
        "em", "strong", "s", "code_inline", "code_path", "code_command",
        "status_ok", "status_fail", "status_warn",
        "h1", "h2", "h3", "h4", "bullet", "fence", "fence_copy", "fence_copied", "table_header", "table_lines",
        "quote", "quote_bar", "rule", "link",
    }

    DEFAULT_CSS = """
    SettledMarkdown {
        height: auto;
        width: 1fr;
        color: $foreground;
        background: $background;
    }
    SettledMarkdown > .strong { color: #5EA8FF; text-style: bold; }
    SettledMarkdown:light > .strong { color: #3D82FF; }
    SettledMarkdown > .em { text-style: italic; }
    SettledMarkdown > .s { text-style: strike; }
    SettledMarkdown > .code_inline { color: #C6AFE3; }
    SettledMarkdown:light > .code_inline { color: #705393; }
    SettledMarkdown > .code_path { color: #8FBC8F; }
    SettledMarkdown:light > .code_path { color: #2D6A4F; }
    SettledMarkdown > .code_command { color: #D7BA7D; }
    SettledMarkdown:light > .code_command { color: #805B20; }
    SettledMarkdown > .status_ok { color: #8FBC8F; text-style: bold; }
    SettledMarkdown:light > .status_ok { color: #2D6A4F; }
    SettledMarkdown > .status_fail { color: #E39B91; text-style: bold; }
    SettledMarkdown:light > .status_fail { color: #914747; }
    SettledMarkdown > .status_warn { color: #D6BC78; text-style: bold; }
    SettledMarkdown:light > .status_warn { color: #795B13; }
    SettledMarkdown > .h1, SettledMarkdown > .h2 { color: $primary; text-style: bold; }
    SettledMarkdown > .h3 { color: $accent; text-style: bold; }
    SettledMarkdown > .h4 { text-style: bold; }
    SettledMarkdown > .bullet { color: $text-muted; }
    SettledMarkdown > .fence { color: $foreground; background: $panel; }
    SettledMarkdown > .fence_copy { color: $text-muted; }
    SettledMarkdown > .fence_copied { color: $success; }
    SettledMarkdown > .table_header { color: $primary; background: $panel; text-style: bold; }
    SettledMarkdown > .table_lines { color: $foreground 20%; }
    SettledMarkdown > .quote { color: $text-muted; }
    SettledMarkdown > .quote_bar { color: $primary 50%; }
    SettledMarkdown > .rule { color: $secondary; }
    SettledMarkdown > .link { color: $accent; text-style: underline; }
    """

    def __init__(self, markdown: str, *, classes: str | None = None) -> None:
        super().__init__(classes=classes)
        self.markdown = markdown
        self._rendered: tuple[int, Content, int] | None = None
        self._copied: int | None = None
        self._copied_timer: Timer | None = None

    @property
    def source(self) -> str:
        return self.markdown

    def _content(self, width: int) -> tuple[Content, int]:
        width = max(1, width)
        cached = self._rendered
        if cached is not None and cached[0] == width:
            return cached[1], cached[2]
        lines = render_markdown_lines(self.markdown, width, copied_fence=self._copied)
        content = Content("\n").join(lines) if lines else Content("")
        self._rendered = (width, content, len(lines))
        return content, len(lines)

    def get_content_width(self, container: Size, viewport: Size) -> int:
        return container.width

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return self._content(width)[1]

    def render(self) -> Content:
        width = self.outer_size.width - self.styles.gutter.width
        return self._content(width if width > 0 else self.size.width)[0]

    async def action_link(self, href: str) -> None:
        self.app.open_url(href)

    def copy_text(self) -> str:
        return self.markdown

    def action_copy_fence(self, position: int) -> None:
        """The copy icon of a code block: copy that block's code only."""
        code = fence_code(self.markdown, position)
        if not code.strip():
            return
        self.app.copy_to_clipboard(code)
        self._show_copied(position)

    def _show_copied(self, position: int | None) -> None:
        if self._copied_timer is not None:
            self._copied_timer.stop()
            self._copied_timer = None
        self._copied = position
        # Same lines, one label changed: repaint without a new layout.
        self._rendered = None
        self.refresh()
        if position is not None:
            self._copied_timer = self.set_timer(COPIED_SECONDS, lambda: self._show_copied(None))

