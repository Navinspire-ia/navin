"""Disk-backed source of truth for the Meetings product.

The store deliberately uses plain JSON, JSONL and media files. Records remain
recoverable with standard tools even when the application cannot start.
"""

from __future__ import annotations

import base64
import io
import json
import re
import shutil
import threading
import time
import unicodedata
import uuid
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from navin.config.paths import get_data_dir
from navin.utils.atomic_io import InterProcessLock, atomic_write_bytes, atomic_write_text

STORE_VERSION = 1
MIGRATION_VERSION = 1
_SECTIONS = ("meta", "transcript", "notes", "summary", "chat")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class MeetingStoreError(ValueError):
    """Invalid or missing meeting data."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _safe_id(value: str, *, label: str = "meeting id") -> str:
    cleaned = str(value or "").strip()
    if not _ID_RE.fullmatch(cleaned):
        raise MeetingStoreError(f"invalid {label}")
    return cleaned


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


class _StoreLock:
    """Re-entrant, thread-safe wrapper over the store's inter-process lock.

    Store calls run on worker threads (``asyncio.to_thread`` in the WebUI) so
    two requests may overlap inside one process. A bare ``InterProcessLock``
    raises on a second ``acquire`` and ``flock`` does not exclude threads of the
    same process anyway, so a thread lock serialises callers first and only the
    outermost holder takes the file lock.
    """

    def __init__(self, path: Path, *, timeout: float) -> None:
        self._path = path
        self._timeout = timeout
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._file_lock: InterProcessLock | None = None

    def __enter__(self) -> "_StoreLock":
        self._thread_lock.acquire()
        try:
            if self._depth == 0:
                lock = InterProcessLock(self._path, timeout=self._timeout)
                lock.acquire()
                self._file_lock = lock
            self._depth += 1
        except BaseException:
            self._thread_lock.release()
            raise
        return self

    def __exit__(self, *exc_info: object) -> None:
        try:
            self._depth -= 1
            if self._depth == 0 and self._file_lock is not None:
                lock, self._file_lock = self._file_lock, None
                lock.release()
        finally:
            self._thread_lock.release()


class MeetingStore:
    """Transactional Meetings storage rooted at one portable directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.records_dir = self.root / "records"
        self.templates_dir = self.root / "templates"
        self.audio_dir = self.root / "audio"
        self.bot_dir = self.root / "bots"
        self.calendar_path = self.root / "calendar.json"
        self.audit_path = self.root / "audit.jsonl"
        self.index_path = self.root / "index.json"
        self.migrations_path = self.root / "migrations.json"
        self.lock = _StoreLock(self.root / ".lock", timeout=10)
        self._ensure()

    def _ensure(self) -> None:
        for path in (self.records_dir, self.templates_dir, self.audio_dir, self.bot_dir):
            path.mkdir(parents=True, exist_ok=True)
        with self.lock:
            if not self.index_path.exists():
                atomic_write_text(
                    self.index_path,
                    _json_text({"version": STORE_VERSION, "meetings": {}}),
                    mode=0o600,
                )
            if not self.calendar_path.exists():
                atomic_write_text(
                    self.calendar_path,
                    _json_text({"version": STORE_VERSION, "events": []}),
                    mode=0o600,
                )
            if not self.migrations_path.exists():
                atomic_write_text(
                    self.migrations_path,
                    _json_text({"version": STORE_VERSION, "imports": {}}),
                    mode=0o600,
                )

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except (OSError, ValueError) as exc:
            raise MeetingStoreError(f"invalid meeting store file: {path.name}") from exc

    def _record_dir(self, meeting_id: str) -> Path:
        return self.records_dir / _safe_id(meeting_id)

    def _read_index(self) -> dict[str, Any]:
        value = self._read_json(self.index_path, {"version": STORE_VERSION, "meetings": {}})
        if not isinstance(value, dict) or not isinstance(value.get("meetings"), dict):
            raise MeetingStoreError("invalid meeting index")
        return value

    def _write_index(self, index: dict[str, Any]) -> None:
        atomic_write_text(self.index_path, _json_text(index), mode=0o600)

    def create(
        self,
        payload: Mapping[str, Any] | None = None,
        *,
        meeting_id: str | None = None,
        audit_actor: str = "local",
    ) -> dict[str, Any]:
        body = dict(payload or {})
        identifier = _safe_id(meeting_id or str(body.get("id") or uuid.uuid4()))
        stamp = _now()
        meta_in = body.get("meta") if isinstance(body.get("meta"), Mapping) else body
        meta = {
            **dict(meta_in),
            "id": identifier,
            "created_at": str(meta_in.get("created_at") or stamp),
            "updated_at": stamp,
            "schema_version": STORE_VERSION,
        }
        with self.lock:
            index = self._read_index()
            if identifier in index["meetings"]:
                raise MeetingStoreError("meeting already exists")
            record_dir = self._record_dir(identifier)
            record_dir.mkdir(parents=True, exist_ok=False)
            for section in _SECTIONS:
                value = meta if section == "meta" else body.get(section, "" if section != "chat" else [])
                atomic_write_text(record_dir / f"{section}.json", _json_text(value), mode=0o600)
            index["meetings"][identifier] = self._index_entry(meta)
            self._write_index(index)
            self._append_audit_unlocked(identifier, "meeting.created", audit_actor, {})
        return self.get(identifier)

    @staticmethod
    def _index_entry(meta: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: meta.get(key)
            for key in (
                "id",
                "title",
                "date",
                "started_at",
                "duration_sec",
                "created_at",
                "updated_at",
                "status",
                "note_id",
            )
            if meta.get(key) is not None
        }

    def get(self, meeting_id: str) -> dict[str, Any]:
        identifier = _safe_id(meeting_id)
        record_dir = self._record_dir(identifier)
        if not record_dir.is_dir():
            raise MeetingStoreError("meeting not found")
        result: dict[str, Any] = {"id": identifier}
        for section in _SECTIONS:
            default: Any = [] if section == "chat" else ({} if section == "meta" else "")
            result[section] = self._read_json(record_dir / f"{section}.json", default)
        return result

    def update(
        self,
        meeting_id: str,
        changes: Mapping[str, Any],
        *,
        audit_actor: str = "local",
    ) -> dict[str, Any]:
        identifier = _safe_id(meeting_id)
        with self.lock:
            current = self.get(identifier)
            record_dir = self._record_dir(identifier)
            changed: list[str] = []
            for section in _SECTIONS:
                if section not in changes:
                    continue
                value = changes[section]
                if section == "meta":
                    if not isinstance(value, Mapping):
                        raise MeetingStoreError("meta must be an object")
                    value = {**current["meta"], **dict(value), "id": identifier}
                atomic_write_text(record_dir / f"{section}.json", _json_text(value), mode=0o600)
                current[section] = value
                changed.append(section)
            current["meta"]["updated_at"] = _now()
            atomic_write_text(record_dir / "meta.json", _json_text(current["meta"]), mode=0o600)
            index = self._read_index()
            index["meetings"][identifier] = self._index_entry(current["meta"])
            self._write_index(index)
            self._append_audit_unlocked(
                identifier, "meeting.updated", audit_actor, {"sections": changed}
            )
        return self.get(identifier)

    def delete(self, meeting_id: str, *, audit_actor: str = "local") -> None:
        identifier = _safe_id(meeting_id)
        with self.lock:
            if not self._record_dir(identifier).exists():
                raise MeetingStoreError("meeting not found")
            shutil.rmtree(self._record_dir(identifier))
            shutil.rmtree(self.audio_dir / identifier, ignore_errors=True)
            index = self._read_index()
            index["meetings"].pop(identifier, None)
            self._write_index(index)
            self._append_audit_unlocked(identifier, "meeting.deleted", audit_actor, {})

    def list(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        entries = list(self._read_index()["meetings"].values())
        entries.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        return entries[max(0, offset) : max(0, offset) + max(1, min(limit, 500))]

    def count(self) -> int:
        return len(self._read_index()["meetings"])

    def search(self, query: str, *, limit: int = 50) -> list[dict[str, Any]]:
        terms = [term for term in re.split(r"\s+", _fold(query).strip()) if term]
        if not terms:
            return self.list(limit=limit)
        matches: list[tuple[int, dict[str, Any]]] = []
        for row in self.list(limit=100_000):
            record = self.get(str(row["id"]))
            haystack = _fold(
                "\n".join(
                    json.dumps(record[name], ensure_ascii=False)
                    if not isinstance(record[name], str)
                    else record[name]
                    for name in _SECTIONS
                )
            )
            if all(term in haystack for term in terms):
                score = sum(haystack.count(term) for term in terms)
                matches.append((score, row))
        matches.sort(key=lambda item: (-item[0], str(item[1].get("updated_at") or "")))
        return [{**row, "score": score} for score, row in matches[: max(1, min(limit, 200))]]

    def append_audit(
        self, meeting_id: str, action: str, *, actor: str = "local", details: Any = None
    ) -> dict[str, Any]:
        identifier = _safe_id(meeting_id)
        with self.lock:
            return self._append_audit_unlocked(identifier, action, actor, details or {})

    def _append_audit_unlocked(
        self, meeting_id: str, action: str, actor: str, details: Any
    ) -> dict[str, Any]:
        event = {
            "id": str(uuid.uuid4()),
            "at": _now(),
            "meeting_id": meeting_id,
            "action": str(action),
            "actor": str(actor),
            "details": details,
        }
        line = json.dumps(event, ensure_ascii=False, sort_keys=True).encode() + b"\n"
        # Append-only: the previous read-everything-then-rewrite made every
        # save O(size of the whole audit log), so a busy desk slowed down with
        # every recording it ever made. JSONL is append-safe by design and the
        # caller already holds the store lock.
        created = not self.audit_path.exists()
        with open(self.audit_path, "ab") as handle:
            handle.write(line)
            handle.flush()
        if created:
            try:
                self.audit_path.chmod(0o600)
            except OSError:
                pass
        return event

    def read_audit(
        self, *, meeting_id: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        identifier = _safe_id(meeting_id) if meeting_id else None
        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        events = [json.loads(line) for line in lines if line.strip()]
        if identifier:
            events = [event for event in events if event.get("meeting_id") == identifier]
        return events[-max(1, min(limit, 5000)) :]

    def put_template(self, template_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        identifier = _safe_id(template_id, label="template id")
        value = {**dict(payload), "id": identifier, "updated_at": _now()}
        with self.lock:
            atomic_write_text(self.templates_dir / f"{identifier}.json", _json_text(value), mode=0o600)
        return value

    def list_templates(self) -> list[dict[str, Any]]:
        return [
            self._read_json(path, {})
            for path in sorted(self.templates_dir.glob("*.json"), key=lambda item: item.name)
        ]

    def delete_template(self, template_id: str) -> None:
        identifier = _safe_id(template_id, label="template id")
        with self.lock:
            try:
                (self.templates_dir / f"{identifier}.json").unlink()
            except FileNotFoundError as exc:
                raise MeetingStoreError("template not found") from exc

    def get_calendar(self) -> dict[str, Any]:
        return self._read_json(self.calendar_path, {"version": STORE_VERSION, "events": []})

    def set_calendar(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        value = {**dict(payload), "version": STORE_VERSION, "updated_at": _now()}
        with self.lock:
            atomic_write_text(self.calendar_path, _json_text(value), mode=0o600)
        return value

    def save_audio_segment(
        self,
        meeting_id: str,
        data: bytes,
        *,
        segment_id: str | None = None,
        suffix: str = ".wav",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        identifier = _safe_id(meeting_id)
        segment = _safe_id(segment_id or str(uuid.uuid4()), label="segment id")
        extension = suffix.lower() if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix.lower()) else ".bin"
        directory = self.audio_dir / identifier
        record = {
            **dict(metadata or {}),
            "id": segment,
            "meeting_id": identifier,
            "file": f"{segment}{extension}",
            "bytes": len(data),
            "created_at": _now(),
        }
        with self.lock:
            atomic_write_bytes(directory / record["file"], data, mode=0o600)
            atomic_write_text(directory / f"{segment}.json", _json_text(record), mode=0o600)
        return record

    def list_audio_segments(self, meeting_id: str) -> list[dict[str, Any]]:
        directory = self.audio_dir / _safe_id(meeting_id)
        return [
            self._read_json(path, {})
            for path in sorted(directory.glob("*.json"), key=lambda item: item.name)
        ]

    def read_audio_segment(self, meeting_id: str, segment_id: str) -> tuple[dict[str, Any], bytes]:
        identifier = _safe_id(meeting_id)
        segment = _safe_id(segment_id, label="segment id")
        directory = self.audio_dir / identifier
        metadata = self._read_json(directory / f"{segment}.json", None)
        if not isinstance(metadata, dict):
            raise MeetingStoreError("audio segment not found")
        filename = str(metadata.get("file") or "")
        if not filename or Path(filename).name != filename:
            raise MeetingStoreError("invalid audio segment")
        try:
            data = (directory / filename).read_bytes()
        except FileNotFoundError as exc:
            raise MeetingStoreError("audio segment not found") from exc
        return metadata, data

    def save_bot_state(self, bot_id: str, state: Mapping[str, Any]) -> dict[str, Any]:
        identifier = _safe_id(bot_id, label="bot id")
        value = {**dict(state), "bot_id": identifier, "persisted_at": _now()}
        with self.lock:
            atomic_write_text(self.bot_dir / f"{identifier}.json", _json_text(value), mode=0o600)
        return value

    def load_bot_state(self, bot_id: str) -> dict[str, Any] | None:
        path = self.bot_dir / f"{_safe_id(bot_id, label='bot id')}.json"
        return self._read_json(path, None)

    def list_bot_states(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        values = [
            self._read_json(path, {})
            for path in sorted(self.bot_dir.glob("*.json"), key=lambda item: item.name)
        ]
        if active_only:
            values = [
                value
                for value in values
                if value.get("state") in {"starting", "joining", "waiting", "live"}
            ]
        return values

    def migrate_local_storage(
        self,
        payload: Mapping[str, Any] | Iterable[Mapping[str, Any]],
        *,
        migration_id: str,
        version: int = MIGRATION_VERSION,
    ) -> dict[str, Any]:
        migration = _safe_id(migration_id, label="migration id")
        if version != MIGRATION_VERSION:
            raise MeetingStoreError("unsupported migration version")
        rows_raw: Any = payload.get("meetings", []) if isinstance(payload, Mapping) else payload
        if isinstance(rows_raw, Mapping):
            rows = [
                {**(dict(value) if isinstance(value, Mapping) else {}), "id": key}
                for key, value in rows_raw.items()
            ]
        elif isinstance(rows_raw, Iterable) and not isinstance(rows_raw, (str, bytes)):
            rows = [dict(value) for value in rows_raw if isinstance(value, Mapping)]
        else:
            raise MeetingStoreError("invalid migration payload")
        with self.lock:
            migrations = self._read_json(
                self.migrations_path, {"version": STORE_VERSION, "imports": {}}
            )
            previous = migrations["imports"].get(migration)
            if previous:
                return {**previous, "already_applied": True}
        imported: list[str] = []
        skipped: list[str] = []
        for row in rows:
            identifier = str(row.get("id") or uuid.uuid4())
            try:
                self.create(row, meeting_id=identifier, audit_actor="migration")
                imported.append(identifier)
            except MeetingStoreError as exc:
                if str(exc) == "meeting already exists":
                    skipped.append(identifier)
                else:
                    raise
        result = {
            "migration_id": migration,
            "version": version,
            "imported": imported,
            "skipped": skipped,
            "applied_at": _now(),
            "already_applied": False,
        }
        with self.lock:
            migrations = self._read_json(
                self.migrations_path, {"version": STORE_VERSION, "imports": {}}
            )
            migrations["imports"][migration] = result
            atomic_write_text(self.migrations_path, _json_text(migrations), mode=0o600)
        return result

    def emergency_export(self) -> bytes:
        """Return a deterministic ZIP containing all user-recoverable data."""
        output = io.BytesIO()
        excluded = {".lock"}
        with self.lock, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(self.root.rglob("*"), key=lambda item: item.as_posix()):
                if not path.is_file() or path.name in excluded or ".tmp" in path.name:
                    continue
                info = zipfile.ZipInfo(path.relative_to(self.root).as_posix())
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.external_attr = 0o600 << 16
                archive.writestr(info, path.read_bytes())
        return output.getvalue()

    def save_audio_data_url(
        self, meeting_id: str, data_url: str, *, metadata: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            header, encoded = data_url.split(",", 1)
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise MeetingStoreError("invalid audio data URL") from exc
        mime = header.split(";", 1)[0].removeprefix("data:")
        suffix = {
            "audio/wav": ".wav",
            "audio/webm": ".webm",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
        }.get(mime, ".bin")
        return self.save_audio_segment(meeting_id, data, suffix=suffix, metadata=metadata)


_DEFAULT_STORE: MeetingStore | None = None


def default_meeting_store() -> MeetingStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = MeetingStore(get_data_dir() / "meetings")
    return _DEFAULT_STORE
