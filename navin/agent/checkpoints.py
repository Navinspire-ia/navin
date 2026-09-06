"""Session checkpoints: snapshot and rewind the agent's conversation and code state.

Mirrors editor-grade checkpointing:

- Every real user prompt creates an automatic checkpoint of the conversation.
- File-editing tools record the *before* content of each file the first time
  they touch it during a turn, so code changes can be rewound.
- ``/checkpoint restore <name> [all|chat|code]`` rewinds conversation, code,
  or both, back to the selected point.

Checkpoints are plain JSON files stored in ``<workspace>/.navin/checkpoints/`` and
are safe to inspect or delete by hand. They complement, not replace, git: the
folder ships its own ``.gitignore`` so it is never committed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import tempfile
from collections.abc import Callable
from contextvars import ContextVar, Token
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navin.session.manager import Session

_NAME_RE = re.compile(r"[^a-z0-9-]+")
_MAX_CHECKPOINTS_PER_SESSION = 100
_MAX_SNAPSHOT_FILE_BYTES = 1024 * 1024  # per-file cap for before-snapshots
_MAX_SNAPSHOT_FILES = 200  # per-checkpoint cap


class CheckpointError(ValueError):
    pass


def _slug(value: str) -> str:
    slug = _NAME_RE.sub("-", value.strip().lower()).strip("-")
    return slug[:64]


def _session_slug(key: str) -> str:
    return _slug(key.replace(":", "-")) or "session"


class TurnRecorder:
    """Collects before-modification file snapshots during one agent turn.

    File-editing tools call :func:`record_file_before` prior to changing a
    file; only the first snapshot per path in a turn is kept, which is the
    state the turn started from.
    """

    __slots__ = ("files", "skipped")

    def __init__(self) -> None:
        # path -> content bytes, or None when the file did not exist yet
        self.files: dict[str, bytes | None] = {}
        self.skipped: list[str] = []

    def record(self, path: str | Path) -> str | None:
        """Snapshot a path; returns the resolved path on first record, else None."""
        try:
            p = str(Path(path).expanduser().resolve(strict=False))
        except (OSError, RuntimeError, ValueError):
            return None
        if p in self.files or p in self.skipped:
            return None
        if len(self.files) >= _MAX_SNAPSHOT_FILES:
            self.skipped.append(p)
            return None
        target = Path(p)
        if not target.exists():
            self.files[p] = None
            return p
        if not target.is_file():
            self.skipped.append(p)
            return None
        try:
            if target.stat().st_size > _MAX_SNAPSHOT_FILE_BYTES:
                self.skipped.append(p)
                return None
            self.files[p] = target.read_bytes()
            return p
        except OSError:
            self.skipped.append(p)
            return None


_current_recorder: ContextVar[TurnRecorder | None] = ContextVar(
    "navin_checkpoint_recorder",
    default=None,
)

# Called with (path, before_bytes_or_None) shortly after a file is first
# recorded in a turn, so the WebUI review list streams in live instead of
# waiting for the end of the turn.
LiveEditHook = Callable[[str, "bytes | None"], None]
_live_edit_hook: ContextVar[LiveEditHook | None] = ContextVar(
    "navin_live_edit_hook",
    default=None,
)
# The record happens *before* the tool writes; the flush must run after, or
# current == baseline and the store drops the entry. One second comfortably
# covers the write that immediately follows.
_LIVE_FLUSH_DELAY_S = 1.0


def bind_checkpoint_recorder(recorder: TurnRecorder) -> Token[TurnRecorder | None]:
    """Bind a snapshot recorder for the current agent turn (async task)."""
    return _current_recorder.set(recorder)


def reset_checkpoint_recorder(token: Token[TurnRecorder | None]) -> None:
    _current_recorder.reset(token)


def bind_live_edit_hook(hook: LiveEditHook) -> Token[LiveEditHook | None]:
    """Bind a per-turn hook that streams first-touch edits to observers."""
    return _live_edit_hook.set(hook)


def reset_live_edit_hook(token: Token[LiveEditHook | None]) -> None:
    _live_edit_hook.reset(token)


def _schedule_live_flush(hook: LiveEditHook, path: str, before: bytes | None) -> None:
    """Run the hook off the event loop, after the pending write lands."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.call_later(
        _LIVE_FLUSH_DELAY_S,
        lambda: loop.run_in_executor(None, _safe_hook, hook, path, before),
    )


