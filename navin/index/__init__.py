# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Project code index: symbols, references, and the call and import graphs.

The index is the shared source of truth for semantic navigation. It powers the
``code_index`` agent tool, the Dev workbench Graph tab, and the file roles the
``metagraph`` tool records in ``.navin/metadata/index.json``.

Three layers of graph are available:

1. **Containment** - which class a method belongs to, from parsed qualified
   names (``members``, ``container``).
2. **Call edges** - which definition calls, subclasses or decorates with which
   other (``callers``, ``callees``, ``impact``).
3. **Import edges** - which file depends on which (``dependencies``,
   ``dependents``, ``edges``).

Design notes:

- **No native dependencies.** Python is parsed with the stdlib :mod:`ast` for
  exact results; other languages use a declarative pattern table
  (``languages.json``). This keeps the index available offline and on every
  platform navin ships to.
- **Incremental.** :mod:`navin.index.store` caches per-file entries keyed by
  ``(mtime_ns, size)`` outside the project tree, so repeated queries never
  re-parse unchanged files. References live in a second cache file loaded only
  when a reference or call-graph query runs.
- **Honest about precision.** Definitions, containment and import edges are
  exact. Call edges are extracted exactly for Python but matched to their target
  by name, so the query layer discards targets that are not callable, not
  reachable through the import graph, or ambiguous project-wide, and labels what
  remains with its evidence.
"""

from navin.index.service import CodeIndex, get_index
from navin.index.symbols import FileEntry, Reference, Symbol

__all__ = ["CodeIndex", "FileEntry", "Reference", "Symbol", "get_index"]
