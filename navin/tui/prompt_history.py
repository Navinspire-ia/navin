# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Prompts sent from the CLI, kept per project for the Up and Down arrows.

The list used to live in ``tui.json`` with the other preferences: the last
200 prompts of every project, rewritten whole by each CLI that saved. With
several CLIs open, the last one to save won with the copy it had loaded at its
start: on 2026-11-06 the file held none of the day's prompts of the project
the user came back to, and the 800 KB it had grown to were rewritten and
synced at each prompt.

Each prompt is now one JSON line appended to ``prompt-history.jsonl``.
Appending never rewrites what another CLI wrote, so nothing is lost however
many CLIs run, and a CLI that is killed leaves every earlier prompt on disk. A
prompt belongs to the folder it was sent from: a project recalls its own. The
file starts a new generation past ``ROTATE_BYTES``; the previous one is kept
as ``.1`` and read when the current one is too short.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from pathlib import Path

from loguru import logger

from navin.utils.atomic_io import InterProcessLock, LockTimeoutError, replace_with_retry

LOG_NAME = "prompt-history.jsonl"
#: Prompts the arrows walk through, per project.
RECALL_LIMIT = 200
#: One stored prompt; a longer paste keeps its beginning.
MAX_PROMPT_CHARS = 120_000
#: Size past which the file starts a new generation.
ROTATE_BYTES = 8 * 1024 * 1024
#: What a read takes from the end of a generation.
READ_TAIL_BYTES = 6 * 1024 * 1024
#: Folder of the prompts moved from the old global list: every project sees them.
LEGACY_FOLDER = ""
_ROTATE_LOCK_TIMEOUT_S = 1.0
_O_BINARY = getattr(os, "O_BINARY", 0)


class PromptHistory:
    """The prompt log at ``path``: append one prompt, read a project's latest."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    @property
    def previous(self) -> Path:
        return self.path.with_name(self.path.name + ".1")

    def append(self, folder: str, session: str, text: str) -> bool:
        """Add one prompt sent from ``folder``; False, with a warning, when it cannot be written."""
        text = (text or "").strip()
        if not text:
            return False
        entry = {"at": round(time.time(), 3), "folder": folder, "session": session, "text": text[:MAX_PROMPT_CHARS]}
        return self._write([entry])

    def import_legacy(self, prompts: Iterable[str]) -> int:
        """Add the prompts of the old global list, oldest first, visible from every project."""
        entries = [
            {"at": 0, "folder": LEGACY_FOLDER, "session": "", "text": text[:MAX_PROMPT_CHARS]}
            for text in (str(prompt).strip() for prompt in prompts)
            if text
        ]
        if not entries:
            return 0
        return len(entries) if self._write(entries) else 0

    def recent(self, folder: str, limit: int = RECALL_LIMIT) -> list[str]:
        """The last ``limit`` prompts of ``folder`` (and the legacy ones), oldest first.

        A prompt repeated right after itself counts once, as the arrows show it.
        """
        self._rotate_if_needed()
        prompts = _collapse(self._read(self.path, folder))
        if len(prompts) < limit:
            prompts = _collapse([*self._read(self.previous, folder), *prompts])
        return prompts[-limit:] if limit > 0 else []

    def _write(self, entries: list[dict]) -> bool:
        data = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries).encode("utf-8")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # One write in append mode: the kernel places it at the end of the
            # file even when another CLI appends at the same moment.
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | _O_BINARY, 0o600)
            try:
                os.write(descriptor, data)
                size = os.fstat(descriptor).st_size
            finally:
                os.close(descriptor)
        except OSError as exc:
            logger.warning("Could not save the prompt in {}: {}", self.path, exc)
            return False
        if size > ROTATE_BYTES:
            self._rotate_if_needed()
        return True

    def _read(self, path: Path, folder: str) -> list[str]:
        try:
            with open(path, "rb") as stream:
                size = stream.seek(0, os.SEEK_END)
                start = max(0, size - READ_TAIL_BYTES)
                stream.seek(start)
                raw = stream.read()
        except FileNotFoundError:
            return []
        except OSError as exc:
            logger.warning("Could not read the prompt history {}: {}", path, exc)
            return []
        if start:
            # The tail begins inside a line: that line is not whole.
            cut = raw.find(b"\n")
            raw = raw[cut + 1:] if cut >= 0 else b""
        prompts: list[str] = []
        unreadable = 0
        for line in raw.split(b"\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                # A line cut by a CLI killed while it wrote, or edited by hand.
                unreadable += 1
                continue
            text = entry.get("text") if isinstance(entry, dict) else None
            owner = entry.get("folder", LEGACY_FOLDER) if isinstance(entry, dict) else None
            if not isinstance(text, str) or not text.strip() or not isinstance(owner, str):
                unreadable += 1
                continue
            if owner in (folder, LEGACY_FOLDER):
                prompts.append(text)
        if unreadable:
            logger.warning("Prompt history {}: {} unreadable line(s) skipped", path, unreadable)
        return prompts

    def _rotate_if_needed(self) -> bool:
        try:
            if self.path.stat().st_size <= ROTATE_BYTES:
                return False
        except FileNotFoundError:
            return False
        except OSError as exc:
            logger.warning("Could not check the size of {}: {}", self.path, exc)
            return False
        try:
            with InterProcessLock(self.path.with_name(self.path.name + ".lock"), timeout=_ROTATE_LOCK_TIMEOUT_S):
                try:
                    if self.path.stat().st_size <= ROTATE_BYTES:
                        return False  # another CLI started the new generation meanwhile
                except FileNotFoundError:
                    return False
                # A CLI still appending to the old file writes into ``.1``: kept.
                replace_with_retry(self.path, self.previous)
                return True
        except LockTimeoutError:
            return False  # another CLI holds the lock to do the same
        except OSError as exc:
            logger.warning("Could not start a new prompt history file after {}: {}", self.path, exc)
            return False


def _collapse(prompts: list[str]) -> list[str]:
    """``prompts`` without a prompt repeated right after itself."""
    kept: list[str] = []
    for text in prompts:
        if not kept or kept[-1] != text:
            kept.append(text)
    return kept
