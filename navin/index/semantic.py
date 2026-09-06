"""Semantic (embedding) search over the project, layered on the code index.

Two choices shape everything here.

**Chunks are symbols, not windows.** A fixed-size sliding window is the usual
approach and it is the wrong one for code: it cuts functions in half, so a hit
points at a line range that means nothing on its own. The code index already
knows where every function and class starts and ends, so a chunk is one symbol,
and a hit is something the agent can act on - a name, a file, a line range.

**Ranking is hybrid.** Vector similarity alone loses to exact lexical matching
whenever the user knows the identifier, and beats it whenever they do not. The
two are fused by reciprocal rank rather than by comparing scores, because a
cosine distance and a fuzzy-match score are not on the same scale and any attempt
to weigh them directly is a tuning constant that ages badly.

Vectors live in the navin cache directory next to the other index caches, never
in the project tree, and are re-embedded per changed file using the same
``(mtime_ns, size)`` fingerprint the rest of the index uses.
"""

from __future__ import annotations

import json
import math
import tempfile
from array import array
from dataclasses import dataclass
from operator import mul
from pathlib import Path
from typing import Any

from loguru import logger

from navin.index.embeddings import EmbeddingClient, EmbeddingError
from navin.index.store import cache_path

try:  # Optional accelerator; declared nowhere, so never assumed present.
    import numpy as _np
except ImportError:  # pragma: no cover - depends on the install
    _np = None

_STORE_VERSION = 1
# A symbol body longer than this is truncated: the head of a function carries its
# meaning, and embedding endpoints charge for the tail.
_MAX_CHUNK_CHARS = 2_000
_MIN_CHUNK_CHARS = 24
# A module-level assignment carries no behaviour to describe, so its vector is
# noise that still costs a slot in the batch. Names remain findable lexically.
_THIN_KINDS = frozenset({"variable", "constant"})
# Chunks held in memory between writes to the flat buffer. Large enough that a
# repo is not indexed one HTTP request per file, small enough that the peak stays
# a few megabytes rather than the whole index.
_FLUSH_AT = 256
# Files with no symbols still matter when they are prose. Code files without
# parsed symbols are skipped instead, since their text is usually boilerplate.
_PROSE_SUFFIXES = frozenset({".md", ".mdx", ".rst", ".txt", ".adoc"})
_RRF_K = 60


@dataclass(frozen=True, slots=True)
class Chunk:
    """One embeddable unit of the project."""

    path: str
    name: str
    kind: str
    start_line: int
    end_line: int
    text: str

    @property
    def key(self) -> str:
        return f"{self.path}:{self.start_line}:{self.name}"


@dataclass(slots=True)
class _Rebuild:
    """Index being assembled during a sync, flushed batch by batch."""

    flat: array
    chunks: list[dict[str, Any]]
    dim: int
    fingerprints: dict[str, str]


@dataclass(frozen=True, slots=True)
class SemanticHit:
    path: str
    name: str
    kind: str
    start_line: int
    end_line: int
    score: float


def _normalize(vector: list[float]) -> list[float]:
    """Scale to unit length so cosine similarity is a plain dot product."""
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


