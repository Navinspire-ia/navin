# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Persist chat artifacts as JSON metadata + content files.

Layout under ``~/.navin/artifacts/<chat_id>/``:

- ``index.json`` - ordered list of artifact ids (newest last)
- ``<id>.meta.json`` - metadata (type, title, version, timestamps, ...)
- ``<id>.html`` / ``.md`` / ``.mmd`` / ``.bin`` - content payload
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

ARTIFACT_TYPES = ("html", "markdown", "mermaid", "file")

_MAX_CONTENT_BYTES = 2 * 1024 * 1024
_MAX_TITLE_LEN = 200
_MAX_ID_LEN = 80
_MAX_ARTIFACTS = 200
_SAFE_CHAT_ID = re.compile(r"^[A-Za-z0-9_.:@+-]+$")
_SAFE_ARTIFACT_ID = re.compile(r"^[A-Za-z0-9_-]+$")

_EXT = {
    "html": ".html",
    "markdown": ".md",
    "mermaid": ".mmd",
    "file": ".bin",
}

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class ArtifactError(Exception):
    """Artifact validation or persistence failure."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def artifacts_root() -> Path:
    """Return ``~/.navin/artifacts`` (created on demand)."""
    root = Path.home() / ".navin" / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def artifacts_dir(chat_id: str) -> Path:
    """Return the directory for one chat's artifacts."""
    cleaned = _clean_chat_id(chat_id)
    path = artifacts_root() / cleaned
    path.mkdir(parents=True, exist_ok=True)
    return path


def _clean_chat_id(chat_id: str) -> str:
    value = (chat_id or "").strip()
    if not value or not _SAFE_CHAT_ID.match(value) or ".." in value or "/" in value or "\\" in value:
        raise ArtifactError("invalid chat_id", status=400)
    return value


def _clean_artifact_id(artifact_id: str | None) -> str | None:
    if artifact_id is None:
        return None
    value = str(artifact_id).strip()
    if not value:
        return None
    if len(value) > _MAX_ID_LEN or not _SAFE_ARTIFACT_ID.match(value):
        raise ArtifactError("invalid artifact id", status=400)
    return value


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id() -> str:
    return f"art-{uuid.uuid4().hex[:12]}"


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _atomic_write_json(path: Path, payload: Any) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    _atomic_write_bytes(path, encoded + b"\n")


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("artifact read failed {}: {}", path, exc)
        return None


def _content_path(directory: Path, artifact_id: str, artifact_type: str) -> Path:
    return directory / f"{artifact_id}{_EXT.get(artifact_type, '.bin')}"


def _meta_path(directory: Path, artifact_id: str) -> Path:
    return directory / f"{artifact_id}.meta.json"


def _index_path(directory: Path) -> Path:
    return directory / "index.json"


def _normalize_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw == "md":
        raw = "markdown"
    if raw not in ARTIFACT_TYPES:
        raise ArtifactError(
            f"type must be one of: {', '.join(ARTIFACT_TYPES)}",
            status=400,
        )
    return raw


def _normalize_title(value: Any, *, artifact_type: str) -> str:
    title = str(value or "").strip()
    if not title:
        title = {
            "html": "HTML artifact",
            "markdown": "Markdown artifact",
            "mermaid": "Mermaid diagram",
            "file": "File artifact",
        }.get(artifact_type, "Artifact")
    return title[:_MAX_TITLE_LEN]


def _encode_content(content: str | bytes, *, artifact_type: str) -> bytes:
    if isinstance(content, bytes):
        data = content
    else:
        data = str(content).encode("utf-8")
    if len(data) > _MAX_CONTENT_BYTES:
        raise ArtifactError("artifact content too large", status=413)
    if artifact_type != "file" and b"\x00" in data:
        raise ArtifactError("text artifact content must be UTF-8 text", status=400)
    return data


