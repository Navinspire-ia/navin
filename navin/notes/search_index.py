# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Persistent full-text index for the Notes store."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Callable, Iterable

_SCHEMA_VERSION = 1
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


class NotesSearchIndex:
    """SQLite FTS5 index with a deterministic lexical fallback."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / ".search.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        self._initialize(connection)
        return connection

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS notes_meta ("
            "id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL,"
            "title TEXT NOT NULL, body TEXT NOT NULL, tags TEXT NOT NULL, aliases TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS index_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        version = connection.execute(
            "SELECT value FROM index_state WHERE key='version'"
        ).fetchone()
        if version is None or version[0] != str(_SCHEMA_VERSION):
            connection.execute("DELETE FROM notes_meta")
            connection.execute(
                "INSERT OR REPLACE INTO index_state(key, value) VALUES('version', ?)",
                (str(_SCHEMA_VERSION),),
            )
        fts = connection.execute(
            "SELECT value FROM index_state WHERE key='fts5'"
        ).fetchone()
        if fts is None:
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5("
                    "note_id UNINDEXED, title, body, tags, aliases, tokenize='unicode61')"
                )
                enabled = "1"
            except sqlite3.OperationalError:
                enabled = "0"
            connection.execute(
                "INSERT OR REPLACE INTO index_state(key, value) VALUES('fts5', ?)",
                (enabled,),
            )
        connection.commit()

    @staticmethod
    def _fts_enabled(connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT value FROM index_state WHERE key='fts5'"
        ).fetchone()
        return bool(row and row[0] == "1")

    def sync(
        self,
        documents: Iterable[dict[str, Any]],
        *,
        body_loader: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> dict[str, int]:
        """Transactionally upsert changed documents and remove stale rows.

        A document may omit ``body``; it is then fetched through *body_loader*
        only when its fingerprint differs from the indexed one. Callers can so
        describe the whole vault from ``stat()`` results and read just the
        notes that actually changed since the last sync.
        """
        docs = list(documents)
        current_ids = {document["id"] for document in docs}
        changed = 0
        removed = 0
        with self._connect() as connection:
            known = {
                row["id"]: row["fingerprint"]
                for row in connection.execute("SELECT id, fingerprint FROM notes_meta")
            }
            stale_ids = sorted(set(known) - current_ids)
            fts = self._fts_enabled(connection)
            for note_id in stale_ids:
                connection.execute("DELETE FROM notes_meta WHERE id=?", (note_id,))
                if fts:
                    connection.execute("DELETE FROM notes_fts WHERE note_id=?", (note_id,))
                removed += 1
            for document in docs:
                if known.get(document["id"]) == document["fingerprint"]:
                    continue
                if "body" not in document:
                    body = body_loader(document) if body_loader else None
                    if body is None:
                        continue
                    document = {**document, "body": body}
                if fts:
                    connection.execute(
                        "DELETE FROM notes_fts WHERE note_id=?", (document["id"],)
                    )
                connection.execute(
                    "INSERT OR REPLACE INTO notes_meta"
                    "(id,path,fingerprint,title,body,tags,aliases) VALUES(?,?,?,?,?,?,?)",
                    (
                        document["id"],
                        document["path"],
                        document["fingerprint"],
                        document["title"],
                        document["body"],
                        document["tags"],
                        document["aliases"],
                    ),
                )
                if fts:
                    connection.execute(
                        "INSERT INTO notes_fts(note_id,title,body,tags,aliases)"
                        " VALUES(?,?,?,?,?)",
                        (
                            document["id"],
                            document["title"],
                            document["body"],
                            document["tags"],
                            document["aliases"],
                        ),
                    )
                changed += 1
        return {"indexed": changed, "removed": removed, "total": len(docs)}

    def upsert(self, document: dict[str, str]) -> None:
        """Transactionally update one note after a store CRUD operation."""
        with self._connect() as connection:
            fts = self._fts_enabled(connection)
            if fts:
                connection.execute("DELETE FROM notes_fts WHERE note_id=?", (document["id"],))
            connection.execute(
                "INSERT OR REPLACE INTO notes_meta"
                "(id,path,fingerprint,title,body,tags,aliases) VALUES(?,?,?,?,?,?,?)",
                (
                    document["id"],
                    document["path"],
                    document["fingerprint"],
                    document["title"],
                    document["body"],
                    document["tags"],
                    document["aliases"],
                ),
            )
            if fts:
                connection.execute(
                    "INSERT INTO notes_fts(note_id,title,body,tags,aliases) VALUES(?,?,?,?,?)",
                    (
                        document["id"],
                        document["title"],
                        document["body"],
                        document["tags"],
                        document["aliases"],
                    ),
                )

    def remove(self, note_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM notes_meta WHERE id=?", (note_id,))
            if self._fts_enabled(connection):
                connection.execute("DELETE FROM notes_fts WHERE note_id=?", (note_id,))

    def mention_candidates(self, phrase: str, *, limit: int = 500) -> list[dict[str, Any]]:
        """Notes whose indexed text contains every word of *phrase*.

        Used for unlinked mentions: instead of reading every note on disk to
        grep a title, the FTS index narrows the set to a few candidates whose
        stored body is then checked exactly by the caller.
        """
        tokens = [token.lower() for token in _TOKEN_RE.findall(phrase)]
        if not tokens:
            return []
        capped = max(1, min(int(limit), 2000))
        with self._connect() as connection:
            if self._fts_enabled(connection):
                expression = " AND ".join(
                    f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens
                )
                try:
                    rows = connection.execute(
                        "SELECT m.id, m.body FROM notes_fts JOIN notes_meta m "
                        "ON m.id=notes_fts.note_id WHERE notes_fts MATCH ? LIMIT ?",
                        (expression, capped),
                    ).fetchall()
                    return [dict(row) for row in rows]
                except sqlite3.OperationalError:
                    pass
            rows = connection.execute("SELECT id, body FROM notes_meta").fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            haystack = row["body"].lower()
            if all(token in haystack for token in tokens):
                out.append(dict(row))
                if len(out) >= capped:
                    break
        return out

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        tokens = [token.lower() for token in _TOKEN_RE.findall(query)]
        if not tokens:
            return []
        capped = max(1, min(int(limit), 200))
        with self._connect() as connection:
            if self._fts_enabled(connection):
                expression = " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens)
                try:
                    rows = connection.execute(
                        "SELECT m.*, bm25(notes_fts, 0.0, 8.0, 1.0, 4.0, 4.0) AS rank "
                        "FROM notes_fts JOIN notes_meta m ON m.id=notes_fts.note_id "
                        "WHERE notes_fts MATCH ? ORDER BY rank, lower(m.title), m.id LIMIT ?",
                        (expression, capped),
                    ).fetchall()
                    return [dict(row) for row in rows]
                except sqlite3.OperationalError:
                    pass
            rows = connection.execute(
                "SELECT *, 0.0 AS rank FROM notes_meta ORDER BY lower(title), id"
            ).fetchall()
        scored: list[tuple[int, str, str, dict[str, Any]]] = []
        for row in rows:
            item = dict(row)
            title = item["title"].lower()
            haystack = " ".join(
                (title, item["body"].lower(), item["tags"].lower(), item["aliases"].lower())
            )
            if not all(token in haystack for token in tokens):
                continue
            title_hits = sum(token in title for token in tokens)
            frequency = sum(haystack.count(token) for token in tokens)
            scored.append((-title_hits, -frequency, title, item))
        scored.sort(key=lambda entry: (entry[0], entry[1], entry[2], entry[3]["id"]))
        return [entry[3] for entry in scored[:capped]]
