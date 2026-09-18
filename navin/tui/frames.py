# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Paint input changes without waiting for a transcript layout frame."""

import gc
from contextlib import contextmanager
from functools import lru_cache
from threading import RLock

from rich.style import Style
from textual.geometry import Region
from textual.strip import Strip
from textual.widget import Widget

_gc_lock = RLock()
_gc_users = 0
_gc_original: tuple[int, int, int] | None = None
_gc_applied: tuple[int, int, int] | None = None


@lru_cache(maxsize=64)
def _blank(width: int, style: Style) -> Strip:
    return Strip.blank(width, style)


def background_lines(widget: Widget, crop: Region) -> list[Strip]:
    """Paint an undecorated opaque screen without allocating a full canvas."""
    style = widget.visual_style
    strip = _blank(crop.width, style.rich_style)
    for line_filter in widget.get_line_filters():
        strip = strip.apply_filter(line_filter, style.background)
    return [strip] * crop.height


@contextmanager
def terminal_gc_policy():
    """Reduce full-heap scans caused by short-lived Textual render caches.

    Keep automatic collection enabled and restore the caller's settings on
    exit. Larger young generations let temporary strips expire before they
    promote; full collections remain enabled for long-running sessions.
    """
    global _gc_users, _gc_original, _gc_applied
    with _gc_lock:
        if _gc_users == 0 and gc.isenabled():
            _gc_original = gc.get_threshold()
            _gc_applied = tuple(max(value, floor) for value, floor in
                                zip(_gc_original, (4000, 10, 50)))
            gc.set_threshold(*_gc_applied)
        _gc_users += 1
    try:
        yield
    finally:
        with _gc_lock:
            _gc_users -= 1
            if _gc_users == 0:
                if _gc_original is not None and gc.get_threshold() == _gc_applied:
                    gc.set_threshold(*_gc_original)
                _gc_original = _gc_applied = None


def paint_input(widget: Widget) -> bool:
    """Use the current composition, including clipping, overlays and scrollbars.

    Layout and other pending repaints still run normally on the next frame.
    Textual's compositor has no regional refresh entry point, so temporarily
    give it just the input region and restore its pending work synchronously.
    """
    if not widget.is_attached or not widget.has_focus:
        return False
    app = widget.app
    screen = widget.screen
    if app._batch_count or app.is_inline or screen is not app.screen:
        return False
    compositor = screen._compositor
    geometry = compositor.visible_widgets.get(widget)
    if geometry is None:
        return False
    region, clip = geometry
    region = region.intersection(clip)
    if not region:
        return False
    pending = compositor._dirty_regions
    compositor._dirty_regions = {region}
    try:
        frame = compositor.render_update(screen_stack=app._background_screens)
    finally:
        pending.update(compositor._dirty_regions)
        compositor._dirty_regions = pending
    if frame is None:
        return False
    app._display(screen, frame)
    return True