class ArtifactStore:
    """CRUD store for one chat's artifacts."""

    def __init__(self, chat_id: str, *, root: Path | None = None) -> None:
        self.chat_id = _clean_chat_id(chat_id)
        self._dir = (root / self.chat_id) if root is not None else artifacts_dir(self.chat_id)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = _lock_for(self._dir)

    @property
    def directory(self) -> Path:
        return self._dir

    def _read_index(self) -> list[str]:
        raw = _read_json(_index_path(self._dir))
        if not isinstance(raw, dict):
            return []
        ids = raw.get("ids")
        if not isinstance(ids, list):
            return []
        out: list[str] = []
        for item in ids:
            if isinstance(item, str) and _SAFE_ARTIFACT_ID.match(item):
                out.append(item)
        return out[:_MAX_ARTIFACTS]

    def _write_index(self, ids: list[str]) -> None:
        _atomic_write_json(_index_path(self._dir), {"version": 1, "ids": ids[:_MAX_ARTIFACTS]})

    def _load_meta(self, artifact_id: str) -> dict[str, Any] | None:
        raw = _read_json(_meta_path(self._dir, artifact_id))
        if not isinstance(raw, dict):
            return None
        artifact_type = raw.get("type")
        if artifact_type not in ARTIFACT_TYPES:
            return None
        return {
            "id": artifact_id,
            "chat_id": self.chat_id,
            "type": artifact_type,
            "title": str(raw.get("title") or "Artifact")[:_MAX_TITLE_LEN],
            "version": int(raw.get("version") or 1),
            "created_at": str(raw.get("created_at") or ""),
            "updated_at": str(raw.get("updated_at") or ""),
            "source_path": (
                str(raw["source_path"])
                if isinstance(raw.get("source_path"), str) and raw.get("source_path")
                else None
            ),
            "content_file": f"{artifact_id}{_EXT.get(str(artifact_type), '.bin')}",
        }

    def _read_content(self, meta: dict[str, Any]) -> str:
        path = self._dir / str(meta["content_file"])
        if not path.is_file():
            return ""
        try:
            data = path.read_bytes()
        except OSError:
            return ""
        if meta["type"] == "file":
            # Binary files are exposed as a short placeholder; export_path is
            # the real download target.
            return f"[file {len(data)} bytes]"
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")

    def list(self) -> list[dict[str, Any]]:
        """Return metadata for all artifacts (no content bodies)."""
        with self._lock:
            ids = self._read_index()
            items: list[dict[str, Any]] = []
            for artifact_id in ids:
                meta = self._load_meta(artifact_id)
                if meta is not None:
                    items.append(meta)
            return items

    def get(self, artifact_id: str, *, include_content: bool = True) -> dict[str, Any] | None:
        """Return one artifact, optionally with decoded content."""
        cleaned = _clean_artifact_id(artifact_id)
        if cleaned is None:
            return None
        with self._lock:
            meta = self._load_meta(cleaned)
            if meta is None:
                return None
            if include_content:
                meta = {**meta, "content": self._read_content(meta)}
            return meta

    def export_path(self, artifact_id: str) -> Path:
        """Return the on-disk content file path for export / download."""
        cleaned = _clean_artifact_id(artifact_id)
        if cleaned is None:
            raise ArtifactError("invalid artifact id", status=400)
        with self._lock:
            meta = self._load_meta(cleaned)
            if meta is None:
                raise ArtifactError("artifact not found", status=404)
            path = self._dir / str(meta["content_file"])
            if not path.is_file():
                raise ArtifactError("artifact content missing", status=404)
            return path

    def upsert(
        self,
        *,
        artifact_type: str,
        title: str | None = None,
        content: str | bytes = "",
        artifact_id: str | None = None,
        source_path: str | None = None,
    ) -> dict[str, Any]:
        """Create or update an artifact; returns the full record with content."""
        kind = _normalize_type(artifact_type)
        cleaned_id = _clean_artifact_id(artifact_id) or _new_id()
        title_clean = _normalize_title(title, artifact_type=kind)
        data = _encode_content(content, artifact_type=kind)
        source = (
            str(source_path).strip()[:1000]
            if isinstance(source_path, str) and source_path.strip()
            else None
        )
        now = _now_iso()

        with self._lock:
            existing = self._load_meta(cleaned_id)
            if existing is not None and existing["type"] != kind:
                # Type changes would leave orphan content files; refuse.
                raise ArtifactError(
                    f"artifact {cleaned_id} already exists with type {existing['type']}",
                    status=409,
                )
            version = int(existing["version"]) + 1 if existing else 1
            created_at = str(existing["created_at"]) if existing and existing.get("created_at") else now
            meta = {
                "id": cleaned_id,
                "chat_id": self.chat_id,
                "type": kind,
                "title": title_clean,
                "version": version,
                "created_at": created_at,
                "updated_at": now,
                "source_path": source,
                "content_file": f"{cleaned_id}{_EXT[kind]}",
            }
            _atomic_write_json(_meta_path(self._dir, cleaned_id), meta)
            _atomic_write_bytes(_content_path(self._dir, cleaned_id, kind), data)

            ids = self._read_index()
            if cleaned_id in ids:
                ids = [i for i in ids if i != cleaned_id]
            ids.append(cleaned_id)
            if len(ids) > _MAX_ARTIFACTS:
                # Drop oldest entries from the index (files kept for safety).
                ids = ids[-_MAX_ARTIFACTS:]
            self._write_index(ids)

            body = self._read_content(meta)
            return {**meta, "content": body}
