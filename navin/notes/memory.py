"""Semantic memory over the Notes store.

Turns ``~/.navin/notes`` into the AI memory the Notes module promises: every
note is chunked by markdown section, embedded with the same
``tools.semanticSearch`` endpoint the code index uses, and queried with cosine
similarity fused with the lexical search. When no embedding endpoint is
available the whole thing degrades to lexical passages, so "ask my notes"
always answers.

Vectors live next to the code-index caches (``~/.navin/index/``), never inside
the notes tree, keyed by the embedding model signature so switching models
rebuilds instead of mixing vector spaces.
"""

from __future__ import annotations

import asyncio
import json
import time
from array import array
from pathlib import Path
from typing import Any

from loguru import logger

from navin.notes import store
from navin.utils.atomic_io import InterProcessLock, atomic_write_bytes, atomic_write_text

_VERSION = 1
_MAX_CHUNK_CHARS = 1600
_MAX_NOTE_CHUNKS = 40
_DEFAULT_LIMIT = 8
_RRF_K = 60

# Endpoints that refused a connection recently, and until when we believe it.
# Mirrors the code_index cache: without it every question re-pays a refused
# TCP connect when the local embedding server is not running.
_UNREACHABLE: dict[str, float] = {}
_UNREACHABLE_TTL_S = 300.0

_FREE_EMBEDDING_PROVIDERS = frozenset(
    {"ollama", "lm_studio", "vllm", "ovms", "atomic_chat"}
)

# One in-flight background sync per notes root.
_SYNCS: dict[str, "asyncio.Task[Any]"] = {}


def _recently_unreachable(key: str) -> bool:
    expiry = _UNREACHABLE.get(key)
    if expiry is None:
        return False
    if time.monotonic() >= expiry:
        _UNREACHABLE.pop(key, None)
        return False
    return True


def _remember_unreachable(key: str) -> None:
    _UNREACHABLE[key] = time.monotonic() + _UNREACHABLE_TTL_S


# ---------------------------------------------------------------------------
# Chunking


