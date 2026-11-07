# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""How the terminal shows a file the agent asked to preview.

The desktop File Preview panel renders HTML reports, PDFs and images. A
terminal cannot: an HTML report's source printed in a box was unreadable
(rows of ``<tr data-family=...>`` cut at the edge, then "... (truncated)").
Those files open in the user's browser or viewer instead. When nothing can be
opened (SSH, a server without a desktop), an HTML report is converted to
Markdown the terminal renders - headings, paragraphs, lists, links and
tables - rather than shown as tags. Markdown stays Markdown, and any other
text file is shown as code, highlighted for its language.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

# What a browser renders and a terminal cannot.
BROWSER_SUFFIXES = frozenset({".html", ".htm", ".xhtml", ".svg"})
# What the system viewer opens: a PDF reader, an image viewer.
VIEWER_SUFFIXES = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})
HTML_SUFFIXES = frozenset({".html", ".htm", ".xhtml"})
MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
MAX_PREVIEW_CHARS = 60_000

_FENCE_LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".sh": "bash",
    ".bash": "bash",
    ".sql": "sql",
    ".css": "css",
    ".xml": "xml",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
}


def opens_outside(path: Path) -> bool:
    """Whether the file belongs in the browser or the system viewer."""
    return path.suffix.lower() in BROWSER_SUFFIXES | VIEWER_SUFFIXES


def outside_app_name(path: Path) -> str:
    """What opened it, for the line that says so."""
    return "browser" if path.suffix.lower() in BROWSER_SUFFIXES else "default viewer"


def readable_in_terminal(path: Path) -> bool:
    """Whether the terminal can still show it when nothing opened outside."""
    return path.suffix.lower() in HTML_SUFFIXES


def terminal_markdown(path: Path, data: bytes, *, note: str = "") -> str | None:
    """The Markdown the terminal viewer shows for ``path``; None when binary.

    ``note`` says why an HTML file is shown here rather than in the browser.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    suffix = path.suffix.lower()
    if suffix in HTML_SUFFIXES:
        header = f"**{_escape(path.name)}** - shown here as text."
        if note.strip():
            header += " " + _escape(note.strip())
        body, cut = _cap(html_to_markdown(text) or "_This page has no readable text._")
        return f"{header}\n\n{_escape(str(path))}\n\n---\n\n{body}{cut}"
    if suffix in MARKDOWN_SUFFIXES:
        body, cut = _cap(text)
        return body + cut
    body, cut = _cap(text)
    fence = _fence(body)
    language = _FENCE_LANGUAGES.get(suffix, "")
    return f"**{_escape(str(path))}**\n\n{fence}{language}\n{body}\n{fence}{cut}"


def html_to_markdown(html: str) -> str:
    """Readable Markdown from an HTML page: its text, not its tags.

    Scripts, styles, forms and drawings are left out; tables become Markdown
    tables, lists stay lists, links keep their address.
    """
    writer = _MarkdownWriter()
    writer.feed(html or "")
    writer.close()
    return writer.markdown()


def _cap(text: str) -> tuple[str, str]:
    if len(text) <= MAX_PREVIEW_CHARS:
        return text, ""
    head = text[:MAX_PREVIEW_CHARS]
    newline = head.rfind("\n")
    if newline > MAX_PREVIEW_CHARS // 2:
        head = head[:newline]
    return head, "\n\n... (truncated)"


def _fence(text: str) -> str:
    """A code fence longer than any run of backticks in ``text``."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


_SPECIAL = re.compile(r"([\\`*_\[\]<>#])")
_SPACES = re.compile(r"\s+")


def _escape(text: str) -> str:
    """Plain text that Markdown shows as it is."""
    return _SPECIAL.sub(r"\\\1", text)


# Content that is not reading matter. Each one is closed explicitly in HTML.
_SKIPPED = frozenset({
    "script", "style", "noscript", "template", "svg", "canvas", "iframe",
    "object", "select", "textarea", "button",
})
_BLOCKS = frozenset({
    "p", "div", "section", "article", "header", "footer", "main", "nav", "aside",
    "figure", "figcaption", "blockquote", "form", "fieldset", "details", "summary",
    "dl", "dt", "dd", "address", "body",
})
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


