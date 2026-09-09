"""Markdown inline code: color only paths, not every backtick span."""

from __future__ import annotations

from textual.content import Content, Span
from textual.widgets.markdown import MarkdownBlock

from navin.tui.paths import looks_like_path

_INSTALLED = False


def restyle_inline_code(content: Content) -> Content:
    """Turn ``.code_inline`` spans that look like paths into ``.code_path``."""
    if not content.spans:
        return content
    plain = content.plain
    new_spans: list[Span] = []
    changed = False
    for span in content.spans:
        if span.style == ".code_inline":
            piece = plain[span.start : span.end]
            if looks_like_path(piece):
                new_spans.append(Span(span.start, span.end, ".code_path"))
                changed = True
                continue
        new_spans.append(span)
    if not changed:
        return content
    return Content(plain, spans=new_spans)


def install_path_styles() -> None:
    """Patch Textual Markdown so path-like inline code uses ``.code_path``."""
    global _INSTALLED
    if _INSTALLED:
        return
    classes = set(MarkdownBlock.COMPONENT_CLASSES)
    classes.add("code_path")
    MarkdownBlock.COMPONENT_CLASSES = classes
    original = MarkdownBlock._token_to_content

    def _token_to_content(self: MarkdownBlock, token: object) -> Content:
        return restyle_inline_code(original(self, token))

    MarkdownBlock._token_to_content = _token_to_content  # type: ignore[method-assign]
    _INSTALLED = True