def chunk_note(title: str, body: str) -> list[dict[str, Any]]:
    """Split a note body into heading-scoped chunks.

    Each chunk carries the heading path and start line so an answer can cite
    "note > section, line N" instead of a whole file.
    """
    chunks: list[dict[str, Any]] = []
    heading = ""
    buffer: list[str] = []
    buffer_line = 1
    in_fence = False

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if not text:
            return
        # Long sections split on paragraph boundaries so no chunk exceeds the
        # embedding budget while sentences stay whole.
        start = 0
        line_offset = 0
        while start < len(text) and len(chunks) < _MAX_NOTE_CHUNKS:
            window = text[start : start + _MAX_CHUNK_CHARS]
            if start + _MAX_CHUNK_CHARS < len(text):
                cut = window.rfind("\n\n")
                if cut > _MAX_CHUNK_CHARS // 4:
                    window = window[:cut]
            chunks.append(
                {
                    "heading": heading,
                    "line": buffer_line + line_offset,
                    "text": window.strip(),
                }
            )
            line_offset += window.count("\n") + (1 if window else 0)
            start += len(window) or _MAX_CHUNK_CHARS

    for line_no, line in enumerate(body.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            buffer.append(line)
            continue
        if not in_fence and stripped.startswith("#") and stripped.lstrip("#").strip():
            flush()
            heading = stripped.lstrip("#").strip()
            buffer = []
            buffer_line = line_no + 1
            continue
        if not buffer and not stripped:
            buffer_line = line_no + 1
            continue
        buffer.append(line)
    flush()

    if not chunks and title.strip():
        # Empty body: the title alone is still worth finding.
        chunks.append({"heading": "", "line": 1, "text": title.strip()})
    return chunks[:_MAX_NOTE_CHUNKS]


def _embed_text(title: str, chunk: dict[str, Any]) -> str:
    prefix = f"{title} › {chunk['heading']}" if chunk["heading"] else title
    return f"{prefix}\n{chunk['text']}"


# ---------------------------------------------------------------------------
# Index


def _normalize(vector: list[float]) -> list[float]:
    norm = sum(component * component for component in vector) ** 0.5
    if norm <= 0:
        return vector
    return [component / norm for component in vector]


class NotesMemoryIndex:
    """Embedding index over one notes root, persisted outside the tree."""

    def __init__(self, root: Path, client: Any) -> None:
        self.root = root
        self.client = client
        self._meta: dict[str, Any] | None = None

    # -- persistence --------------------------------------------------------

    def _meta_path(self) -> Path:
        from navin.index.store import cache_path

        base = cache_path(self.root)
        return base.with_name(base.stem + "-notes-mem.json")

    def _vec_path(self) -> Path:
        return self._meta_path().with_suffix(".vec")

    def _load_meta(self) -> dict[str, Any]:
        if self._meta is not None:
            return self._meta
        try:
            loaded = json.loads(self._meta_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if (
            not isinstance(loaded, dict)
            or loaded.get("version") != _VERSION
            or loaded.get("signature") != self.client.signature
        ):
            loaded = {
                "version": _VERSION,
                "signature": self.client.signature,
                "dim": 0,
                "files": {},
                "chunks": [],
            }
        self._meta = loaded
        return loaded

    def _load_vectors(self, dim: int, count: int) -> array:
        vectors = array("f")
        if dim <= 0 or count <= 0:
            return vectors
        try:
            with self._vec_path().open("rb") as handle:
                vectors.fromfile(handle, dim * count)
        except (OSError, EOFError):
            return array("f")
        return vectors

    def _write(self, meta: dict[str, Any], vectors: array) -> None:
        meta_path = self._meta_path()
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        vec_path = self._vec_path()
        # Vectors are committed first; metadata is the generation commit point.
        # A crash can leave an unused vector generation, never metadata pointing
        # at a partial temporary file.
        atomic_write_bytes(vec_path, vectors.tobytes())
        atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False))
        self._meta = meta

    # -- state ---------------------------------------------------------------

    def _current_files(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for path in store._iter_note_paths(self.root):
            try:
                stat = path.stat()
            except OSError:
                continue
            rel = path.relative_to(self.root).as_posix()
            out[rel] = f"{stat.st_mtime_ns}:{stat.st_size}"
        return out

    def pending(self) -> int:
        """Notes whose content changed since the last sync (plus removals)."""
        meta = self._load_meta()
        known: dict[str, str] = meta.get("files", {})
        current = self._current_files()
        changed = sum(1 for rel, fp in current.items() if known.get(rel) != fp)
        removed = sum(1 for rel in known if rel not in current)
        return changed + removed

    def has_vectors(self) -> bool:
        meta = self._load_meta()
        return bool(meta.get("chunks")) and self._vec_path().is_file()

    # -- sync ----------------------------------------------------------------

    async def sync(self) -> dict[str, int]:
        """Serialize memory-index read/version/write across processes."""
        lock_path = self._meta_path().with_suffix(".lock")
        with InterProcessLock(lock_path, timeout=60):
            self._meta = None
            return await self._sync_unlocked()

    async def _sync_unlocked(self) -> dict[str, int]:
        """Re-embed changed notes only; returns {embedded, removed, total}."""
        meta = self._load_meta()
        known: dict[str, str] = dict(meta.get("files", {}))
        current = self._current_files()
        changed = [rel for rel, fp in current.items() if known.get(rel) != fp]
        removed = [rel for rel in known if rel not in current]
        if not changed and not removed:
            return {"embedded": 0, "removed": 0, "total": len(meta.get("chunks", []))}

        dim = int(meta.get("dim") or 0)
        old_chunks: list[dict[str, Any]] = meta.get("chunks", [])
        old_vectors = self._load_vectors(dim, len(old_chunks))
        stale = set(changed) | set(removed)

        kept_chunks: list[dict[str, Any]] = []
        kept_vectors = array("f")
        if dim > 0 and len(old_vectors) == dim * len(old_chunks):
            for index, chunk in enumerate(old_chunks):
                if chunk["rel"] in stale:
                    continue
                kept_chunks.append(chunk)
                kept_vectors.extend(old_vectors[index * dim : (index + 1) * dim])
        # A corrupt/missing vector file re-embeds everything.
        elif old_chunks:
            changed = sorted(set(changed) | {c["rel"] for c in old_chunks if c["rel"] in current})

        new_chunks: list[dict[str, Any]] = []
        texts: list[str] = []
        for rel in changed:
            info = store._read_note_file(self.root, self.root / rel)
            if info is None:
                continue
            try:
                text = (self.root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            _, body = store.parse_note_text(text)
            for chunk in chunk_note(info["title"], body):
                new_chunks.append(
                    {
                        "rel": rel,
                        "note_id": info["id"],
                        "title": info["title"],
                        "folder": info["folder"],
                        "heading": chunk["heading"],
                        "line": chunk["line"],
                        "text": chunk["text"],
                    }
                )
                texts.append(_embed_text(info["title"], chunk))

        embedded = await self.client.embed(texts)
        for vector in embedded:
            normalized = _normalize(vector)
            if dim == 0:
                dim = len(normalized)
            kept_vectors.extend(normalized)
        kept_chunks.extend(new_chunks)

        files = {rel: fp for rel, fp in current.items()}
        self._write(
            {
                "version": _VERSION,
                "signature": self.client.signature,
                "dim": dim,
                "files": files,
                "chunks": kept_chunks,
            },
            kept_vectors,
        )
        return {
            "embedded": len(new_chunks),
            "removed": len(removed),
            "total": len(kept_chunks),
        }

    # -- search ---------------------------------------------------------------

    async def search(self, query: str, *, limit: int = _DEFAULT_LIMIT) -> list[dict[str, Any]]:
        meta = self._load_meta()
        chunks: list[dict[str, Any]] = meta.get("chunks", [])
        dim = int(meta.get("dim") or 0)
        if not chunks or dim <= 0:
            return []
        vectors = self._load_vectors(dim, len(chunks))
        if len(vectors) != dim * len(chunks):
            return []
        query_vector = _normalize((await self.client.embed([query]))[0])
        scored: list[tuple[float, int]] = []
        for index in range(len(chunks)):
            offset = index * dim
            score = 0.0
            for component in range(dim):
                score += vectors[offset + component] * query_vector[component]
            scored.append((score, index))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        out: list[dict[str, Any]] = []
        for score, index in scored[: max(1, limit)]:
            chunk = chunks[index]
            out.append({**chunk, "score": round(float(score), 4)})
        return out


# ---------------------------------------------------------------------------
# Ask (hybrid retrieval with lexical fallback)


def _resolve_client() -> Any | None:
    """Embedding client from ``tools.semanticSearch``; None means lexical only."""
    from navin.config.loader import load_config
    from navin.index.embeddings import EmbeddingError, build_client

    config = load_config()
    semantic = config.tools.semantic_search
    if semantic.enabled is False:
        return None
    name = (semantic.provider or "").strip().lower().replace("-", "_")
    if semantic.enabled is None and name not in _FREE_EMBEDDING_PROVIDERS:
        # Auto mode never bills a hosted endpoint the user did not opt into.
        return None
    unreachable_key = f"notes:{semantic.provider}:{semantic.model}"
    if _recently_unreachable(unreachable_key):
        return None
    try:
        return build_client(semantic, config.providers)
    except EmbeddingError:
        return None


def _lexical_passages(root: Path, query: str, *, limit: int) -> list[dict[str, Any]]:
    results = store.search_notes(root, query, limit=limit)["results"]
    passages: list[dict[str, Any]] = []
    for result in results:
        if result["matches"]:
            for match in result["matches"][:2]:
                passages.append(
                    {
                        "note_id": result["id"],
                        "title": result["title"],
                        "folder": result["folder"],
                        "heading": "",
                        "line": match["line"],
                        "text": match["text"],
                        "score": 0.0,
                    }
                )
        else:
            passages.append(
                {
                    "note_id": result["id"],
                    "title": result["title"],
                    "folder": result["folder"],
                    "heading": "",
                    "line": 1,
                    "text": result["title"],
                    "score": 0.0,
                }
            )
    return passages[:limit]


def _fuse(
    semantic: list[dict[str, Any]],
    lexical: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Reciprocal-rank fusion keyed on (note, line) so both signals count."""
    ranked: dict[tuple[str, int], dict[str, Any]] = {}
    scores: dict[tuple[str, int], float] = {}
    for rank, passage in enumerate(semantic):
        key = (passage["note_id"], passage["line"])
        ranked.setdefault(key, passage)
        scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank + 1)
    for rank, passage in enumerate(lexical):
        key = (passage["note_id"], passage["line"])
        ranked.setdefault(key, passage)
        scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank + 1)
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [ranked[key] for key, _ in ordered[: max(1, limit)]]


def _schedule_background_sync(index: NotesMemoryIndex, unreachable_key: str) -> bool:
    key = str(index.root)
    existing = _SYNCS.get(key)
    if existing is not None and not existing.done():
        return True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False

    async def _work() -> None:
        from navin.index.embeddings import EmbeddingError

        try:
            stats = await index.sync()
            logger.debug("notes memory synced: {}", stats)
        except EmbeddingError as exc:
            _remember_unreachable(unreachable_key)
            logger.debug("notes memory sync failed: {}", exc)
        except Exception:
            logger.exception("notes memory sync crashed")
        finally:
            _SYNCS.pop(key, None)

    _SYNCS[key] = loop.create_task(_work())
    return True


async def ask_notes(
    root: Path,
    query: str,
    *,
    limit: int = _DEFAULT_LIMIT,
    client: Any | None = None,
) -> dict[str, Any]:
    """Best passages across all notes for ``query``.

    Returns ``{"passages": [...], "semantic": bool, "syncing": bool}``.
    Semantic when an embedding endpoint is available, lexical otherwise -
    never an error, because "ask my notes" must always answer with something.
    """
    from navin.index.embeddings import EmbeddingError

    text = (query or "").strip()
    if not text:
        return {"passages": [], "semantic": False, "syncing": False}

    resolved = client if client is not None else _resolve_client()
    lexical = _lexical_passages(root, text, limit=limit)
    if resolved is None:
        return {"passages": lexical, "semantic": False, "syncing": False}

    index = NotesMemoryIndex(root, resolved)
    unreachable_key = f"notes:{resolved.model}"
    syncing = False
    try:
        if index.pending():
            if index.has_vectors():
                # Stale but usable: answer from what exists, refresh behind.
                syncing = _schedule_background_sync(index, unreachable_key)
                if not syncing:
                    await index.sync()
            else:
                await index.sync()
        semantic_hits = await index.search(text, limit=limit)
    except EmbeddingError as exc:
        _remember_unreachable(unreachable_key)
        logger.debug("notes semantic search degraded to lexical: {}", exc)
        return {"passages": lexical, "semantic": False, "syncing": False}

    fused = _fuse(semantic_hits, lexical, limit=limit)
    return {"passages": fused, "semantic": True, "syncing": syncing}
