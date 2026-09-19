# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Restrained Markdown accents for paths and recognizable shell commands."""

from __future__ import annotations

import re

from textual.content import Content, Span
from textual.widgets.markdown import MarkdownBlock

from navin.tui.paths import looks_like_path

_INSTALLED = False
_COMMAND = re.compile(
    r"^(?:\$\s+|(?:sudo\s+)?(?:git|npm|npx|pnpm|yarn|bun|uv|pip|pip3|"
    r"python|python3|pytest|ruff|node|cargo|go|make|docker|kubectl|curl|"
    r"ls|cd|rg|ssh|navin)\s+|\./\S+(?:\s|$))"
)


def readable_validation_report(text: str) -> str | None:
    """Render stored pre-report validation messages without rewriting history."""
    reasons = {
        "The changes are saved, but the task is not validated.": "repeated",
        "The changes are saved, but validation is still incomplete.": "no_progress",
        "The task ended before validation was completed.": "ended",
    }
    reason = next((reason for prefix, reason in reasons.items() if text.startswith(prefix)), None)
    if reason is None:
        return None
    from navin.agent.code_validation import CodeValidationState

    _, separator, details = text.partition("\n\n")
    if not separator:
        return None
    body, _, paths = details.partition("Changed files: ")
    state = CodeValidationState(
        revision=1,
        needs_tests="Run meaningful tests for the requested behavior" in body,
        paths={path.strip() for path in paths.strip().split(", ") if path.strip()},
    )
    instructions = (
        "Run meaningful tests for the requested behavior",
        "Run the changed tests, for example with exec:",
        "Wait for the test process to finish, inspect its results",
        "Run an appropriate check after the latest edits.",
    )
    # Preserve diagnostics we cannot classify rather than treating them as proof
    # that tests passed or failed. This adapter only changes the presentation.
    remaining = [line for line in body.splitlines() if line.strip() and not line.startswith(instructions)]
    state.test_result_note = "\n\n".join(remaining)
    return state.completion_message(reason=reason)


def restyle_inline_code(content: Content) -> Content:
    """Accent commands and paths while leaving identifiers neutral."""
    if not content.spans:
        return content
    plain = content.plain
    new_spans: list[Span] = []
    changed = False
    for span in content.spans:
        if span.style == ".code_inline":
            piece = plain[span.start : span.end]
            if _COMMAND.match(piece.strip()):
                new_spans.append(Span(span.start, span.end, ".code_command"))
                changed = True
                continue
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
    classes.update({"code_path", "code_command"})
    MarkdownBlock.COMPONENT_CLASSES = classes
    original = MarkdownBlock._token_to_content

    def _token_to_content(self: MarkdownBlock, token: object) -> Content:
        return restyle_inline_code(original(self, token))

    MarkdownBlock._token_to_content = _token_to_content  # type: ignore[method-assign]
    _INSTALLED = True