class SemanticIndex:
    """Embedding index for one project root."""

    def __init__(self, root: Path, client: EmbeddingClient, *, max_chunks: int) -> None:
        self.root = root
        self.client = client
        self.max_chunks = max_chunks
        self._meta: dict[str, Any] = {}
        self._chunks: list[dict[str, Any]] = []
        # One contiguous float32 buffer, not a list of lists. On a 4,000-file
        # repo that is 49 MB instead of 561 MB: a Python float is a 24-byte
        # object plus an 8-byte pointer, so 12M of them cost more than eleven
        # times the vectors themselves, in a process that stays alive for hours.
        self._flat: array = array("f")
        self._dim = 0
        self._matrix = None
        self._loaded = False

    @property
    def _count(self) -> int:
        return len(self._chunks)

    def _row(self, position: int) -> array:
        start = position * self._dim
        return self._flat[start : start + self._dim]

    # -- persistence --------------------------------------------------------

    @property
    def _meta_path(self) -> Path:
        return cache_path(self.root).with_name(
            f"{cache_path(self.root).stem}-semantic.json"
        )

    @property
    def _vec_path(self) -> Path:
        return cache_path(self.root).with_name(
            f"{cache_path(self.root).stem}-semantic.vec"
        )

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = json.loads(self._meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if (
            not isinstance(raw, dict)
            or raw.get("version") != _STORE_VERSION
            or raw.get("signature") != self.client.signature
        ):
            # A different model or truncation means a different vector space, so
            # the old vectors are not wrong - they are meaningless here.
            return
        chunks = raw.get("chunks")
        dim = raw.get("dim")
        if not isinstance(chunks, list) or not isinstance(dim, int) or dim <= 0:
            return
        try:
            blob = self._vec_path.read_bytes()
        except OSError:
            return
        values = array("f")
        try:
            values.frombytes(blob)
        except ValueError:
            return
        if len(values) != len(chunks) * dim:
            # Torn write between the two files; rebuilding is cheap next to
            # returning vectors paired with the wrong chunks.
            logger.debug("Semantic cache for {} is inconsistent, rebuilding", self.root)
            return
        self._meta = raw
        self._chunks = chunks
        self._flat = values
        self._dim = dim

    def _save(self, dim: int, fingerprints: dict[str, str]) -> None:
        payload = {
            "version": _STORE_VERSION,
            "signature": self.client.signature,
            "dim": dim,
            "files": fingerprints,
            "chunks": self._chunks,
        }
        # Also the in-memory state. Without this a long-lived index re-embeds the
        # whole project on its second sync, because _load has already run and
        # will not re-read the fingerprints it just wrote.
        self._meta = payload
        try:
            self._vec_path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(self._vec_path, self._flat.tobytes())
            self._atomic_write(
                self._meta_path,
                json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            )
        except OSError:
            # An unwritable cache costs a re-embed next time, never correctness.
            logger.debug("Could not persist the semantic cache for {}", self.root)

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, delete=False, suffix=".tmp"
        ) as handle:
            handle.write(data)
            temp = Path(handle.name)
        temp.replace(path)

    # -- chunking -----------------------------------------------------------

    def _chunks_for_file(self, index: Any, rel: str) -> list[Chunk]:
        entry = index.entries.get(rel)
        try:
            text = (self.root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        lines = text.splitlines()

        if entry is None or not entry.symbols:
            if Path(rel).suffix.lower() not in _PROSE_SUFFIXES:
                return []
            body = text[:_MAX_CHUNK_CHARS].strip()
            if len(body) < _MIN_CHUNK_CHARS:
                return []
            return [
                Chunk(
                    path=rel,
                    name=Path(rel).name,
                    kind="document",
                    start_line=1,
                    end_line=len(lines) or 1,
                    text=f"{rel}\n\n{body}",
                )
            ]

        out: list[Chunk] = []
        for symbol in entry.symbols:
            start = max(symbol.line, 1)
            end = max(symbol.end_line or start, start)
            body = "\n".join(lines[start - 1 : end])[:_MAX_CHUNK_CHARS]
            # The path and qualified name go into the embedded text on purpose:
            # much of what makes a function findable is what it is called and
            # where it lives, and the body alone drops both.
            header = f"{rel} · {symbol.kind} {symbol.qualname or symbol.name}"
            parts = [header]
            if symbol.signature:
                parts.append(symbol.signature)
            if symbol.doc:
                parts.append(symbol.doc)
            parts.append(body)
            # Judge substance on the doc and body alone: the header is path and
            # name, which would push even ``x = 1`` past any threshold.
            substance = f"{symbol.doc}\n{body}".strip()
            if len(substance) < _MIN_CHUNK_CHARS and not symbol.doc:
                continue
            if symbol.kind in _THIN_KINDS and not symbol.doc and end - start < 2:
                continue
            blob = "\n".join(part for part in parts if part).strip()
            out.append(
                Chunk(
                    path=rel,
                    name=symbol.qualname or symbol.name,
                    kind=symbol.kind,
                    start_line=start,
                    end_line=end,
                    text=blob,
                )
            )
        return out

    # -- syncing ------------------------------------------------------------

    def _current_fingerprints(self, index: Any) -> dict[str, str]:
        current: dict[str, str] = {}
        for rel in index.all_files:
            entry = index.entries.get(rel)
            if entry is not None:
                current[rel] = f"{entry.mtime_ns}:{entry.size}"
            elif Path(rel).suffix.lower() in _PROSE_SUFFIXES:
                try:
                    stat = (self.root / rel).stat()
                except OSError:
                    continue
                current[rel] = f"{stat.st_mtime_ns}:{stat.st_size}"
        return current

    def pending_files(self, index: Any) -> int:
        """How many files sync() would have to (re-)embed or drop.

        Fingerprint comparison only - no embedding calls - so callers can
        decide to run the actual sync off the agent turn.
        """
        self._load()
        known: dict[str, str] = dict(self._meta.get("files") or {})
        current = self._current_fingerprints(index)
        changed = sum(1 for rel, fp in current.items() if known.get(rel) != fp)
        removed = sum(1 for rel in known if rel not in current)
        return changed + removed

    def has_vectors(self) -> bool:
        """Whether a previous sync left anything searchable on disk."""
        self._load()
        return bool(self._chunks)

    async def sync(self, index: Any) -> dict[str, int]:
        """Bring the vectors in line with the code index.

        Only files whose fingerprint moved are re-embedded, which is what keeps
        this affordable on a repo that is being actively edited.
        """
        self._load()
        known: dict[str, str] = dict(self._meta.get("files") or {})
        current = self._current_fingerprints(index)

        changed = [rel for rel, fp in current.items() if known.get(rel) != fp]
        removed = [rel for rel in known if rel not in current]

        if not changed and not removed:
            return {"embedded": 0, "removed": 0, "total": len(self._chunks)}

        surviving = {rel for rel in current if rel not in changed}
        keep = [
            position
            for position, chunk in enumerate(self._chunks)
            if chunk.get("path") in surviving
        ]

        state = _Rebuild(
            flat=array("f"),
            chunks=[self._chunks[position] for position in keep],
            dim=self._dim,
            fingerprints={rel: current[rel] for rel in surviving},
        )
        for position in keep:
            state.flat.extend(self._row(position))

        pending: list[Chunk] = []
        pending_files: list[str] = []
        try:
            for rel in changed:
                if len(state.chunks) + len(pending) >= self.max_chunks:
                    logger.debug(
                        "Semantic index for {} hit the {} chunk cap",
                        self.root,
                        self.max_chunks,
                    )
                    break
                pending.extend(self._chunks_for_file(index, rel))
                pending_files.append(rel)
                if len(pending) >= _FLUSH_AT:
                    await self._flush(state, pending, pending_files, current)
            if pending or pending_files:
                await self._flush(state, pending, pending_files, current)
        except EmbeddingError:
            # Keep whatever completed. On a first index of a large repo this is
            # the difference between a network blip costing one batch and costing
            # the whole spend, since the next call resumes from the fingerprints
            # already recorded.
            self._commit(state)
            raise

        embedded = len(state.chunks) - len(keep)
        self._commit(state)
        return {
            "embedded": embedded,
            "removed": len(removed),
            "total": len(state.chunks),
        }

    async def _flush(
        self,
        state: _Rebuild,
        pending: list[Chunk],
        pending_files: list[str],
        current: dict[str, str],
    ) -> None:
        """Embed the pending chunks into ``state``, then release them.

        Vectors are appended to the flat buffer and the Python lists dropped
        immediately: holding every vector until the end costs 32 bytes per float
        and turned a 49 MB index into a 561 MB resident process.
        """
        if pending:
            raw = await self.client.embed([chunk.text for chunk in pending])
            for vector in raw:
                normalized = _normalize(vector)
                if not state.dim:
                    state.dim = len(normalized)
                elif len(normalized) != state.dim:
                    raise EmbeddingError("endpoint returned vectors of differing sizes")
                state.flat.extend(normalized)
            state.chunks.extend(
                {
                    "path": chunk.path,
                    "name": chunk.name,
                    "kind": chunk.kind,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                }
                for chunk in pending
            )
        # Only now are these files fully represented in the buffer, so only now
        # is it safe to record their fingerprints as up to date.
        for rel in pending_files:
            if rel in current:
                state.fingerprints[rel] = current[rel]
        pending.clear()
        pending_files.clear()

    def _commit(self, state: _Rebuild) -> None:
        self._chunks = state.chunks
        self._flat = state.flat
        self._dim = state.dim
        self._matrix = None
        if state.dim and state.chunks:
            self._save(state.dim, state.fingerprints)
        else:
            # Nothing embeddable in this project; remember that so the walk is
            # not repeated on every query.
            self._meta = {**self._meta, "files": state.fingerprints}

    # -- querying -----------------------------------------------------------

    async def search(self, query: str, limit: int) -> list[SemanticHit]:
        """Nearest chunks to ``query`` by cosine similarity."""
        self._load()
        if not self._chunks:
            return []
        vector = _normalize((await self.client.embed([query]))[0])
        scores = self._scores(vector)
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        hits: list[SemanticHit] = []
        for i in order[:limit]:
            chunk = self._chunks[i]
            hits.append(
                SemanticHit(
                    path=str(chunk.get("path", "")),
                    name=str(chunk.get("name", "")),
                    kind=str(chunk.get("kind", "")),
                    start_line=int(chunk.get("start_line", 1)),
                    end_line=int(chunk.get("end_line", 1)),
                    score=float(scores[i]),
                )
            )
        return hits

    def _scores(self, vector: list[float]) -> list[float]:
        if not self._dim or len(vector) != self._dim:
            return [0.0] * self._count
        if _np is not None:
            if self._matrix is None:
                # frombuffer, not array: a view over the existing buffer rather
                # than a second copy of every vector.
                self._matrix = _np.frombuffer(self._flat, dtype="float32").reshape(
                    self._count, self._dim
                )
            return (self._matrix @ _np.array(vector, dtype="float32")).tolist()
        # Both sides are unit vectors, so the dot product is the cosine. Slower
        # without numpy but linear in a capped number of chunks.
        return [
            sum(map(mul, self._row(position), vector))
            for position in range(self._count)
        ]


def fuse_rankings(
    semantic: list[str],
    lexical: list[str],
    limit: int,
) -> list[str]:
    """Reciprocal-rank fusion of two ranked key lists.

    Rank, not score: a cosine similarity and a fuzzy-name score share no scale,
    and any weighting between them is a constant that would need retuning for
    every embedding model. Appearing high on either list is what counts, and
    appearing on both is what wins.
    """
    scores: dict[str, float] = {}
    for ranking in (semantic, lexical):
        for position, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + position + 1)
    ordered = sorted(scores, key=lambda key: scores[key], reverse=True)
    return ordered[:limit]
