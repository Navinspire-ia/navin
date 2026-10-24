# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Transcript virtualization: off-screen blocks kept as data, not widgets.

A long chat used to keep every message as live widgets (about 56 DOM nodes,
2.7 MB and 17k Python objects per turn), only hidden with ``display: none``
far above the viewport. Every restyle, full garbage collection and focus
change still walked all of them.

Now a finished block that scrolls well out of view is replaced by a
``BlockRecord``: the data needed to build it again (texts, tool results,
states). Runs of records sit in a ``RecordGap``, one blank widget as tall
as the blocks it stands for, so the scroll position and scrollbar stay
true. When the reader scrolls toward a gap, its records are built again
(finished answers as one cheap ``SettledMarkdown``) and the gap shrinks,
with the reading position anchored.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.style import Style
from textual.strip import Strip
from textual.widget import Widget


class BlockRecord:
    """A transcript block held as plain data while it is out of view.

    App code walks ``Transcript.blocks()`` for find, copy and export. A
    record answers like the widget it stands for: ``isinstance(record,
    UserMessage)`` is true (``__class__`` reports the widget type, as
    ``unittest.mock`` does), state such as ``raw_text`` or ``text`` reads
    from ``state``, and find highlights (``add_class``) are kept for the
    rebuilt widget.
    """

    __slots__ = ("widget_type", "state", "height", "classes", "_build", "_copy")

    def __init__(
        self,
        widget_type: type[Widget],
        state: dict[str, Any],
        build: Callable[[BlockRecord], Widget],
        *,
        classes: set[str] | frozenset[str] = frozenset(),
        copy: Callable[[BlockRecord], str] | None = None,
        height: int = 0,
    ) -> None:
        self.widget_type = widget_type
        self.state = state
        self._build = build
        self._copy = copy
        self.classes = set(classes)
        self.height = height

    # isinstance(record, UserMessage) / AssistantMessage keeps app loops simple.
    @property  # type: ignore[misc]
    def __class__(self) -> type:  # noqa: D105
        return self.widget_type

    display = False
    is_attached = False
    transient = False

    def __getattr__(self, name: str) -> Any:
        if name == "copy_text" and self._copy is not None:
            return lambda: self._copy(self)
        state = object.__getattribute__(self, "state")
        if name in state:
            return state[name]
        raise AttributeError(name)

    def build(self) -> Widget:
        widget = self._build(self)
        if self.classes:
            widget.set_classes(set(widget.classes) | self.classes)
        return widget

    def add_class(self, *names: str) -> BlockRecord:
        self.classes.update(names)
        return self

    def remove_class(self, *names: str) -> BlockRecord:
        self.classes.difference_update(names)
        return self

    def has_class(self, *names: str) -> bool:
        return all(name in self.classes for name in names)

    def __repr__(self) -> str:
        return f"BlockRecord({self.widget_type.__name__}, height={self.height})"


class RecordGap(Widget):
    """Blank space standing for a run of off-screen records."""

    ALLOW_SELECT = False
    can_focus = False

    DEFAULT_CSS = """
    RecordGap {
        width: 1fr;
        height: 0;
        margin: 0;
        padding: 0;
        background: $background;
    }
    """

    def __init__(self, records: list[BlockRecord] | None = None) -> None:
        super().__init__()
        self.records: list[BlockRecord] = []
        self.set_records(records or [])

    @property
    def total_height(self) -> int:
        return sum(max(0, record.height) for record in self.records)

    def set_records(self, records: list[BlockRecord]) -> None:
        self.records = records
        height = self.total_height
        if self.styles.height is None or self.styles.height.value != height:
            self.styles.height = height

    def offsets(self) -> list[int]:
        """Top of each record, relative to the gap."""
        out = []
        top = 0
        for record in self.records:
            out.append(top)
            top += max(0, record.height)
        return out

    def render_line(self, y: int) -> Strip:
        width = self.outer_size.width
        style = self.visual_style.rich_style if self.is_attached else Style()
        return Strip.blank(max(0, width), style)
