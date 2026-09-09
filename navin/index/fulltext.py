# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Ranked full-text search over the project, backed by native tantivy.

The code index already fingerprints every file; this module diffs those
fingerprints against its own manifest and hands the changes to the tantivy
index maintained by ``navin_core``. Without the native extension the feature
simply reports itself unavailable - grep and the symbol index keep working.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

from navin.index.store import cache_path

# Text-ish files worth searching that the symbol index has no language for.
_EXTRA_TEXT_SUFFIXES = frozenset({
    ".md", ".rst", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".env", ".sh", ".ps1", ".bat", ".dockerfile", ".html", ".css",
})


def _native() -> Any | None:
    try:
        from navin.utils.native import native

        core = native()
    except Exception:
        return None
    if core is None or not hasattr(core, "fulltext_update"):
        return None
    return core


class FulltextIndex:
    """One tantivy index per project root, updated incrementally."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=False)
        base = cache_path(self.root)
        self.index_dir = base.with_name(base.stem + "-fulltext")
        self.manifest_path = base.with_name(base.stem + "-fulltext-manifest.json")

    @staticmethod
    def available() -> bool:
        return _native() is not None

    def _load_manifest(self) -> dict[str, int]:
        try:
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, int] = {}
        for key, value in raw.items():
            try:
                out[str(key)] = int(value)
            except (TypeError, ValueError):
                continue
        return out

    def _save_manifest(self, manifest: dict[str, int]) -> None:
        try:
            self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.manifest_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(manifest), encoding="utf-8")
            os.replace(tmp, self.manifest_path)
        except OSError:
            logger.debug("Fulltext manifest save failed for {}", self.root)

    def refresh(self, files: dict[str, int]) -> int:
        """Bring the index in line with ``files`` (rel path -> mtime_ns).

        Returns how many files were (re)indexed; 0 when already current.
        """
        core = _native()
        if core is None:
            return 0
        manifest = self._load_manifest()
        changed = [
            (rel, mtime) for rel, mtime in files.items() if manifest.get(rel) != mtime
        ]
        removed = [rel for rel in manifest if rel not in files]
        if not changed and not removed:
            return 0
        try:
            indexed = int(
                core.fulltext_update(
                    str(self.index_dir), str(self.root), changed, removed
                )
            )
        except Exception as exc:
            logger.debug("Fulltext update failed for {}: {}", self.root, exc)
            return 0
        self._save_manifest(dict(files))
        return indexed

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]] | None:
        """BM25 hits as ``{path, score, snippet}`` rows, or None when unavailable."""
        core = _native()
        if core is None:
            return None
        try:
            raw = core.fulltext_search(str(self.index_dir), query, int(limit))
            hits = json.loads(raw)
        except Exception as exc:
            logger.debug("Fulltext search failed for {}: {}", self.root, exc)
            return None
        return [hit for hit in hits if isinstance(hit, dict)] if isinstance(hits, list) else None


def fulltext_file_set(index: Any) -> dict[str, int]:
    """The files a project's full-text index should cover, with fingerprints.

    Code files come straight from the symbol index entries (their mtimes are
    already known). Doc/config files have no ``FileEntry``, so they are
    stat'ed here - only on refresh, never per query.
    """
    files: dict[str, int] = {
        rel: entry.mtime_ns for rel, entry in index.entries.items()
    }
    root = index.root
    for rel in getattr(index, "all_files", []):
        if rel in files:
            continue
        suffix = os.path.splitext(rel)[1].lower()
        if suffix not in _EXTRA_TEXT_SUFFIXES:
            continue
        try:
            files[rel] = os.stat(os.path.join(root, rel)).st_mtime_ns
        except OSError:
            continue
    return files
