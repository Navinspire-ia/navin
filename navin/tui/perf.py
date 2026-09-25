# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Textual hot-path fixes for long chats.

``Widget.refresh()`` marks the widget dirty through ``self.size``, which
resolves the widget's region in the compositor's full map. Textual drops
that map after every mount and every scroll, so each refresh that follows
rebuilds the arrangement of *every* widget on the screen. Restoring a
history page or streaming tool cards triggered hundreds of rebuilds.

The last laid-out size (``outer_size``) carries the same information and
costs nothing. It is exact once the widget has been through a layout, and
both are empty before that.
"""

from __future__ import annotations

from textual.geometry import Region
from textual.widget import Widget

_original_set_dirty = Widget._set_dirty
_installed = False


def _set_dirty(self: Widget, *regions: Region) -> None:
    if regions:
        _original_set_dirty(self, *regions)
        return
    self._dirty_regions.clear()
    self._repaint_regions.clear()
    self._styles_cache.clear()
    outer_size = self.outer_size
    gutter = self.styles.gutter
    self._styles_cache.set_dirty(
        Region(0, 0, max(0, outer_size.width - gutter.width), max(0, outer_size.height - gutter.height))
    )
    self._dirty_regions.add(outer_size.region)
    if outer_size:
        self._repaint_regions.add(outer_size.region)


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    Widget._set_dirty = _set_dirty  # type: ignore[method-assign]
