# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A small copy icon for one transcript block.

A click puts the block's text in the clipboard (the app keeps its in-app
copy and writes the host clipboard off the UI thread), and the icon reads
"Copied" for a moment so the click visibly landed.
"""

from __future__ import annotations

from collections.abc import Callable

from textual import events
from textual.timer import Timer
from textual.widgets import Static

COPY_LABEL = "⧉ Copy"
COPIED_LABEL = "✓ Copied"
COPIED_SECONDS = 1.6


class CopyChip(Static):
    """One muted line, ``⧉ Copy``: click copies what ``source`` returns."""

    ALLOW_SELECT = False
    can_focus = False

    DEFAULT_CSS = """
    CopyChip {
        width: auto;
        height: 1;
        padding: 0;
        margin: 0;
        color: $text-muted;
        background: transparent;
        text-style: none;
    }
    CopyChip:hover { color: $foreground; }
    CopyChip.-copied { color: $success; }
    """

    def __init__(self, source: Callable[[], str], *, classes: str | None = None) -> None:
        super().__init__(COPY_LABEL, classes=classes, markup=False)
        self._source = source
        self._restore: Timer | None = None

    def on_click(self, event: events.Click) -> None:
        # The block under it must not fold, toggle or start a selection.
        event.stop()
        self.copy()

    def copy(self) -> bool:
        """Copy the block's text; False when there is nothing to copy."""
        text = self._source() or ""
        if not text.strip():
            self.app.notify("Nothing to copy", severity="warning", timeout=1.5)
            return False
        self.app.copy_to_clipboard(text)
        self.update(COPIED_LABEL)
        self.add_class("-copied")
        if self._restore is not None:
            self._restore.stop()
        self._restore = self.set_timer(COPIED_SECONDS, self._reset)
        return True

    def _reset(self) -> None:
        self._restore = None
        self.update(COPY_LABEL)
        self.remove_class("-copied")
