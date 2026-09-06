"""Pending agent-edit review: Cursor-style accept/reject of agent file changes.

Every agent turn already snapshots the *before* content of each file it
modifies (see :mod:`navin.agent.checkpoints`). This module keeps, per session,
the earliest such baseline for every file still awaiting review. Changes stay
applied on disk (provisional acceptance); the user can then:

- **accept**: drop the baseline, the change becomes permanent;
- **reject**: restore the baseline content (or delete a file the agent
  created), undoing the agent's edit.

Either decision can also be taken one hunk at a time, which needs no extra
storage: the pending change is exactly ``baseline + every hunk``, so accepting a
hunk advances the baseline and rejecting one rewrites the file without it. See
:mod:`navin.agent.hunks`.

State lives in ``<workspace>/.pending-review/<session>.json`` and is safe to
delete by hand. The folder ships its own ``.gitignore`` so it is never
committed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from navin.agent.hunks import (
    HunkConflictError,
    apply_hunks,
    compute_hunks,
    resolve_hunk,
)

_NAME_RE = re.compile(r"[^a-z0-9-]+")
_MAX_TRACKED_FILES = 500
_MAX_BASELINE_BYTES = 1024 * 1024  # matches checkpoint per-file snapshot cap


def _slug(value: str) -> str:
    slug = _NAME_RE.sub("-", value.strip().lower()).strip("-")
    return slug[:80] or "session"


def _read_current(path: str) -> bytes | None:
    target = Path(path)
    try:
        if not target.is_file():
            return None
        return target.read_bytes()
    except OSError:
        return None


def _decode_baseline(entry: dict[str, Any]) -> bytes | None:
    """Baseline bytes, or None when the file did not exist before the edit."""
    if entry.get("absent"):
        return None
    raw = entry.get("b64")
    if not isinstance(raw, str):
        return None
    try:
        return base64.b64decode(raw)
    except (ValueError, TypeError):
        return None


def _text_or_none(raw: bytes | None) -> str | None:
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


class PendingReviewStore:
    """File-backed store of agent edits awaiting user review, per session."""

    def __init__(self, workspace: str | Path) -> None:
        self.dir = Path(workspace) / ".pending-review"
        # path -> (signature, added, deleted). The review list is polled every
        # few seconds and diffing every pending file each time is wasteful, so
        # counts are recomputed only when the file or its baseline moved.
        self._counts: dict[str, tuple[tuple[str, bytes], int, int]] = {}
        # Every mutation is a load-modify-save of the per-session JSON. Live
        # edit flushes run on executor threads while end-of-turn merges and
        # accept/reject run on the event loop, so unsynchronized writers would
        # clobber each other's entries (lost baselines = unrecoverable edits).
        self._write_lock = threading.RLock()

    # -- persistence --------------------------------------------------------

    def _path(self, session_key: str) -> Path:
        return self.dir / f"{_slug(session_key.replace(':', '-'))}.json"

    def _load(self, session_key: str) -> dict[str, Any]:
        path = self._path(session_key)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {"files": {}}
        files = data.get("files")
        if not isinstance(files, dict):
            return {"files": {}}
        return {"files": files}

    def _save(self, session_key: str, data: dict[str, Any]) -> None:
        target = self._path(session_key)
        if not data.get("files"):
            target.unlink(missing_ok=True)
            return
        self._ensure_ignored()
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _ensure_ignored(self) -> None:
        marker = self.dir / ".gitignore"
        if marker.exists():
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                "# Navin pending agent-edit review state - never commit.\n*\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    # -- recording ----------------------------------------------------------

    def merge_turn(self, session_key: str, files: dict[str, bytes | None]) -> None:
        """Fold one turn's before-snapshots into the pending review state.

        The earliest baseline per path wins (state before the *first* agent
        edit since the last accept/reject). Entries whose current content is
        back to the baseline are dropped.
        """
        if not files:
            return
        with self._write_lock:
            data = self._load(session_key)
            tracked: dict[str, Any] = data["files"]
            now = datetime.now().isoformat()
            for raw_path, before in files.items():
                try:
                    path = str(Path(raw_path).expanduser().resolve(strict=False))
                except (OSError, RuntimeError, ValueError):
                    continue
                current = _read_current(path)
                entry = tracked.get(path)
                if isinstance(entry, dict):
                    if current == _decode_baseline(entry):
                        tracked.pop(path, None)
                    else:
                        entry["updated_at"] = now
                    continue
                if current == before:
                    continue
                if before is not None and len(before) > _MAX_BASELINE_BYTES:
                    continue
                if len(tracked) >= _MAX_TRACKED_FILES:
                    continue
                snapshot: dict[str, Any] = {"updated_at": now}
                if before is None:
                    snapshot["absent"] = True
                else:
                    snapshot["b64"] = base64.b64encode(before).decode("ascii")
                tracked[path] = snapshot
            self._save(session_key, data)

    # -- queries ------------------------------------------------------------

    @staticmethod
    def _status(entry: dict[str, Any], current: bytes | None) -> str:
        if entry.get("absent"):
            return "created"
        if current is None:
            return "deleted"
        return "modified"

    def _line_counts(
        self, path: str, entry: dict[str, Any], current: bytes | None
    ) -> tuple[int, int]:
        """Lines added/removed by the pending change, for the review list.

        Binary and deleted files have no line diff and report ``(0, 0)``; they
        stay whole-file decisions.
        """
        signature = (
            str(entry.get("updated_at", "")),
            hashlib.blake2b(current or b"", digest_size=16).digest(),
        )
        cached = self._counts.get(path)
        if cached is not None and cached[0] == signature:
            return cached[1], cached[2]

        current_text = _text_or_none(current)
        baseline_text = "" if entry.get("absent") else _text_or_none(_decode_baseline(entry))
        if current_text is None or baseline_text is None:
            added = deleted = 0
        else:
            hunks = compute_hunks(baseline_text, current_text)
            added = sum(hunk.added for hunk in hunks)
            deleted = sum(hunk.deleted for hunk in hunks)
        self._counts[path] = (signature, added, deleted)
        return added, deleted

    def list_changes(self, session_key: str) -> list[dict[str, Any]]:
        """Pending changes, pruning entries reverted back to their baseline."""
        with self._write_lock:
            data = self._load(session_key)
            tracked: dict[str, Any] = data["files"]
            rows: list[dict[str, Any]] = []
            stale: list[str] = []
            for path, entry in tracked.items():
                if not isinstance(entry, dict):
                    stale.append(path)
                    continue
                current = _read_current(path)
                if current == _decode_baseline(entry):
                    stale.append(path)
                    continue
                added, deleted = self._line_counts(path, entry, current)
                rows.append({
                    "path": path,
                    "status": self._status(entry, current),
                    "updated_at": entry.get("updated_at", ""),
                    "added": added,
                    "deleted": deleted,
                })
            if stale:
                for path in stale:
                    tracked.pop(path, None)
                    self._counts.pop(path, None)
                self._save(session_key, data)
        rows.sort(key=lambda row: row["path"])
        return rows

    def file_payload(self, session_key: str, path: str) -> dict[str, Any] | None:
        """Baseline + current content for one pending file, or None."""
        data = self._load(session_key)
        entry = data["files"].get(path)
        if not isinstance(entry, dict):
            return None
        baseline = _decode_baseline(entry)
        current = _read_current(path)
        baseline_text = _text_or_none(baseline)
        current_text = _text_or_none(current)
        binary = (baseline is not None and baseline_text is None) or (
            current is not None and current_text is None
        )
        hunks: list[dict[str, Any]] = []
        if not binary and current_text is not None:
            # A deleted file has no current text to carve up, and a binary one has
            # no lines; both stay whole-file decisions.
            hunks = [
                hunk.payload()
                for hunk in compute_hunks(baseline_text or "", current_text)
            ]
        return {
            "path": path,
            "status": self._status(entry, current),
            "binary": binary,
            "baseline": None if binary else baseline_text,
            "current": None if binary else current_text,
            "hunks": hunks,
        }

    # -- actions ------------------------------------------------------------

    def accept(self, session_key: str, path: str | None = None) -> int:
        """Keep the changes: drop baselines (all files when path is None)."""
        with self._write_lock:
            data = self._load(session_key)
            tracked: dict[str, Any] = data["files"]
            if path is None:
                count = len(tracked)
                tracked.clear()
            else:
                count = 1 if tracked.pop(path, None) is not None else 0
            self._save(session_key, data)
            return count

    # -- per-hunk actions ---------------------------------------------------

    def _hunk_context(self, session_key: str, path: str) -> tuple[dict, dict, str, str]:
        """Load the state a hunk decision needs, or explain why there is none."""
        data = self._load(session_key)
        entry = data["files"].get(path)
        if not isinstance(entry, dict):
            raise HunkConflictError("this file is no longer awaiting review")
        current = _read_current(path)
        if current is None:
            raise HunkConflictError(
                "this file was deleted, so there are no hunks to choose from; "
                "reject the file to restore it"
            )
        current_text = _text_or_none(current)
        if current_text is None:
            raise HunkConflictError("binary files can only be accepted or rejected whole")
        if entry.get("absent"):
            # A file the agent created diffs against nothing, which is one hunk
            # covering the whole thing until part of it is accepted.
            baseline_text = ""
        else:
            baseline_text = _text_or_none(_decode_baseline(entry))
            if baseline_text is None:
                raise HunkConflictError(
                    "binary files can only be accepted or rejected whole"
                )
        return data, entry, baseline_text, current_text

    def accept_hunk(self, session_key: str, path: str, hunk_id: str) -> dict[str, Any]:
        """Keep one hunk: fold it into the baseline, leave the file on disk alone.

        The remaining hunks stay pending against the advanced baseline, which is
        what makes repeated single-hunk accepts converge on the same result as
        accepting the file.
        """
        with self._write_lock:
            data, entry, baseline_text, current_text = self._hunk_context(
                session_key, path
            )
            hunks = compute_hunks(baseline_text, current_text)
            resolve_hunk(hunks, hunk_id)
            new_baseline = apply_hunks(baseline_text, current_text, {hunk_id})

            if new_baseline == current_text:
                data["files"].pop(path, None)
                self._save(session_key, data)
                return {"path": path, "remaining": 0, "done": True}

            entry.pop("absent", None)
            entry["b64"] = base64.b64encode(
                new_baseline.encode("utf-8")
            ).decode("ascii")
            entry["updated_at"] = datetime.now().isoformat()
            self._save(session_key, data)
            return {
                "path": path,
                "remaining": len(compute_hunks(new_baseline, current_text)),
                "done": False,
            }

    def reject_hunk(self, session_key: str, path: str, hunk_id: str) -> dict[str, Any]:
        """Undo one hunk: rewrite the file with every *other* hunk still applied."""
        with self._write_lock:
            data, entry, baseline_text, current_text = self._hunk_context(
                session_key, path
            )
            hunks = compute_hunks(baseline_text, current_text)
            resolve_hunk(hunks, hunk_id)
            keep = {hunk.id for hunk in hunks if hunk.id != hunk_id}
            new_current = apply_hunks(baseline_text, current_text, keep)

            target = Path(path)
            created = bool(entry.get("absent"))
            try:
                if created and not new_current:
                    # Undoing the last hunk of a file the agent created has to
                    # remove it. Writing an empty file would leave a stray
                    # artifact behind and no longer match the baseline, so
                    # review would never clear.
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(new_current.encode("utf-8"))
            except OSError as exc:
                raise HunkConflictError(f"could not write {path}: {exc}") from exc

            if new_current == baseline_text or (created and not new_current):
                data["files"].pop(path, None)
                self._save(session_key, data)
                return {"path": path, "remaining": 0, "done": True}

            entry["updated_at"] = datetime.now().isoformat()
            self._save(session_key, data)
            return {
                "path": path,
                "remaining": len(compute_hunks(baseline_text, new_current)),
                "done": False,
            }

    def reject(self, session_key: str, path: str | None = None) -> tuple[int, int]:
        """Undo the changes: restore baselines. Returns (restored, deleted)."""
        with self._write_lock:
            data = self._load(session_key)
            tracked: dict[str, Any] = data["files"]
            targets = (
                list(tracked.keys())
                if path is None
                else ([path] if path in tracked else [])
            )
            restored = 0
            deleted = 0
            for target_path in targets:
                entry = tracked.get(target_path)
                if not isinstance(entry, dict):
                    tracked.pop(target_path, None)
                    continue
                target = Path(target_path)
                try:
                    if entry.get("absent"):
                        if target.exists():
                            target.unlink()
                            deleted += 1
                    else:
                        baseline = _decode_baseline(entry)
                        if baseline is None:
                            # An unreadable baseline can never be restored, so
                            # the entry is dropped instead of being kept
                            # forever: left tracked, it would make the review
                            # state unclearable.
                            tracked.pop(target_path, None)
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(baseline)
                        restored += 1
                except OSError:
                    continue
                tracked.pop(target_path, None)
            self._save(session_key, data)
            return restored, deleted
