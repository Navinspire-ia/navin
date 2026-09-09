# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Markdown-file notes store for the Notes module.

Every note is a plain ``.md`` file with a YAML frontmatter, stored in real
folders under ``~/.navin/notes/``. That layout is the product decision, not an
implementation detail: notes stay portable (Obsidian-compatible), the agent can
read and write them with its normal file tools, and search/backlinks work by
parsing text.

Layout::

    <root>/<folder...>/<slug>.md   notes (frontmatter + markdown body)
    <root>/_files/<hash>-<name>    attachments
    <root>/_templates/<name>.md    note templates
    <root>/.trash/<id>.md          soft-deleted notes
    <root>/.history/<id>/<ts>.md   edit snapshots (rotation-capped)

An atomic id-to-path manifest makes direct reads constant-time. Full-text
search uses a persistent SQLite FTS5 index with a lexical fallback. Both are
rebuilt from the portable Markdown source of truth, and file fingerprints pick
up external edits made by an agent, editor, or sync tool.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import functools
import hashlib
import json
import mimetypes
import os
import re
import tempfile
import threading
import unicodedata
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml

from navin.config.paths import get_runtime_subdir
from navin.utils.atomic_io import InterProcessLock, atomic_write_bytes, atomic_write_text

NOTE_EXTENSION = ".md"
TRASH_DIR = ".trash"
HISTORY_DIR = ".history"
FILES_DIR = "_files"
TEMPLATES_DIR = "_templates"
# Never listed as note folders and never valid note destinations.
RESERVED_DIRS = frozenset({TRASH_DIR, HISTORY_DIR, FILES_DIR, TEMPLATES_DIR})
MANIFEST_FILE = ".notes-manifest.json"
LOCK_FILE = ".notes.lock"
MANIFEST_VERSION = 1

_MAX_NOTE_BYTES = 2_000_000
_MAX_ATTACHMENT_BYTES = 50_000_000
_MAX_FOLDER_DEPTH = 6
_MAX_HISTORY_SNAPSHOTS = 50
_MAX_TITLE_LEN = 300
_MAX_TAGS = 32
_DEFAULT_PAGE = 50
_MAX_PAGE = 200
_MAX_PROPS = 32
_MAX_PROP_KEY_LEN = 64
_MAX_PROP_STR_LEN = 1000
_MAX_PROP_LIST = 20
_MAX_VIEWS = 50
VIEWS_FILE = "_views.json"
_ROOT_LOCKS: dict[str, threading.RLock] = {}
_ROOT_LOCKS_GUARD = threading.Lock()
_LOCK_STATE = threading.local()
# Frontmatter keys owned by the store itself; custom properties may not
# shadow them (they live under the ``props`` mapping).
_RESERVED_META_KEYS = frozenset(
    {
        "id",
        "title",
        "tags",
        "aliases",
        "created",
        "updated",
        "pinned",
        "archived",
        "props",
        "trashed_from",
        "trashed_at",
    }
)

_WIKILINK_RE = re.compile(r"\[\[([^\[\]|#\n]+)(?:#[^\[\]|\n]*)?(?:\|[^\[\]\n]*)?\]\]")
_TASK_RE = re.compile(r"^(\s*)[-*] \[( |x|X)\] (.*)$")
_TASK_DUE_RE = re.compile(r"@due\((\d{4}-\d{2}-\d{2})\)")
_TASK_PRIORITY_RE = re.compile(r"(?<!\w)!p([1-3])(?!\w)")
_HASHTAG_RE = re.compile(r"(?<!\w)#([\w][\w/-]{0,48})")
_FENCE_RE = re.compile(r"^(```|~~~)")


class NotesError(Exception):
    """API-facing error carrying an HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class NoteConflictError(NotesError):
    """The note changed on disk since the editor loaded it."""

    def __init__(self, message: str = "note changed since it was loaded") -> None:
        super().__init__(409, message)


def _root_key(root: Path) -> str:
    return os.path.normcase(str(root.resolve(strict=False)))


@contextlib.contextmanager
def _notes_transaction(root: Path) -> Iterator[None]:
    """Serialize a complete read/check/write transaction across processes."""
    key = _root_key(root)
    with _ROOT_LOCKS_GUARD:
        thread_lock = _ROOT_LOCKS.setdefault(key, threading.RLock())
    with thread_lock:
        depths = getattr(_LOCK_STATE, "depths", {})
        depth = depths.get(key, 0)
        if depth:
            depths[key] = depth + 1
            _LOCK_STATE.depths = depths
            try:
                yield
            finally:
                depths[key] -= 1
            return
        root.mkdir(parents=True, exist_ok=True)
        with InterProcessLock(root / LOCK_FILE, timeout=30):
            depths[key] = 1
            _LOCK_STATE.depths = depths
            try:
                yield
            finally:
                depths.pop(key, None)


def _transactional(function: Any) -> Any:
    @functools.wraps(function)
    def wrapped(root: Path, *args: Any, **kwargs: Any) -> Any:
        with _notes_transaction(root):
            return function(root, *args, **kwargs)

    return wrapped


def notes_root() -> Path:
    return get_runtime_subdir("notes")


def _now_iso() -> str:
    # Full microsecond precision: ``updated`` doubles as the edit-conflict
    # token, so two consecutive saves must never produce the same value.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _slugify(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", title)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    return slug[:80] or "note"


def clean_folder(folder: str | None) -> str:
    """Validate and normalize a relative folder path (empty = root)."""
    raw = (folder or "").strip().replace("\\", "/")
    if not raw:
        return ""
    parts: list[str] = []
    for part in raw.split("/"):
        part = part.strip()
        if not part:
            continue
        if part.startswith(".") or part in RESERVED_DIRS or part in {"..", "."}:
            raise NotesError(400, f"invalid folder segment: {part!r}")
        if len(part) > 120:
            raise NotesError(400, "folder segment too long")
        parts.append(part)
    if len(parts) > _MAX_FOLDER_DEPTH:
        raise NotesError(400, "folder too deep")
    return "/".join(parts)


def _note_dir(root: Path, folder: str) -> Path:
    target = (root / folder) if folder else root
    resolved = target.resolve(strict=False)
    if resolved != root.resolve(strict=False) and root.resolve(strict=False) not in resolved.parents:
        raise NotesError(400, "folder escapes the notes root")
    return target


# ---------------------------------------------------------------------------
# Frontmatter


def parse_note_text(text: str) -> tuple[dict[str, Any], str]:
    """Split a note file into (frontmatter dict, markdown body)."""
    if not text.startswith("---\n") and text.strip() != "---":
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    raw_meta = text[4:end]
    # Body starts after the closing delimiter line; leading blank lines are
    # serializer padding, not content, so the round trip stays exact.
    body = text[end + 4 :].lstrip("\n")
    try:
        loaded = yaml.load(raw_meta, Loader=_YAML_LOADER)
    except yaml.YAMLError:
        return {}, text
    return (loaded if isinstance(loaded, dict) else {}), body


# libyaml parses frontmatter about ten times faster than the pure-Python
# loader; same safe constructor set, so the result is identical.
_YAML_LOADER: Any = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def serialize_note(meta: dict[str, Any], body: str) -> str:
    ordered_keys = [
        "id",
        "title",
        "tags",
        "aliases",
        "created",
        "updated",
        "pinned",
        "archived",
    ]
    ordered = {key: meta[key] for key in ordered_keys if key in meta}
    for key, value in meta.items():
        if key not in ordered:
            ordered[key] = value
    dumped = yaml.safe_dump(
        ordered, sort_keys=False, allow_unicode=True, default_flow_style=None
    ).strip()
    return f"---\n{dumped}\n---\n\n{body.lstrip(chr(10))}"


def _clean_tags(tags: Any) -> list[str]:
    if not isinstance(tags, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        cleaned = str(tag).strip().lstrip("#").lower()[:64]
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
        if len(out) >= _MAX_TAGS:
            break
    return out


def _clean_aliases(aliases: Any) -> list[str]:
    if not isinstance(aliases, (list, tuple)):
        return []
    out = [str(a).strip()[:200] for a in aliases if str(a).strip()]
    return out[:16]


def _clean_prop_key(key: Any) -> str:
    cleaned = str(key).strip()[:_MAX_PROP_KEY_LEN]
    if not cleaned or cleaned.lower() in _RESERVED_META_KEYS:
        return ""
    return cleaned


def _clean_prop_value(value: Any) -> Any:
    """A property value: scalar or a flat list of strings (multi-select)."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (list, tuple)):
        items = [str(item).strip()[:_MAX_PROP_STR_LEN] for item in value]
        return [item for item in items if item][:_MAX_PROP_LIST]
    return str(value).strip()[:_MAX_PROP_STR_LEN]


