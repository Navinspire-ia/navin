# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Let removed widgets be freed.

Textual's ``NodeList`` caches its displayed children as ``(updates, list)``
and only rebuilds that list the next time it is read. After a removal the
stale list keeps every removed child (and its whole subtree) alive until
something reads ``displayed`` again, which for a chat that only appends may
be never: about a third of removed transcript blocks survived collection.
Dropping the cache when a child leaves fixes that; it is rebuilt lazily.
"""

from __future__ import annotations

from textual._node_list import NodeList

_installed = False


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    original_remove = NodeList._remove
    original_clear = NodeList._clear

    def _remove(self: NodeList, widget) -> None:  # type: ignore[no-untyped-def]
        original_remove(self, widget)
        self._displayed_nodes = (-1, [])
        self._displayed_visible_nodes = (-1, [])

    def _clear(self: NodeList) -> None:
        original_clear(self)
        self._displayed_nodes = (-1, [])
        self._displayed_visible_nodes = (-1, [])

    NodeList._remove = _remove  # type: ignore[method-assign]
    NodeList._clear = _clear  # type: ignore[method-assign]
