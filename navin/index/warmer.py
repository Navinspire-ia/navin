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

import asyncio
import threading
from pathlib import Path

from loguru import logger

_warming: set[str] = set()
_warming_lock = threading.Lock()


def schedule_warm(root: Path | str | None) -> bool:
    """Build/refresh the code index for ``root`` off the turn path.

    Idempotent per root: a warm already in flight is not duplicated. Runs in
    the asyncio default executor when a loop is running, in a daemon thread
    otherwise. Returns whether a warm was scheduled.
    """
    if root is None:
        return False
    try:
        resolved = Path(root).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    if not resolved.is_dir():
        return False

    key = str(resolved)
    with _warming_lock:
        if key in _warming:
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
            _warm_fulltext(index)
            _warm_semantic(resolved, index)
        except Exception as exc:
            logger.debug("code index warm failed for {}: {}", resolved, exc)
        finally:
            with _warming_lock:
                _warming.discard(key)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        threading.Thread(target=_work, daemon=True, name="index-warmer").start()
        return True
    loop.run_in_executor(None, _work)
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
            index.mark_dirty()
            touched_roots.append(root_key)
        if touched_roots:
            from navin.webui.metagraph_notify import schedule_metagraph_refresh

            for root_key in touched_roots:
                schedule_metagraph_refresh(root_key)
    except Exception:  # noqa: BLE001 - freshness hint only, never break a write
        logger.debug("note_file_written failed for {}", path)