def _clean_props(props: Any) -> dict[str, Any]:
    if not isinstance(props, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in props.items():
        cleaned_key = _clean_prop_key(key)
        if not cleaned_key:
            continue
        cleaned_value = _clean_prop_value(value)
        if cleaned_value is None or cleaned_value == "" or cleaned_value == []:
            continue
        out[cleaned_key] = cleaned_value
        if len(out) >= _MAX_PROPS:
            break
    return out


# ---------------------------------------------------------------------------
# Scanning (mtime-cached)

# path -> (mtime_ns, size, info dict, parsed tasks)
_scan_cache: dict[str, tuple[int, int, dict[str, Any], list[dict[str, Any]]]] = {}


# Colored text is stored as inline HTML (<span style>, <mark>); snippets must
# show the words, not the tags.
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def _snippet_of(body: str) -> str:
    lines: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if _FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        stripped = _HTML_TAG_RE.sub("", line).strip().lstrip("#").strip()
        if stripped:
            lines.append(stripped)
        if sum(len(entry) for entry in lines) > 240:
            break
    return " ".join(lines)[:240]


def extract_tasks(body: str) -> list[dict[str, Any]]:
    """All checkbox tasks in a markdown body with inline metadata."""
    tasks: list[dict[str, Any]] = []
    in_fence = False
    for line_no, line in enumerate(body.splitlines(), start=1):
        if _FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _TASK_RE.match(line)
        if not match:
            continue
        text = match.group(3).strip()
        due = _TASK_DUE_RE.search(text)
        priority = _TASK_PRIORITY_RE.search(text)
        tasks.append(
            {
                "line": line_no,
                "done": match.group(2).lower() == "x",
                "text": text,
                "due": due.group(1) if due else None,
                "priority": int(priority.group(1)) if priority else None,
                "tags": _HASHTAG_RE.findall(text),
            }
        )
    return tasks


def extract_wikilinks(body: str) -> list[str]:
    """Ordered unique wikilink targets (``[[Title]]``, ``[[Title|label]]``)."""
    out: list[str] = []
    seen: set[str] = set()
    for match in _WIKILINK_RE.finditer(body):
        target = match.group(1).strip()
        key = target.lower()
        if target and key not in seen:
            seen.add(key)
            out.append(target)
    return out


def _read_note_file(root: Path, path: Path) -> dict[str, Any] | None:
    entry = _read_note_entry(root, path)
    return entry[0] if entry else None


def _read_note_entry(
    root: Path, path: Path
) -> tuple[dict[str, Any], str, list[dict[str, Any]]] | None:
    """``(info, fingerprint, tasks)`` for one note, from the scan cache when the
    file is unchanged. One ``stat()`` per call; the file is read only when its
    mtime or size moved since the last parse."""
    try:
        stat = path.stat()
    except OSError:
        return None
    cache_key = str(path)
    fingerprint = f"{stat.st_mtime_ns}:{stat.st_size}"
    cached = _scan_cache.get(cache_key)
    if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2], fingerprint, cached[3]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    meta, body = parse_note_text(text)
    rel = path.relative_to(root).as_posix()
    folder = str(Path(rel).parent.as_posix())
    if folder == ".":
        folder = ""
    tasks = extract_tasks(body)
    info = {
        "id": str(meta.get("id") or "") or _fallback_id(rel),
        "path": rel,
        "folder": folder,
        "title": str(meta.get("title") or Path(rel).stem),
        "tags": _clean_tags(meta.get("tags")),
        "aliases": _clean_aliases(meta.get("aliases")),
        "created": str(meta.get("created") or ""),
        "updated": str(meta.get("updated") or ""),
        "pinned": bool(meta.get("pinned")),
        "archived": bool(meta.get("archived")),
        "props": _clean_props(meta.get("props")),
        "snippet": _snippet_of(body),
        "tasks_open": sum(1 for task in tasks if not task["done"]),
        "tasks_done": sum(1 for task in tasks if task["done"]),
        "links": extract_wikilinks(body),
    }
    _scan_cache[cache_key] = (stat.st_mtime_ns, stat.st_size, info, tasks)
    return info, fingerprint, tasks


def _fallback_id(rel_path: str) -> str:
    """Stable id for files created outside the API (agent, editor, import)."""
    return "f" + hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:12]