class _MarkdownWriter(HTMLParser):
    """Streams an HTML page into Markdown, one tag at a time."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._title: list[str] = []
        self._has_h1 = False
        self._in_title = False
        self._skip_tag = ""
        self._skip_depth = 0
        self._heading = False
        self._lists: list[list] = []
        self._link: tuple[str, list[str]] | None = None
        self._pre: list[str] | None = None
        self._table_depth = 0
        self._rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._caption: list[str] | None = None
        self._caption_text = ""

    # -- result ---------------------------------------------------------

    def markdown(self) -> str:
        # A page cut short still gives what it had.
        self._end_link()
        self._end_pre()
        if self._table_depth:
            self._table_depth = 1
            self._end_table()
        text = "".join(self._out)
        text = re.sub(r"[ \t]+\n", "\n", text)
        # A bullet left empty, as after <li></li>.
        text = re.sub(r"^[ \t]*(?:-|\d+\.)[ \t]*\n", "", text + "\n", flags=re.M)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        title = _SPACES.sub(" ", "".join(self._title)).strip()
        if title and not self._has_h1:
            text = f"# {_escape(title)}\n\n{text}".rstrip()
        return text

    # -- parser events --------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        if tag in _SKIPPED:
            self._skip_tag, self._skip_depth = tag, 1
            return
        if tag == "title":
            self._in_title = True
        elif tag in _BLOCKS:
            self._break()
        elif tag in _HEADINGS:
            if self._inline_only():
                self._text(" ")
            else:
                self._has_h1 = self._has_h1 or tag == "h1"
                self._out.append("\n\n" + "#" * int(tag[1]) + " ")
                self._heading = True
        elif tag in ("br", "hr"):
            if tag == "hr" and not self._inline_only() and not self._lists:
                self._out.append("\n\n---\n\n")
            else:
                self._break()
        elif tag in ("ul", "ol"):
            self._break()
            self._lists.append([tag, 0])
        elif tag == "li":
            self._start_item()
        elif tag == "a":
            if self._link is None:
                self._link = (dict(attrs).get("href") or "").strip(), []
        elif tag == "img":
            alt = _SPACES.sub(" ", dict(attrs).get("alt") or "").strip()
            if alt:
                self._text(_escape(f"[image: {alt}]"))
        elif tag == "pre":
            if self._inline_only():
                self._text(" ")
            elif self._pre is None:
                self._pre = []
        elif tag == "table":
            self._start_table()
        elif tag in ("tr", "td", "th", "caption"):
            self._table_part(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # <br/>, <img/>: never opens anything that a closing tag would end.
        if tag in _SKIPPED or tag in ("a", "pre", "table", "ul", "ol", "title"):
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        elif tag in _HEADINGS:
            if self._heading:
                self._out.append("\n\n")
                self._heading = False
        elif tag in _BLOCKS:
            self._break()
        elif tag in ("ul", "ol"):
            if self._lists:
                self._lists.pop()
            self._break()
        elif tag == "a":
            self._end_link()
        elif tag == "pre":
            self._end_pre()
        elif tag == "table":
            self._end_table()
        elif tag in ("td", "th") and self._table_depth == 1:
            self._end_cell()
        elif tag == "tr" and self._table_depth == 1:
            self._end_row()
        elif tag == "caption" and self._caption is not None:
            self._caption_text = _SPACES.sub(" ", "".join(self._caption)).strip()
            self._caption = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data:
            return
        if self._in_title:
            self._title.append(data)
            return
        if self._pre is not None and self._link is None and self._cell is None:
            self._pre.append(data)
            return
        self._text(_escape(_SPACES.sub(" ", data)))

    # -- writing --------------------------------------------------------

    def _inline_only(self) -> bool:
        """Inside a link, a table cell or a caption: blocks become spaces."""
        return (
            self._link is not None
            or self._cell is not None
            or self._caption is not None
            or self._heading
        )

    def _text(self, text: str) -> None:
        if not text:
            return
        if self._link is not None:
            self._link[1].append(text)
        elif self._cell is not None:
            self._cell.append(text)
        elif self._caption is not None:
            self._caption.append(text)
        elif self._table_depth:
            return  # whitespace between rows and cells
        else:
            last = self._out[-1][-1:] if self._out else "\n"
            if last in ("\n", " "):
                text = text.lstrip(" ")
            if text:
                self._out.append(text)

    def _break(self) -> None:
        """A paragraph break, or a space where a break cannot go."""
        if self._inline_only() or self._lists:
            self._text(" ")
        elif not self._table_depth:
            self._out.append("\n\n")

    def _start_item(self) -> None:
        if self._inline_only() or self._table_depth:
            self._text(" ")
            return
        depth = max(len(self._lists), 1)
        bullet = "- "
        if self._lists and self._lists[-1][0] == "ol":
            self._lists[-1][1] += 1
            bullet = f"{self._lists[-1][1]}. "
        self._out.append("\n" + "  " * (depth - 1) + bullet)

    def _end_link(self) -> None:
        if self._link is None:
            return
        href, parts = self._link
        self._link = None
        label = _SPACES.sub(" ", "".join(parts)).strip()
        if href.startswith(("http://", "https://", "mailto:")):
            target = href.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
            self._text(f"[{label or _escape(href)}]({target})")
        else:
            self._text(label)

    def _end_pre(self) -> None:
        if self._pre is None:
            return
        code = "".join(self._pre).strip("\n")
        self._pre = None
        if code.strip():
            fence = _fence(code)
            self._out.append(f"\n\n{fence}\n{code}\n{fence}\n\n")

    # -- tables ---------------------------------------------------------

    def _start_table(self) -> None:
        if self._table_depth:
            # A table inside a cell: its text joins that cell.
            self._table_depth += 1
            self._text(" ")
            return
        self._break()
        self._table_depth = 1
        self._rows, self._row, self._cell = [], None, None
        self._caption, self._caption_text = None, ""

    def _table_part(self, tag: str) -> None:
        if self._table_depth != 1:
            self._text(" ")
        elif tag == "tr":
            self._end_row()
            self._row = []
        elif tag in ("td", "th"):
            self._end_cell()
            if self._row is None:
                self._row = []
            self._cell = []
        elif tag == "caption":
            self._caption = []

    def _end_cell(self) -> None:
        if self._cell is None:
            return
        text = _SPACES.sub(" ", "".join(self._cell)).strip().replace("|", "\\|")
        self._cell = None
        if self._row is None:
            self._row = []
        self._row.append(text)

    def _end_row(self) -> None:
        self._end_cell()
        if self._row:
            self._rows.append(self._row)
        self._row = None

    def _end_table(self) -> None:
        if not self._table_depth:
            return
        self._table_depth -= 1
        if self._table_depth:
            return
        self._end_row()
        self._caption = None
        rows = [row for row in self._rows if any(row)]
        self._rows = []
        if not rows:
            return
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        lines = [f"**{self._caption_text}**", ""] if self._caption_text else []
        lines.append("| " + " | ".join(rows[0]) + " |")
        lines.append("|" + "|".join([" --- "] * width) + "|")
        lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
        self._caption_text = ""
        self._out.append("\n\n" + "\n".join(lines) + "\n\n")