def _safe_hook(hook: LiveEditHook, path: str, before: bytes | None) -> None:
    try:
        hook(path, before)
    except Exception:  # noqa: BLE001 - observers must never break a turn
        pass


def record_file_before(path: str | Path) -> None:
    """Snapshot a file's current content before an agent tool modifies it.

    No-op when no recorder is bound (e.g. WebUI file-save, tests).

    Every agent write path funnels through here, so it doubles as the
    freshness signal for the code index: the write about to happen makes the
    throttled revalidation snapshot stale.
    """
    recorder = _current_recorder.get()
    if recorder is not None:
        first_recorded = recorder.record(path)
        if first_recorded is not None:
            hook = _live_edit_hook.get()
            if hook is not None:
                _schedule_live_flush(
                    hook, first_recorded, recorder.files.get(first_recorded)
                )
    from navin.index.warmer import note_file_written

    note_file_written(path)


def _encode_files(files: dict[str, bytes | None]) -> dict[str, Any]:
    encoded: dict[str, Any] = {}
    for path, content in files.items():
        if content is None:
            encoded[path] = {"absent": True}
        else:
            encoded[path] = {"b64": base64.b64encode(content).decode("ascii")}
    return encoded


class CheckpointStore:
    """File-backed store for session checkpoints (conversation + code)."""

    def __init__(self, workspace: str | Path) -> None:
        root = Path(workspace)
        self.dir = root / ".navin" / "checkpoints"
        # One-time migrations: relocate the legacy folders in place
        # (oldest layout first: checkpoints/ -> .checkpoints/ -> .navin/checkpoints/).
        for legacy in (root / ".checkpoints", root / "checkpoints"):
            if legacy.is_dir() and not self.dir.exists():
                try:
                    self.dir.parent.mkdir(parents=True, exist_ok=True)
                    legacy.rename(self.dir)
                except OSError:
                    pass

    def _ensure_ignored(self) -> None:
        """Make git ignore the whole folder, wherever the workspace lives."""
        marker = self.dir / ".gitignore"
        if marker.exists():
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            marker.write_text("# Navin checkpoints - never commit.\n*\n", encoding="utf-8")
        except OSError:
            pass

    def _session_dir(self, session_key: str) -> Path:
        return self.dir / _session_slug(session_key)

    def _path(self, session_key: str, name: str) -> Path:
        return self._session_dir(session_key) / f"{name}.json"

    def save(
        self,
        session: Session,
        note: str = "",
        *,
        auto: bool = False,
        prompt: str = "",
        files: dict[str, bytes | None] | None = None,
        skipped: list[str] | None = None,
    ) -> dict[str, Any]:
        """Snapshot the session (and optionally file states). Returns metadata."""
        now = datetime.now()
        name = now.strftime("%Y%m%d-%H%M%S")
        if note:
            suffix = _slug(note)
            if suffix:
                name = f"{name}-{suffix[:32]}"
        if self._path(session.key, name).exists():
            name = f"{name}-{now.microsecond:06d}"
        payload = {
            "name": name,
            "session_key": session.key,
            "created_at": now.isoformat(),
            "note": note.strip(),
            "auto": bool(auto),
            "prompt": prompt.strip()[:200],
            "message_count": len(session.messages),
            "last_consolidated": session.last_consolidated,
            "messages": session.messages,
            "files": _encode_files(files or {}),
            # Explicit exclusion manifest: paths the recorder could not
            # snapshot (size cap, file-count cap, unreadable). A restore
            # cannot rewind these, and pretending otherwise is worse than
            # saying so.
            "skipped_files": sorted(set(skipped or [])),
        }
        self._ensure_ignored()
        self._write(self._path(session.key, name), payload)
        self._prune(session.key)
        return {
            k: payload[k]
            for k in ("name", "created_at", "note", "auto", "prompt", "message_count")
        }

    def attach_files(
        self,
        session_key: str,
        name: str,
        files: dict[str, bytes | None],
        *,
        skipped: list[str] | None = None,
    ) -> None:
        """Merge before-snapshots into an existing checkpoint (end of turn)."""
        if not files and not skipped:
            return
        try:
            data = self.load(session_key, name)
        except CheckpointError:
            return
        existing = data.get("files") or {}
        merged = {**_encode_files(files), **existing}
        data["files"] = merged
        if skipped:
            previous = data.get("skipped_files") or []
            data["skipped_files"] = sorted(set(previous) | set(skipped))
        self._write(self._path(session_key, _slug(name)), data)

    @staticmethod
    def _write(target: Path, payload: dict[str, Any]) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def list(self, session_key: str) -> list[dict[str, Any]]:
        """List checkpoints for a session, newest first."""
        folder = self._session_dir(session_key)
        if not folder.is_dir():
            return []
        rows: list[dict[str, Any]] = []
        for path in sorted(folder.glob("*.json"), reverse=True):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                rows.append({
                    "name": data.get("name") or path.stem,
                    "created_at": data.get("created_at", ""),
                    "note": data.get("note", ""),
                    "auto": bool(data.get("auto")),
                    "prompt": data.get("prompt", ""),
                    "message_count": int(data.get("message_count") or 0),
                    "file_count": len(data.get("files") or {}),
                    "skipped_count": len(data.get("skipped_files") or []),
                })
            except (OSError, ValueError):
                continue
        return rows

    def load(self, session_key: str, name: str) -> dict[str, Any]:
        safe = _slug(name)
        if not safe:
            raise CheckpointError("invalid checkpoint name")
        path = self._path(session_key, safe)
        if not path.is_file():
            raise CheckpointError(f"checkpoint not found: {name}")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            raise CheckpointError(f"checkpoint unreadable: {name}") from exc
        if not isinstance(data.get("messages"), list):
            raise CheckpointError(f"checkpoint corrupt: {name}")
        return data

    def restore_chat(self, session: Session, name: str) -> int:
        """Replace the session's messages with the checkpoint contents."""
        data = self.load(session.key, name)
        messages = data["messages"]
        last_consolidated = data.get("last_consolidated", 0)
        if not isinstance(last_consolidated, int) or not 0 <= last_consolidated <= len(messages):
            last_consolidated = 0
        session.messages = messages
        session.last_consolidated = last_consolidated
        session.updated_at = datetime.now()
        return len(messages)

    def restore_files(self, session_key: str, name: str) -> tuple[int, int, list[str]]:
        """Rewind files to their state at the checkpoint.

        Collects, for every file touched at or after the target checkpoint,
        the earliest before-snapshot, then writes those states back. Returns
        ``(restored, deleted, unrestorable)`` where *unrestorable* lists the
        paths whose pre-state was never captured (snapshot caps) - the
        restore is partial for those and the caller must say so.
        """
        target = _slug(name)
        if not target:
            raise CheckpointError("invalid checkpoint name")
        if not self._path(session_key, target).is_file():
            raise CheckpointError(f"checkpoint not found: {name}")

        folder = self._session_dir(session_key)
        # Ascending order: target first, then everything after it. The first
        # snapshot seen per path is the state at the target checkpoint.
        earliest: dict[str, dict[str, Any]] = {}
        unrestorable: set[str] = set()
        for path in sorted(folder.glob("*.json")):
            if path.stem < target:
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            for file_path, snap in (data.get("files") or {}).items():
                if file_path not in earliest and isinstance(snap, dict):
                    earliest[file_path] = snap
            for skipped_path in data.get("skipped_files") or []:
                if isinstance(skipped_path, str):
                    unrestorable.add(skipped_path)

        restored = 0
        deleted = 0
        for file_path, snap in earliest.items():
            target_file = Path(file_path)
            try:
                if snap.get("absent"):
                    if target_file.exists():
                        target_file.unlink()
                        deleted += 1
                    continue
                raw = snap.get("b64")
                if not isinstance(raw, str):
                    continue
                content = base64.b64decode(raw)
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_bytes(content)
                restored += 1
            except (OSError, ValueError):
                continue
        # A path that also has a snapshot was captured by a later turn and
        # did rewind; only report what truly could not be restored.
        return restored, deleted, sorted(unrestorable - set(earliest))

    def delete(self, session_key: str, name: str) -> None:
        safe = _slug(name)
        if not safe:
            raise CheckpointError("invalid checkpoint name")
        path = self._path(session_key, safe)
        if not path.is_file():
            raise CheckpointError(f"checkpoint not found: {name}")
        path.unlink()

    def _prune(self, session_key: str) -> None:
        folder = self._session_dir(session_key)
        files = sorted(folder.glob("*.json"), reverse=True)
        for stale in files[_MAX_CHECKPOINTS_PER_SESSION:]:
            stale.unlink(missing_ok=True)