def _iter_note_paths(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    out: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            # scandir answers is_dir/is_file from the directory entry itself
            # (no stat per entry); iterdir + is_dir() + is_file() cost two.
            with os.scandir(current) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            try:
                if entry.is_dir():
                    if name.startswith(".") or name in RESERVED_DIRS:
                        continue
                    stack.append(Path(entry.path))
                elif name.endswith(NOTE_EXTENSION) and entry.is_file():
                    out.append(Path(entry.path))
            except OSError:
                continue
    return out


def scan_notes(root: Path) -> list[dict[str, Any]]:
    infos = []
    for path in _iter_note_paths(root):
        info = _read_note_file(root, path)
        if info is not None:
            infos.append(info)
    return infos


def _file_fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _manifest_path(root: Path) -> Path:
    return root / MANIFEST_FILE


def _read_manifest(root: Path) -> dict[str, str] | None:
    try:
        payload = json.loads(_manifest_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != MANIFEST_VERSION:
        return None
    entries = payload.get("notes")
    if not isinstance(entries, dict):
        return None
    return {
        str(note_id): str(rel)
        for note_id, rel in entries.items()
        if isinstance(note_id, str) and isinstance(rel, str)
    }


def _write_manifest(root: Path, entries: dict[str, str]) -> None:
    payload = {
        "version": MANIFEST_VERSION,
        "notes": dict(sorted(entries.items())),
    }
    atomic_write_text(
        _manifest_path(root),
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )


def rebuild_manifest(root: Path) -> dict[str, Any]:
    """Rebuild the id-to-path migration manifest from an existing vault."""
    with _notes_transaction(root):
        entries: dict[str, str] = {}
        duplicates: list[str] = []
        for path in _iter_note_paths(root):
            info = _read_note_file(root, path)
            if info is None:
                continue
            if info["id"] in entries:
                duplicates.append(info["id"])
                continue
            entries[info["id"]] = info["path"]
        _write_manifest(root, entries)
        return {"ok": True, "total": len(entries), "duplicates": sorted(set(duplicates))}


def _safe_manifest_path(root: Path, rel: str) -> Path | None:
    candidate = root / Path(rel.replace("\\", "/"))
    resolved_root = root.resolve(strict=False)
    resolved = candidate.resolve(strict=False)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        return None
    return candidate


def _manifest_set(root: Path, note_id: str, rel: str | None) -> None:
    entries = _read_manifest(root)
    if entries is None:
        rebuild_manifest(root)
        entries = _read_manifest(root) or {}
    if rel is None:
        if note_id not in entries:
            return
        entries.pop(note_id, None)
    else:
        # A plain edit keeps id -> path: rewriting (and fsyncing) the whole
        # manifest for every autosave was a quarter of the save cost.
        if entries.get(note_id) == rel:
            return
        entries[note_id] = rel
    _write_manifest(root, entries)


def _find_note(root: Path, note_id: str) -> tuple[Path, dict[str, Any]]:
    cleaned = (note_id or "").strip()
    if not cleaned:
        raise NotesError(400, "missing note id")
    entries = _read_manifest(root)
    if entries is None:
        rebuild_manifest(root)
        entries = _read_manifest(root) or {}
    rel = entries.get(cleaned)
    if rel:
        path = _safe_manifest_path(root, rel)
        info = _read_note_file(root, path) if path is not None else None
        if info is not None and info["id"] == cleaned:
            return path, info
    # External moves or edits can stale the manifest. Rebuild once, then use
    # the direct lookup again. Normal reads remain O(1).
    rebuild_manifest(root)
    entries = _read_manifest(root) or {}
    rel = entries.get(cleaned)
    path = _safe_manifest_path(root, rel) if rel else None
    info = _read_note_file(root, path) if path is not None else None
    if info is not None and info["id"] == cleaned:
        return path, info
    raise NotesError(404, "note not found")


def _search_documents(root: Path) -> list[dict[str, Any]]:
    """Index descriptors for every note, without reading any file.

    Each descriptor carries the ``stat()`` fingerprint and a ``_path`` the
    index uses (through :func:`_load_document_body`) to read the body only for
    notes whose fingerprint differs from the indexed one. A search on a vault
    of thousands of notes used to re-read and re-parse all of them first.
    """
    documents: list[dict[str, Any]] = []
    for path in _iter_note_paths(root):
        entry = _read_note_entry(root, path)
        if entry is None:
            continue
        info, fingerprint, _tasks = entry
        documents.append(
            {
                "id": info["id"],
                "path": info["path"],
                "fingerprint": fingerprint,
                "title": info["title"],
                "tags": " ".join(info["tags"]),
                "aliases": " ".join(info["aliases"]),
                "_path": path,
            }
        )
    return documents


def _load_document_body(document: dict[str, Any]) -> str | None:
    path = document.get("_path")
    if not isinstance(path, Path):
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return parse_note_text(text)[1]


def rebuild_search_index(root: Path) -> dict[str, int]:
    from navin.notes.search_index import NotesSearchIndex

    with _notes_transaction(root):
        index = NotesSearchIndex(root)
        try:
            index.path.unlink()
        except FileNotFoundError:
            pass
        return index.sync(_search_documents(root), body_loader=_load_document_body)


def _sync_search_index(root: Path) -> dict[str, int]:
    from navin.notes.search_index import NotesSearchIndex

    return NotesSearchIndex(root).sync(
        _search_documents(root), body_loader=_load_document_body
    )


def _index_note(root: Path, path: Path, info: dict[str, Any], body: str) -> None:
    from navin.notes.search_index import NotesSearchIndex

    NotesSearchIndex(root).upsert(
        {
            "id": info["id"],
            "path": info["path"],
            "fingerprint": _file_fingerprint(path),
            "title": info["title"],
            "body": body,
            "tags": " ".join(info["tags"]),
            "aliases": " ".join(info["aliases"]),
        }
    )


def _unindex_note(root: Path, note_id: str) -> None:
    from navin.notes.search_index import NotesSearchIndex

    NotesSearchIndex(root).remove(note_id)


# ---------------------------------------------------------------------------
# Listing


_SORTS = {
    "updated_desc": lambda info: (info["updated"] or info["created"] or ""),
    "updated_asc": lambda info: (info["updated"] or info["created"] or ""),
    "created_desc": lambda info: (info["created"] or ""),
    "created_asc": lambda info: (info["created"] or ""),
    "title_asc": lambda info: info["title"].lower(),
    "title_desc": lambda info: info["title"].lower(),
}


def list_notes(
    root: Path,
    *,
    folder: str | None = None,
    tag: str | None = None,
    query: str | None = None,
    sort: str = "updated_desc",
    archived: bool = False,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    infos = scan_notes(root)
    cleaned_folder = clean_folder(folder) if folder else None
    if cleaned_folder is not None:
        infos = [
            info
            for info in infos
            if info["folder"] == cleaned_folder
            or info["folder"].startswith(cleaned_folder + "/")
        ]
    if tag:
        wanted = tag.strip().lstrip("#").lower()
        infos = [info for info in infos if wanted in info["tags"]]
    infos = [info for info in infos if info["archived"] == archived]
    if query:
        ranked_ids = [
            result["id"]
            for result in search_notes(root, query, limit=_MAX_PAGE)["results"]
        ]
        wanted = set(ranked_ids)
        infos = [info for info in infos if info["id"] in wanted]
    sort_key = _SORTS.get(sort, _SORTS["updated_desc"])
    reverse = sort.endswith("_desc")
    infos.sort(key=sort_key, reverse=reverse)
    # Pinned notes float to the top of every listing.
    infos.sort(key=lambda info: not info["pinned"])

    page = max(1, min(int(limit or _DEFAULT_PAGE), _MAX_PAGE))
    try:
        offset = max(0, int(cursor or "0"))
    except ValueError:
        offset = 0
    window = infos[offset : offset + page]
    next_cursor = str(offset + page) if offset + page < len(infos) else None
    return {"notes": window, "next_cursor": next_cursor, "total": len(infos)}


# ---------------------------------------------------------------------------
# CRUD


def _unique_note_path(root: Path, folder: str, title: str) -> Path:
    directory = _note_dir(root, folder)
    directory.mkdir(parents=True, exist_ok=True)
    slug = _slugify(title)
    candidate = directory / f"{slug}{NOTE_EXTENSION}"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{slug}-{counter}{NOTE_EXTENSION}"
        counter += 1
    return candidate


def _write_note_file(path: Path, meta: dict[str, Any], body: str) -> None:
    text = serialize_note(meta, body)
    if len(text.encode("utf-8")) > _MAX_NOTE_BYTES:
        raise NotesError(413, "note too large")
    atomic_write_text(path, text)
    # File mtime granularity can be a full timer tick (~1ms on Linux): two
    # quick same-size writes would otherwise leave stale metadata in the scan
    # cache and break edit-conflict detection.
    _scan_cache.pop(str(path), None)


@_transactional
def create_note(
    root: Path,
    *,
    title: str,
    folder: str = "",
    markdown: str = "",
    template: str | None = None,
) -> dict[str, Any]:
    cleaned_title = (title or "").strip()[:_MAX_TITLE_LEN] or "Untitled"
    cleaned_folder = clean_folder(folder)
    body = markdown or ""
    if template:
        template_path = root / TEMPLATES_DIR / f"{_slugify(template)}{NOTE_EXTENSION}"
        if template_path.is_file():
            _, template_body = parse_note_text(
                template_path.read_text(encoding="utf-8", errors="replace")
            )
            now_local = datetime.now()
            body = template_body.replace("{{date}}", now_local.strftime("%Y-%m-%d"))
            body = body.replace("{{title}}", cleaned_title)
    now = _now_iso()
    meta = {
        "id": uuid.uuid4().hex[:12],
        "title": cleaned_title,
        "tags": [],
        "created": now,
        "updated": now,
        "pinned": False,
        "archived": False,
    }
    path = _unique_note_path(root, cleaned_folder, cleaned_title)
    _write_note_file(path, meta, body)
    _manifest_set(root, meta["id"], path.relative_to(root).as_posix())
    info = _read_note_file(root, path)
    assert info is not None
    _index_note(root, path, info, body)
    return {"note": info, "markdown": body}


def get_note(root: Path, note_id: str) -> dict[str, Any]:
    path, info = _find_note(root, note_id)
    text = path.read_text(encoding="utf-8", errors="replace")
    _, body = parse_note_text(text)
    return {"note": info, "markdown": body, "backlinks": note_backlinks(root, note_id)}


def _snapshot_note(root: Path, note_id: str, path: Path) -> None:
    try:
        current = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    history_dir = root / HISTORY_DIR / note_id
    history_dir.mkdir(parents=True, exist_ok=True)
    snapshots = sorted(history_dir.glob(f"*{NOTE_EXTENSION}"))
    if snapshots:
        try:
            if snapshots[-1].read_text(encoding="utf-8", errors="replace") == current:
                return
        except OSError:
            pass
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    atomic_write_text(history_dir / f"{stamp}{NOTE_EXTENSION}", current)
    snapshots = sorted(history_dir.glob(f"*{NOTE_EXTENSION}"))
    for stale in snapshots[:-_MAX_HISTORY_SNAPSHOTS]:
        try:
            stale.unlink()
        except OSError:
            pass


def list_history(root: Path, note_id: str) -> list[dict[str, Any]]:
    history_dir = root / HISTORY_DIR / note_id
    if not history_dir.is_dir():
        return []
    out = []
    for path in sorted(history_dir.glob(f"*{NOTE_EXTENSION}"), reverse=True):
        out.append({"stamp": path.stem, "size": path.stat().st_size})
    return out


def get_history_snapshot(root: Path, note_id: str, stamp: str) -> dict[str, Any]:
    cleaned = re.sub(r"[^0-9TZ]", "", stamp or "")
    path = root / HISTORY_DIR / note_id / f"{cleaned}{NOTE_EXTENSION}"
    if not path.is_file():
        raise NotesError(404, "snapshot not found")
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_note_text(text)
    return {"stamp": cleaned, "markdown": body, "title": str(meta.get("title") or "")}


def _rewrite_wikilinks(root: Path, old_title: str, new_title: str) -> int:
    """Point ``[[old_title]]`` links at the renamed note. Returns notes touched."""
    if old_title.strip().lower() == new_title.strip().lower():
        return 0
    pattern = re.compile(
        r"\[\[" + re.escape(old_title.strip()) + r"(?=[\]|#])", re.IGNORECASE
    )
    touched = 0
    for path in _iter_note_paths(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        replaced = pattern.sub("[[" + new_title.strip(), text)
        if replaced != text:
            atomic_write_text(path, replaced)
            _scan_cache.pop(str(path), None)
            touched += 1
    return touched


@_transactional
def update_note(
    root: Path,
    note_id: str,
    *,
    markdown: str | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    pinned: bool | None = None,
    archived: bool | None = None,
    folder: str | None = None,
    props: dict[str, Any] | None = None,
    base_updated: str | None = None,
) -> dict[str, Any]:
    path, info = _find_note(root, note_id)
    if base_updated is not None and info["updated"] and base_updated != info["updated"]:
        raise NoteConflictError()
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_note_text(text)
    meta.setdefault("id", info["id"])
    meta.setdefault("created", info["created"] or _now_iso())

    if markdown is not None and markdown != body:
        _snapshot_note(root, info["id"], path)
        body = markdown
    old_title = str(meta.get("title") or info["title"])
    if title is not None:
        cleaned_title = title.strip()[:_MAX_TITLE_LEN] or "Untitled"
        if cleaned_title != old_title:
            _rewrite_wikilinks(root, old_title, cleaned_title)
        meta["title"] = cleaned_title
    if tags is not None:
        meta["tags"] = _clean_tags(tags)
    if pinned is not None:
        meta["pinned"] = bool(pinned)
    if archived is not None:
        meta["archived"] = bool(archived)
    if props is not None:
        # Merge semantics: null/empty values delete the key, others upsert.
        merged = _clean_props(meta.get("props"))
        for key, value in props.items():
            cleaned_key = _clean_prop_key(key)
            if not cleaned_key:
                continue
            cleaned_value = _clean_prop_value(value)
            if cleaned_value is None or cleaned_value == "" or cleaned_value == []:
                merged.pop(cleaned_key, None)
            else:
                merged[cleaned_key] = cleaned_value
        merged = dict(list(merged.items())[:_MAX_PROPS])
        if merged:
            meta["props"] = merged
        else:
            meta.pop("props", None)
    meta["updated"] = _now_iso()

    target_path = path
    if folder is not None:
        cleaned_folder = clean_folder(folder)
        if cleaned_folder != info["folder"]:
            target_path = _unique_note_path(
                root, cleaned_folder, str(meta.get("title") or info["title"])
            )
    elif title is not None and _slugify(str(meta["title"])) != _slugify(old_title):
        target_path = _unique_note_path(root, info["folder"], str(meta["title"]))

    _write_note_file(path, meta, body)
    if target_path != path:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, target_path)
        _scan_cache.pop(str(path), None)
    _manifest_set(root, info["id"], target_path.relative_to(root).as_posix())
    updated_info = _read_note_file(root, target_path)
    assert updated_info is not None
    _index_note(root, target_path, updated_info, body)
    if title is not None and str(meta.get("title") or "") != old_title:
        _sync_search_index(root)
    return {"note": updated_info, "markdown": body}


@_transactional
def delete_note(root: Path, note_id: str) -> dict[str, Any]:
    path, info = _find_note(root, note_id)
    trash_dir = root / TRASH_DIR
    trash_dir.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_note_text(text)
    meta["trashed_from"] = info["folder"]
    meta["trashed_at"] = _now_iso()
    target = trash_dir / f"{info['id']}{NOTE_EXTENSION}"
    atomic_write_text(target, serialize_note(meta, body))
    path.unlink()
    _scan_cache.pop(str(path), None)
    _manifest_set(root, info["id"], None)
    _unindex_note(root, info["id"])
    return {"ok": True, "id": info["id"]}


def list_trash(root: Path) -> list[dict[str, Any]]:
    trash_dir = root / TRASH_DIR
    if not trash_dir.is_dir():
        return []
    out = []
    for path in sorted(trash_dir.glob(f"*{NOTE_EXTENSION}")):
        meta, body = parse_note_text(
            path.read_text(encoding="utf-8", errors="replace")
        )
        out.append(
            {
                "id": str(meta.get("id") or path.stem),
                "title": str(meta.get("title") or path.stem),
                "trashed_from": str(meta.get("trashed_from") or ""),
                "trashed_at": str(meta.get("trashed_at") or ""),
                "snippet": _snippet_of(body),
            }
        )
    out.sort(key=lambda item: item["trashed_at"], reverse=True)
    return out


@_transactional
def purge_note(root: Path, note_id: str) -> dict[str, Any]:
    """Permanently delete a trashed note (file + edit history)."""
    cleaned = (note_id or "").strip()
    trash_path = root / TRASH_DIR / f"{cleaned}{NOTE_EXTENSION}"
    if not cleaned or not trash_path.is_file():
        raise NotesError(404, "note not found in trash")
    trash_path.unlink()
    history_dir = root / HISTORY_DIR / cleaned
    if history_dir.is_dir():
        for snapshot in history_dir.glob(f"*{NOTE_EXTENSION}"):
            try:
                snapshot.unlink()
            except OSError:
                pass
        try:
            history_dir.rmdir()
        except OSError:
            pass
    return {"ok": True, "id": cleaned}


@_transactional
def empty_trash(root: Path) -> dict[str, Any]:
    """Permanently delete every trashed note."""
    purged = 0
    for item in list_trash(root):
        purge_note(root, item["id"])
        purged += 1
    return {"ok": True, "purged": purged}


@_transactional
def restore_note(root: Path, note_id: str) -> dict[str, Any]:
    trash_path = root / TRASH_DIR / f"{(note_id or '').strip()}{NOTE_EXTENSION}"
    if not trash_path.is_file():
        raise NotesError(404, "note not found in trash")
    meta, body = parse_note_text(
        trash_path.read_text(encoding="utf-8", errors="replace")
    )
    folder = clean_folder(str(meta.pop("trashed_from", "") or ""))
    meta.pop("trashed_at", None)
    meta["updated"] = _now_iso()
    target = _unique_note_path(root, folder, str(meta.get("title") or "Untitled"))
    _write_note_file(target, meta, body)
    trash_path.unlink()
    _manifest_set(root, str(meta.get("id") or note_id), target.relative_to(root).as_posix())
    info = _read_note_file(root, target)
    assert info is not None
    _index_note(root, target, info, body)
    return {"note": info}


# ---------------------------------------------------------------------------
# Vault import


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    total = 0
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            name = member.filename.replace("\\", "/")
            parts = [part for part in name.split("/") if part]
            if (
                not parts
                or name.startswith("/")
                or any(part in {".", ".."} for part in parts)
                or member.is_dir()
            ):
                continue
            total += member.file_size
            if total > 500_000_000:
                raise NotesError(413, "vault archive too large")
            target = destination.joinpath(*parts)
            resolved = target.resolve(strict=False)
            if destination.resolve(strict=False) not in resolved.parents:
                raise NotesError(400, "vault archive contains an unsafe path")
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source_handle:
                atomic_write_bytes(target, source_handle.read())


def _vault_markdown_paths(source: Path) -> list[Path]:
    paths: list[Path] = []
    for current, directories, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(
            name
            for name in directories
            if not name.startswith(".")
            and name not in RESERVED_DIRS
            and not (current_path / name).is_symlink()
        )
        for name in sorted(files):
            path = current_path / name
            if path.is_symlink() or path.suffix.lower() != NOTE_EXTENSION:
                continue
            resolved = path.resolve(strict=True)
            if source.resolve(strict=True) not in resolved.parents:
                raise NotesError(400, "vault file escapes the import root")
            paths.append(path)
    return paths


@_transactional
def import_vault(
    root: Path,
    source: str | os.PathLike[str],
    *,
    conflict: str = "rename",
) -> dict[str, Any]:
    """Import a Markdown, Obsidian, or Notion-export vault.

    ``conflict`` is one of ``rename`` (default), ``skip``, or ``overwrite``.
    Zip exports are extracted into a sandbox before files are considered.
    """
    strategy = (conflict or "rename").strip().lower()
    if strategy not in {"rename", "skip", "overwrite"}:
        raise NotesError(400, "invalid import conflict strategy")
    source_path = Path(source).expanduser()
    if not source_path.exists() or source_path.is_symlink():
        raise NotesError(404, "vault source not found")

    temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        if source_path.is_file():
            if source_path.suffix.lower() == ".zip":
                temporary = tempfile.TemporaryDirectory(prefix="navin-notes-import-")
                import_root = Path(temporary.name)
                _safe_extract_zip(source_path, import_root)
            elif source_path.suffix.lower() == NOTE_EXTENSION:
                import_root = source_path.parent
                candidates = [source_path]
            else:
                raise NotesError(400, "vault source must be a directory, markdown, or zip")
        elif source_path.is_dir():
            import_root = source_path.resolve(strict=True)
        else:
            raise NotesError(400, "unsupported vault source")
        if "candidates" not in locals():
            candidates = _vault_markdown_paths(import_root)

        imported = 0
        skipped = 0
        overwritten = 0
        known_ids = set((_read_manifest(root) or {}).keys())
        for source_note in candidates:
            rel = source_note.relative_to(import_root).as_posix()
            folder_parts = [
                part
                for part in Path(rel).parent.parts
                if part not in {"", "."} and not part.startswith(".")
            ]
            folder = clean_folder("/".join(folder_parts))
            raw = source_note.read_text(encoding="utf-8", errors="replace")
            meta, body = parse_note_text(raw)
            notion_stem = re.sub(r"\s+[0-9a-f]{32}$", "", source_note.stem, flags=re.I)
            title = str(meta.get("title") or notion_stem).strip()[:_MAX_TITLE_LEN] or "Untitled"
            directory = _note_dir(root, folder)
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"{_slugify(title)}{NOTE_EXTENSION}"
            if target.exists():
                if strategy == "skip":
                    skipped += 1
                    continue
                if strategy == "rename":
                    target = _unique_note_path(root, folder, title)
                else:
                    old_info = _read_note_file(root, target)
                    if old_info is not None:
                        _snapshot_note(root, old_info["id"], target)
                        known_ids.discard(old_info["id"])
                    overwritten += 1
            now = _now_iso()
            imported_id = str(meta.get("id") or "").strip()
            if not re.fullmatch(r"[0-9A-Za-z_-]{6,128}", imported_id) or imported_id in known_ids:
                imported_id = uuid.uuid4().hex[:12]
            known_ids.add(imported_id)
            imported_meta = dict(meta)
            imported_meta.update(
                {
                    "id": imported_id,
                    "title": title,
                    "tags": _clean_tags(meta.get("tags")),
                    "aliases": _clean_aliases(meta.get("aliases")),
                    "created": str(meta.get("created") or now),
                    "updated": now,
                    "pinned": bool(meta.get("pinned")),
                    "archived": bool(meta.get("archived")),
                }
            )
            _write_note_file(target, imported_meta, body)
            imported += 1
        _scan_cache.clear()
        rebuild_manifest(root)
        stats = rebuild_search_index(root)
        return {
            "ok": True,
            "imported": imported,
            "skipped": skipped,
            "overwritten": overwritten,
            "indexed": stats["total"],
        }
    finally:
        if temporary is not None:
            temporary.cleanup()


# ---------------------------------------------------------------------------
# Folders


def list_folders(root: Path) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for info in scan_notes(root):
        folder = info["folder"]
        while True:
            counts[folder] = counts.get(folder, 0) + 1
            if not folder:
                break
            folder = str(Path(folder).parent.as_posix())
            if folder == ".":
                folder = ""
    dirs: set[str] = set()
    if root.is_dir():
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                entries = list(current.iterdir())
            except OSError:
                continue
            for entry in entries:
                if not entry.is_dir():
                    continue
                name = entry.name
                if name.startswith(".") or name in RESERVED_DIRS:
                    continue
                dirs.add(entry.relative_to(root).as_posix())
                stack.append(entry)

    def children_of(prefix: str) -> list[dict[str, Any]]:
        depth = prefix.count("/") + 1 if prefix else 0
        out = []
        for folder in sorted(dirs):
            if prefix and not folder.startswith(prefix + "/"):
                continue
            if not prefix and "/" in folder:
                continue
            if folder.count("/") != depth:
                continue
            out.append(
                {
                    "name": Path(folder).name,
                    "path": folder,
                    "count": counts.get(folder, 0),
                    "children": children_of(folder),
                }
            )
        return out

    return children_of("")


@_transactional
def create_folder(root: Path, folder: str) -> dict[str, Any]:
    cleaned = clean_folder(folder)
    if not cleaned:
        raise NotesError(400, "missing folder name")
    _note_dir(root, cleaned).mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": cleaned}


@_transactional
def rename_folder(root: Path, folder: str, new_name: str) -> dict[str, Any]:
    cleaned = clean_folder(folder)
    if not cleaned:
        raise NotesError(400, "cannot rename the root")
    new_leaf = clean_folder(new_name)
    if not new_leaf or "/" in new_leaf:
        raise NotesError(400, "invalid folder name")
    source = _note_dir(root, cleaned)
    if not source.is_dir():
        raise NotesError(404, "folder not found")
    parent = str(Path(cleaned).parent.as_posix())
    if parent == ".":
        parent = ""
    target_rel = f"{parent}/{new_leaf}" if parent else new_leaf
    target = _note_dir(root, target_rel)
    if target.exists():
        raise NotesError(409, "a folder with that name already exists")
    source.replace(target)
    _scan_cache.clear()
    rebuild_manifest(root)
    _sync_search_index(root)
    return {"ok": True, "path": target_rel}


@_transactional
def delete_folder(root: Path, folder: str, *, force: bool = False) -> dict[str, Any]:
    cleaned = clean_folder(folder)
    if not cleaned:
        raise NotesError(400, "cannot delete the root")
    target = _note_dir(root, cleaned)
    if not target.is_dir():
        raise NotesError(404, "folder not found")
    remaining = [
        info for info in scan_notes(root)
        if info["folder"] == cleaned or info["folder"].startswith(cleaned + "/")
    ]
    if remaining and not force:
        raise NotesError(
            409, f"folder still contains {len(remaining)} note(s); move them first"
        )
    # force: contained notes go to the trash (recoverable), never straight to
    # permanent deletion.
    for info in remaining:
        delete_note(root, info["id"])
    # Only empty directory trees are removed.
    for current, _directories, files in os.walk(target, topdown=False):
        if files:
            raise NotesError(409, "folder contains files; move them first")
        Path(current).rmdir()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Tags / tasks / backlinks


def list_tags(root: Path) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for info in scan_notes(root):
        for tag in info["tags"]:
            counts[tag] = counts.get(tag, 0) + 1
    return [
        {"tag": tag, "count": count}
        for tag, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def list_tasks(
    root: Path,
    *,
    state: str = "open",
    folder: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    cleaned_folder = clean_folder(folder) if folder else None
    rows: list[dict[str, Any]] = []
    for path in _iter_note_paths(root):
        entry = _read_note_entry(root, path)
        if entry is None:
            continue
        info, _fingerprint, tasks = entry
        if info["archived"]:
            continue
        if cleaned_folder is not None and not (
            info["folder"] == cleaned_folder
            or info["folder"].startswith(cleaned_folder + "/")
        ):
            continue
        if info["tasks_open"] == 0 and info["tasks_done"] == 0:
            continue
        # Tasks were parsed with the note metadata; re-reading the file for
        # every listing turned the Tasks pane into a full-vault read.
        for task in tasks:
            if state == "open" and task["done"]:
                continue
            if state == "done" and not task["done"]:
                continue
            rows.append(
                {
                    **task,
                    "note_id": info["id"],
                    "note_title": info["title"],
                    "note_folder": info["folder"],
                }
            )
    # Due dates first (soonest on top), then priority (p1 before p3).
    rows.sort(key=lambda row: (row["due"] is None, row["due"] or "", row["priority"] or 9))
    page = max(1, min(int(limit or _DEFAULT_PAGE), _MAX_PAGE))
    try:
        offset = max(0, int(cursor or "0"))
    except ValueError:
        offset = 0
    window = rows[offset : offset + page]
    next_cursor = str(offset + page) if offset + page < len(rows) else None
    return {"tasks": window, "next_cursor": next_cursor, "total": len(rows)}


@_transactional
def toggle_task(root: Path, note_id: str, line: int, done: bool) -> dict[str, Any]:
    path, info = _find_note(root, note_id)
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_note_text(text)
    lines = body.splitlines()
    index = line - 1
    if index < 0 or index >= len(lines):
        raise NotesError(400, "task line out of range")
    match = _TASK_RE.match(lines[index])
    if not match:
        raise NotesError(409, "line is no longer a task")
    marker = "x" if done else " "
    lines[index] = f"{match.group(1)}- [{marker}] {match.group(3)}"
    meta["updated"] = _now_iso()
    new_body = "\n".join(lines) + ("\n" if body.endswith("\n") else "")
    _write_note_file(path, meta, new_body)
    updated = _read_note_file(root, path)
    assert updated is not None
    _index_note(root, path, updated, new_body)
    # The new `updated` token lets an editor holding this note keep saving
    # without a phantom 409.
    return {"ok": True, "line": line, "done": done, "updated": updated["updated"]}


@_transactional
def add_task(
    root: Path,
    *,
    text: str,
    note_id: str | None = None,
    inbox_title: str = "Tasks",
) -> dict[str, Any]:
    """Append a ``- [ ] text`` item to a note.

    Without a ``note_id`` the task lands in a dedicated inbox note (created on
    first use, looked up by title case-insensitively), so the UI can offer
    one-click task capture without asking where to put it.
    """
    cleaned = " ".join((text or "").split())[:500]
    if not cleaned:
        raise NotesError(400, "empty task text")
    if note_id:
        path, info = _find_note(root, note_id)
    else:
        cleaned_title = (inbox_title or "Tasks").strip()[:_MAX_TITLE_LEN] or "Tasks"
        found: tuple[Path, dict[str, Any]] | None = None
        for note_path in _iter_note_paths(root):
            candidate = _read_note_file(root, note_path)
            if candidate is None or candidate["archived"]:
                continue
            if candidate["title"].strip().lower() == cleaned_title.lower():
                found = (note_path, candidate)
                break
        if found is None:
            created = create_note(root, title=cleaned_title)
            found = _find_note(root, created["note"]["id"])
        path, info = found
    raw = path.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_note_text(raw)
    trimmed = body.rstrip("\n")
    new_body = (trimmed + "\n" if trimmed else "") + f"- [ ] {cleaned}\n"
    meta["updated"] = _now_iso()
    _write_note_file(path, meta, new_body)
    updated = _read_note_file(root, path)
    assert updated is not None
    _index_note(root, path, updated, new_body)
    return {
        "ok": True,
        "note_id": info["id"],
        "note_title": info["title"],
        "line": len(new_body.rstrip("\n").splitlines()),
        "text": cleaned,
        "updated": updated["updated"],
    }


def note_backlinks(root: Path, note_id: str) -> dict[str, Any]:
    path, info = _find_note(root, note_id)
    names = {info["title"].strip().lower()}
    names.update(alias.strip().lower() for alias in info["aliases"])
    linked: list[dict[str, Any]] = []
    linked_ids: set[str] = set()
    others: dict[str, dict[str, Any]] = {}
    for other_path in _iter_note_paths(root):
        if other_path == path:
            continue
        other = _read_note_file(root, other_path)
        if other is None:
            continue
        others[other["id"]] = other
        if any(link.strip().lower() in names for link in other["links"]):
            linked.append(
                {"id": other["id"], "title": other["title"], "folder": other["folder"]}
            )
            linked_ids.add(other["id"])

    # Unlinked mention: the exact title appears as plain text. Skip short
    # titles: two-letter words would match everywhere and drown the list.
    # Candidates come from the full-text index (every word of the title), so
    # only those few bodies are checked for the exact phrase instead of
    # grepping the whole vault from disk.
    unlinked: list[dict[str, Any]] = []
    title = info["title"].strip()
    if len(title) >= 4:
        from navin.notes.search_index import NotesSearchIndex

        index = NotesSearchIndex(root)
        index.sync(_search_documents(root), body_loader=_load_document_body)
        pattern = re.compile(re.escape(title), re.IGNORECASE)
        for candidate in index.mention_candidates(title):
            other = others.get(str(candidate.get("id") or ""))
            if other is None or other["id"] in linked_ids:
                continue
            if pattern.search(str(candidate.get("body") or "")):
                unlinked.append(
                    {"id": other["id"], "title": other["title"], "folder": other["folder"]}
                )
        unlinked.sort(key=lambda row: row["title"].lower())
    return {"backlinks": linked, "unlinked_mentions": unlinked}


def notes_graph(root: Path) -> dict[str, Any]:
    """Knowledge graph over the notes: wikilink and tag relations.

    Nodes are non-archived notes plus one node per tag; edges resolve
    ``[[wikilinks]]`` case-insensitively against titles and aliases. Dangling
    links (no matching note) are dropped rather than shown as ghost nodes.
    """
    notes = [info for info in scan_notes(root) if not info["archived"]]
    by_name: dict[str, str] = {}
    for info in notes:
        by_name.setdefault(info["title"].strip().lower(), info["id"])
        for alias in info["aliases"]:
            by_name.setdefault(alias.strip().lower(), info["id"])

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()

    def add_edge(source: str, target: str, kind: str) -> None:
        key = (source, target, kind)
        if key in seen_edges or source == target:
            return
        seen_edges.add(key)
        edges.append({"source": source, "target": target, "kind": kind})

    tag_counts: dict[str, int] = {}
    for info in notes:
        nodes.append(
            {
                "id": info["id"],
                "kind": "note",
                "title": info["title"],
                "folder": info["folder"],
                "tags": info["tags"],
                "pinned": info["pinned"],
            }
        )
        for link in info["links"]:
            target = by_name.get(link.strip().lower())
            if target:
                add_edge(info["id"], target, "link")
        for tag in info["tags"]:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
            add_edge(info["id"], f"tag:{tag}", "tag")

    for tag, count in sorted(tag_counts.items()):
        nodes.append(
            {
                "id": f"tag:{tag}",
                "kind": "tag",
                "title": f"#{tag}",
                "folder": "",
                "tags": [],
                "pinned": False,
                "count": count,
            }
        )

    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Attachments


@_transactional
def save_attachment(root: Path, name: str, data_base64: str) -> dict[str, Any]:
    cleaned_name = re.sub(r"[^\w.\- ]", "_", (name or "file").strip())[:120] or "file"
    try:
        data = base64.b64decode(data_base64 or "", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise NotesError(400, "invalid attachment encoding") from exc
    if not data:
        raise NotesError(400, "empty attachment")
    if len(data) > _MAX_ATTACHMENT_BYTES:
        raise NotesError(413, "attachment too large")
    digest = hashlib.sha256(data).hexdigest()[:16]
    files_dir = root / FILES_DIR
    files_dir.mkdir(parents=True, exist_ok=True)
    target = files_dir / f"{digest}-{cleaned_name}"
    if not target.exists():
        atomic_write_bytes(target, data)
    rel = target.relative_to(root).as_posix()
    return {"path": rel, "name": cleaned_name, "size": len(data)}


def list_attachments(
    root: Path, *, limit: int | None = None, cursor: str | None = None
) -> dict[str, Any]:
    files_dir = root / FILES_DIR
    rows: list[dict[str, Any]] = []
    if files_dir.is_dir():
        references: dict[str, list[str]] = {}
        for info in scan_notes(root):
            note_path = root / info["path"]
            try:
                text = note_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in re.finditer(r"_files/([\w.\- ]+)", text):
                references.setdefault(match.group(1), []).append(info["id"])
        for path in sorted(files_dir.iterdir()):
            if not path.is_file():
                continue
            stat = path.stat()
            rows.append(
                {
                    "path": f"{FILES_DIR}/{path.name}",
                    "name": path.name.split("-", 1)[-1],
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                    "referenced_by": references.get(path.name, []),
                }
            )
    rows.sort(key=lambda row: row["modified"], reverse=True)
    page = max(1, min(int(limit or _DEFAULT_PAGE), _MAX_PAGE))
    try:
        offset = max(0, int(cursor or "0"))
    except ValueError:
        offset = 0
    window = rows[offset : offset + page]
    next_cursor = str(offset + page) if offset + page < len(rows) else None
    return {"files": window, "next_cursor": next_cursor, "total": len(rows)}


def read_attachment(root: Path, rel_path: str) -> tuple[bytes, str]:
    cleaned = (rel_path or "").strip().replace("\\", "/")
    if not cleaned.startswith(f"{FILES_DIR}/") or "/.." in cleaned or cleaned.count("/") != 1:
        raise NotesError(400, "invalid attachment path")
    path = root / cleaned
    files_root = (root / FILES_DIR).resolve(strict=False)
    resolved = path.resolve(strict=False)
    if resolved.parent != files_root or path.is_symlink() or not path.is_file():
        raise NotesError(404, "attachment not found")
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return path.read_bytes(), content_type


# ---------------------------------------------------------------------------
# Search


def search_notes(root: Path, query: str, *, limit: int = 30) -> dict[str, Any]:
    needle = (query or "").strip().lower()
    if not needle:
        return {"results": []}
    from navin.notes.search_index import NotesSearchIndex

    # Bring the index up to date from stat() fingerprints (only changed notes
    # are re-read), then answer from the index rows: the matched bodies come
    # back with the hits, so no note file is opened for a search.
    index = NotesSearchIndex(root)
    index.sync(_search_documents(root), body_loader=_load_document_body)
    results: list[dict[str, Any]] = []
    rows = index.search(query, max(1, min(limit, _MAX_PAGE)))
    for row in rows:
        matches: list[dict[str, Any]] = []
        for line_no, line in enumerate(str(row.get("body") or "").splitlines(), start=1):
            position = line.lower().find(needle)
            if position < 0:
                continue
            matches.append(
                {"line": line_no, "text": line.strip()[:240], "col": position}
            )
            if len(matches) >= 3:
                break
        info_path = _safe_manifest_path(root, str(row.get("path") or ""))
        info = _read_note_file(root, info_path) if info_path else None
        if info is None:
            continue
        title_hit = needle in info["title"].lower()
        snippets = [match["text"] for match in matches]
        if not snippets:
            snippets = [info["snippet"] or info["title"]]
        results.append(
            {
                "id": info["id"],
                "title": info["title"],
                "folder": info["folder"],
                "title_match": title_hit,
                "matches": matches,
                "snippet": snippets[0][:240],
            }
        )
    results.sort(key=lambda row: (not row["title_match"],))
    return {"results": results}


# ---------------------------------------------------------------------------
# Database views (Notion-style Table / Board over notes)
#
# A view is a saved filter + presentation over the ordinary notes tree: it
# never owns the notes. Configs live in a single JSON file at the notes root
# so they survive syncs and remain hand-editable.


def _views_path(root: Path) -> Path:
    return root / VIEWS_FILE


def _clean_view(view: Any) -> dict[str, Any]:
    if not isinstance(view, dict):
        raise NotesError(400, "invalid view payload")
    kind = str(view.get("kind") or "table").strip().lower()
    if kind not in {"table", "board"}:
        raise NotesError(400, f"unknown view kind: {kind!r}")
    name = str(view.get("name") or "").strip()[:120]
    if not name:
        raise NotesError(400, "missing view name")
    sort = str(view.get("sort") or "updated_desc")
    if sort not in _SORTS:
        sort = "updated_desc"
    columns = view.get("columns")
    if not isinstance(columns, (list, tuple)):
        columns = []
    cleaned_columns = []
    for column in columns:
        key = _clean_prop_key(column)
        if key and key not in cleaned_columns:
            cleaned_columns.append(key)
        if len(cleaned_columns) >= 12:
            break
    groups = view.get("groups")
    if not isinstance(groups, (list, tuple)):
        groups = []
    cleaned_groups = [str(g).strip()[:_MAX_PROP_STR_LEN] for g in groups if str(g).strip()]
    view_id = str(view.get("id") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{12}", view_id or ""):
        view_id = uuid.uuid4().hex[:12]
    return {
        "id": view_id,
        "name": name,
        "kind": kind,
        "folder": clean_folder(str(view.get("folder") or "")),
        "tag": str(view.get("tag") or "").strip().lstrip("#").lower()[:64],
        "group_by": _clean_prop_key(view.get("group_by") or "") or "status",
        "columns": cleaned_columns,
        "groups": cleaned_groups[:30],
        "sort": sort,
    }


def list_views(root: Path) -> list[dict[str, Any]]:
    path = _views_path(root)
    if not path.is_file():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(loaded, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in loaded:
        try:
            out.append(_clean_view(entry))
        except NotesError:
            continue
    return out


def _write_views(root: Path, views: list[dict[str, Any]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = _views_path(root)
    atomic_write_text(
        path,
        json.dumps(views, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


@_transactional
def save_view(root: Path, view: Any) -> dict[str, Any]:
    cleaned = _clean_view(view)
    views = list_views(root)
    for index, existing in enumerate(views):
        if existing["id"] == cleaned["id"]:
            views[index] = cleaned
            break
    else:
        if len(views) >= _MAX_VIEWS:
            raise NotesError(400, "too many views")
        views.append(cleaned)
    _write_views(root, views)
    return {"view": cleaned}


@_transactional
def delete_view(root: Path, view_id: str) -> dict[str, Any]:
    cleaned_id = (view_id or "").strip()
    views = list_views(root)
    remaining = [view for view in views if view["id"] != cleaned_id]
    if len(remaining) == len(views):
        raise NotesError(404, "view not found")
    _write_views(root, remaining)
    return {"ok": True}
