# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Background warming of the code index (A1 of the hot-path plan).

The first ``code_index``/symbol tool of a session used to pay the full cold
build (~3s on a 5k-file repo) inside the agent turn. Warming starts that
build as soon as the workspace is known - WebUI chat opened, workspace scope
changed - so the first lookup finds a loaded index.

Freshness between turns is handled by :meth:`CodeIndex.revalidate`, which is
an incremental stat-walk (no external watcher dependency): the warmer only
has to (a) trigger the initial build off the turn path and (b) mark the index
dirty when an agent tool writes a file, so the throttled revalidate cannot
serve a stale snapshot right after an edit.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from loguru import logger

_warming: set[str] = set()
_warming_lock = threading.Lock()

# A WSL project opened from Windows pays every stat through the 9P bridge:
# chat opens and workspace switches re-warm it at most this often. Lookups
# still revalidate on their own (throttled) schedule.
_BRIDGED_WARM_COOLDOWN_S = 600.0
_last_bridged_warm: dict[str, float] = {}


def schedule_warm(root: Path | str | None) -> bool:
    """Build/refresh the code index for ``root`` off the turn path.

    Idempotent per root: a warm already in flight is not duplicated. Runs in
    a daemon thread so speculative indexing never holds interpreter shutdown.
    Returns whether a warm was scheduled.
    """
    if root is None:
        return False
    try:
        from navin.utils.resolve_cache import resolve_root

        resolved = resolve_root(root)
    except (OSError, RuntimeError):
        return False
    if not resolved.is_dir():
        return False

    from navin.index.service import is_bridged_root

    key = str(resolved)
    bridged = is_bridged_root(resolved)
    with _warming_lock:
        if key in _warming:
            return False
        if bridged:
            last = _last_bridged_warm.get(key)
            if last is not None and time.monotonic() - last < _BRIDGED_WARM_COOLDOWN_S:
                return False
        _warming.add(key)

    def _work() -> None:
        try:
            from navin.index import get_index

            index = get_index(resolved)
            stats = index.ensure()
            logger.debug(
                "code index warmed for {} ({} files)",
                resolved,
                stats.total if stats else 0,
            )
            index.warm_refs()
            _warm_fulltext(index)
            _warm_semantic(resolved, index)
        except Exception as exc:
            logger.debug("code index warm failed for {}: {}", resolved, exc)
        finally:
            with _warming_lock:
                _warming.discard(key)
                if bridged:
                    # Failures too: a bridge that broke a warm will break the
                    # next one, and retrying at once only loads it again.
                    _last_bridged_warm[key] = time.monotonic()

    threading.Thread(target=_work, daemon=True, name="index-warmer").start()
    return True


def _warm_fulltext(index: object) -> None:
    """Refresh the lexical full-text index while the warm is already paid."""
    try:
        from navin.index.fulltext import FulltextIndex, fulltext_file_set

        if not FulltextIndex.available():
            return
        ft = FulltextIndex(getattr(index, "root"))
        updated = ft.refresh(fulltext_file_set(index))
        if updated:
            logger.debug(
                "fulltext warmed for {} ({} files)",
                getattr(index, "root", "?"),
                updated,
            )
    except Exception as exc:
        logger.debug("fulltext warm skipped: {}", exc)


def _warm_semantic(root: Path, index: object) -> None:
    """Best-effort background semantic sync when a free local embedder is on."""
    try:
        from navin.agent.tools.code_index import (
            SemanticSearchConfig,
            _is_free_provider,
            _schedule_semantic_sync,
        )
        from navin.config.loader import load_config
        from navin.index.embeddings import build_client

        config = load_config()
        semantic_cfg = getattr(getattr(config, "tools", None), "semantic_search", None)
        if semantic_cfg is None:
            semantic_cfg = SemanticSearchConfig()
        enabled = getattr(semantic_cfg, "enabled", None)
        provider = str(getattr(semantic_cfg, "provider", "") or "")
        # Auto-on only for free local providers when unset; respect explicit false.
        if enabled is False:
            return
        if enabled is not True and not _is_free_provider(provider):
            return
        client = build_client(semantic_cfg, config.providers)
        max_chunks = int(getattr(semantic_cfg, "max_chunks", 0) or 0) or 50_000
        _schedule_semantic_sync(
            root,
            index,
            client,
            max_chunks,
            remember_unreachable_key=provider or None,
        )
    except Exception as exc:
        logger.debug("semantic warm skipped for {}: {}", root, exc)


def note_file_written(path: Path | str) -> None:
    """Mark loaded indexes containing ``path`` dirty after an agent edit.

    Cheap by construction: only in-memory instances are touched (no I/O), so
    this can run on every file write. The next ``ensure()`` then re-stats the
    tree instead of trusting the revalidation TTL. Also schedules a debounced
    metagraph rebuild so the Graph tab stays live.
    """
    try:
        from navin.index import service as index_service

        resolved = Path(path).expanduser().resolve(strict=False)
        touched_roots: list[str] = []
        for root_key, index in list(index_service._instances.items()):
            try:
                resolved.relative_to(root_key)
            except ValueError:
                continue
            index.mark_dirty(resolved)
            touched_roots.append(root_key)
        if touched_roots:
            from navin.webui.metagraph_notify import schedule_metagraph_refresh

            for root_key in touched_roots:
                schedule_metagraph_refresh(root_key)
    except Exception:  # noqa: BLE001 - freshness hint only, never break a write
        logger.debug("note_file_written failed for {}", path)
