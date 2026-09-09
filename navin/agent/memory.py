# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Memory system: pure file I/O store and lightweight Consolidator."""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import weakref
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator

from loguru import logger

from navin.runtime_context import public_history_messages
from navin.session.manager import Session, SessionManager
from navin.utils.gitstore import GitStore
from navin.utils.helpers import (
    ensure_dir,
    estimate_message_tokens,
    estimate_prompt_tokens_chain,
    find_legal_message_start,
    recent_message_start_index,
    strip_think,
    truncate_text,
    truncate_text_to_tokens,
)
from navin.utils.prompt_templates import render_template
from navin.utils.workspace_prompts import (
    WORKSPACE_PROMPT_MAX_CHARS,
    has_workspace_prompt_override,
    load_workspace_prompt_override,
    workspace_prompt_file,
)

if TYPE_CHECKING:
    from navin.utils.llm_runtime import LLMRuntime

# ---------------------------------------------------------------------------
# MemoryStore - pure file I/O layer
# ---------------------------------------------------------------------------

class MemoryStore:
    """Pure file I/O for memory files: MEMORY.md, history.jsonl, SOUL.md, USER.md.

    All files live under ``.navin/`` (see :mod:`navin.workspace_layout`);
    legacy root-level copies are migrated on first construction.
    """

    _DEFAULT_MAX_HISTORY = 1000
    # Durable files whose real working-tree delta grounds Dream commit messages
    # and the cursor-advance gate. Deliberately excludes memory/.dream_cursor so
    # that advancing the cursor itself is never mistaken for a productive edit.
    _DREAM_CONTENT_PATHS = (
        ".navin/SOUL.md",
        ".navin/USER.md",
        ".navin/memory/MEMORY.md",
    )
    # Per-file cap when embedding current contents into the Dream prompt. The
    # durable files are tiny in practice (~5 KB total), but a runaway file must
    # not unbounded the prompt.
    _DREAM_FILE_EMBED_CAP = 8000
    _INTERNAL_HISTORY_SESSION_PREFIXES = ("cron:", "dream:")
    _INTERNAL_HISTORY_SESSION_KEYS = {"heartbeat"}
    _LEGACY_ENTRY_START_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}[^\]]*)\]\s*")
    _LEGACY_TIMESTAMP_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]\s*")
    _LEGACY_RAW_MESSAGE_RE = re.compile(
        r"^\[\d{4}-\d{2}-\d{2}[^\]]*\]\s+[A-Z][A-Z0-9_]*(?:\s+\[tools:\s*[^\]]+\])?:"
    )

    def __init__(self, workspace: Path, max_history_entries: int = _DEFAULT_MAX_HISTORY):
        from navin import workspace_layout

        self.workspace = workspace
        self.max_history_entries = max_history_entries
        # Move legacy root-level brain files (SOUL.md, memory/, ...) into
        # .navin/ so old projects keep their memory at the new location.
        workspace_layout.migrate_layout(workspace)
        self.memory_dir = ensure_dir(workspace_layout.memory_dir(workspace))
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "history.jsonl"
        self.legacy_history_file = self.memory_dir / "HISTORY.md"
        self.soul_file = workspace_layout.soul_file(workspace)
        self.user_file = workspace_layout.user_file(workspace)
        self._cursor_file = self.memory_dir / ".log_navin"
        self._legacy_cursor_file = self.memory_dir / ".cursor"
        self._dream_cursor_file = self.memory_dir / ".dream_cursor"
        self._corruption_logged = False  # rate-limit invalid cursor warning
        self._malformed_entry_logged = False  # rate-limit bad history shape warning
        self._oversize_logged = False  # rate-limit oversized-entry warning
        self._dream_prompt_oversize_logged = False
        self._append_lock = threading.Lock()  # serialize cursor allocation + append
        self._git = GitStore(workspace, tracked_files=[
            ".navin/SOUL.md",
            ".navin/USER.md",
            ".navin/memory/MEMORY.md",
            ".navin/memory/.dream_cursor",
        ])
        if self._git.is_initialized() and self._git.owns_gitignore():
            # Legacy stores ignore .navin/: without this, Dream commits would
            # silently stop after the layout migration. Guarded so a project's
            # own repo / .gitignore is never touched.
            with suppress(OSError):
                self._git.refresh_gitignore()
        self._maybe_migrate_cursor_name()
        self._maybe_migrate_legacy_history()

    @property
    def git(self) -> GitStore:
        return self._git

    # -- generic helpers -----------------------------------------------------

    @staticmethod
    def read_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def _maybe_migrate_legacy_history(self) -> None:
        """One-time upgrade from legacy HISTORY.md to history.jsonl.

        The migration is best-effort and prioritizes preserving as much content
        as possible over perfect parsing.
        """
        if not self.legacy_history_file.exists():
            return
        if self.history_file.exists() and self.history_file.stat().st_size > 0:
            return

        try:
            legacy_text = self.legacy_history_file.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            logger.exception("Failed to read legacy HISTORY.md for migration")
            return

        entries = self._parse_legacy_history(legacy_text)
        try:
            if entries:
                self._write_entries(entries)
                last_cursor = entries[-1]["cursor"]
                self._cursor_file.write_text(str(last_cursor), encoding="utf-8")
                # Default to "already processed" so upgrades do not replay the
                # user's entire historical archive into Dream on first start.
                self._dream_cursor_file.write_text(str(last_cursor), encoding="utf-8")

            backup_path = self._next_legacy_backup_path()
            self.legacy_history_file.replace(backup_path)
            logger.info(
                "Migrated legacy HISTORY.md to history.jsonl ({} entries)",
                len(entries),
            )
        except Exception:
            logger.exception("Failed to migrate legacy HISTORY.md")

    def _parse_legacy_history(self, text: str) -> list[dict[str, Any]]:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return []

        fallback_timestamp = self._legacy_fallback_timestamp()
        entries: list[dict[str, Any]] = []
        chunks = self._split_legacy_history_chunks(normalized)

        for cursor, chunk in enumerate(chunks, start=1):
            timestamp = fallback_timestamp
            content = chunk
            match = self._LEGACY_TIMESTAMP_RE.match(chunk)
            if match:
                timestamp = match.group(1)
                remainder = chunk[match.end():].lstrip()
                if remainder:
                    content = remainder

            entries.append({
                "cursor": cursor,
                "timestamp": timestamp,
                "content": content,
            })
        return entries

    def _split_legacy_history_chunks(self, text: str) -> list[str]:
        lines = text.split("\n")
        chunks: list[str] = []
        current: list[str] = []
        saw_blank_separator = False

        for line in lines:
            if saw_blank_separator and line.strip() and current:
                chunks.append("\n".join(current).strip())
                current = [line]
                saw_blank_separator = False
                continue
            if self._should_start_new_legacy_chunk(line, current):
                chunks.append("\n".join(current).strip())
                current = [line]
                saw_blank_separator = False
                continue
            current.append(line)
            saw_blank_separator = not line.strip()

        if current:
            chunks.append("\n".join(current).strip())
        return [chunk for chunk in chunks if chunk]

    def _should_start_new_legacy_chunk(self, line: str, current: list[str]) -> bool:
        if not current:
            return False
        if not self._LEGACY_ENTRY_START_RE.match(line):
            return False
        if self._is_raw_legacy_chunk(current) and self._LEGACY_RAW_MESSAGE_RE.match(line):
            return False
        return True

    def _is_raw_legacy_chunk(self, lines: list[str]) -> bool:
        first_nonempty = next((line for line in lines if line.strip()), "")
        match = self._LEGACY_TIMESTAMP_RE.match(first_nonempty)
        if not match:
            return False
        return first_nonempty[match.end():].lstrip().startswith("[RAW]")

    def _legacy_fallback_timestamp(self) -> str:
        try:
            return datetime.fromtimestamp(
                self.legacy_history_file.stat().st_mtime,
            ).strftime("%Y-%m-%d %H:%M")
        except OSError:
            return datetime.now().strftime("%Y-%m-%d %H:%M")

    def _next_legacy_backup_path(self) -> Path:
        candidate = self.memory_dir / "HISTORY.md.bak"
        suffix = 2
        while candidate.exists():
            candidate = self.memory_dir / f"HISTORY.md.bak.{suffix}"
            suffix += 1
        return candidate

    # -- MEMORY.md (long-term facts) -----------------------------------------

    def read_memory(self) -> str:
        return self.read_file(self.memory_file)

    def write_memory(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    # -- SOUL.md -------------------------------------------------------------

    def read_soul(self) -> str:
        return self.read_file(self.soul_file)

    def write_soul(self, content: str) -> None:
        self.soul_file.write_text(content, encoding="utf-8")

    # -- USER.md -------------------------------------------------------------

    def read_user(self) -> str:
        return self.read_file(self.user_file)

    def write_user(self, content: str) -> None:
        self.user_file.write_text(content, encoding="utf-8")

    # -- context injection (used by context.py) ------------------------------

    def get_memory_context(self) -> str:
        long_term = self.read_memory()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    # -- history.jsonl - append-only, JSONL format ---------------------------

    def append_history(
        self,
        entry: str,
        *,
        max_chars: int | None = None,
        session_key: str | None = None,
    ) -> int:
        """Append *entry* to history.jsonl and return its auto-incrementing cursor.

        Entries are passed through `strip_think` to drop template-level leaks
        (e.g. unclosed `<think` prefixes, `<channel|>` markers) before being
        persisted. If the cleaned content is empty but the raw entry wasn't,
        the record is persisted with an empty string rather than falling back
        to the raw leak - otherwise `strip_think`'s guarantees would be
        undone by history replay / consolidation downstream.

        A defensive cap (*max_chars*, default ``_HISTORY_ENTRY_HARD_CAP``) is
        applied as a final safety net: individual callers should cap their own
        content more tightly; this default only exists to catch unintentional
        large writes (e.g. an LLM echoing its input back as a "summary").
        """
        limit = max_chars if max_chars is not None else _HISTORY_ENTRY_HARD_CAP
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        raw = entry.rstrip()
        if len(raw) > limit:
            if not self._oversize_logged:
                self._oversize_logged = True
                logger.warning(
                    "history entry exceeds {} chars ({}); truncating. "
                    "Usually means a caller forgot its own cap; "
                    "further occurrences suppressed.",
                    limit, len(raw),
                )
            raw = truncate_text(raw, limit)
        content = strip_think(raw)
        # Cursor allocation and the append must be atomic: concurrent writers
        # could otherwise read the same current cursor and emit duplicates.
        with self._append_lock:
            cursor = self._next_cursor()
            if raw and not content:
                logger.debug(
                    "history entry {} stripped to empty (likely template leak); "
                    "persisting empty content to avoid re-polluting context",
                    cursor,
                )
            record = {"cursor": cursor, "timestamp": ts, "content": content}
            if session_key:
                record["session_key"] = session_key
            with open(self.history_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._cursor_file.write_text(str(cursor), encoding="utf-8")
        return cursor

    @staticmethod
    def _valid_cursor(value: Any) -> int | None:
        """Non-negative int cursors only; reject bool (``isinstance(True, int)`` is True)."""
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    def _iter_valid_entries(self) -> Iterator[tuple[dict[str, Any], int]]:
        """Yield ``(entry, cursor)`` for well-formed entries; warn once on corruption."""
        poisoned: Any = None
        malformed_cursor: int | None = None
        for entry in self._read_entries():
            raw = entry.get("cursor")
            if raw is None:
                continue
            cursor = self._valid_cursor(raw)
            if cursor is None:
                poisoned = raw
                continue
            if not self._valid_history_payload(entry):
                malformed_cursor = cursor
                continue
            yield entry, cursor
        if poisoned is not None and not self._corruption_logged:
            self._corruption_logged = True
            logger.warning(
                "history.jsonl contains an invalid cursor ({!r}); dropping it. "
                "Usually caused by an external writer; further occurrences suppressed.",
                poisoned,
            )
        if malformed_cursor is not None and not self._malformed_entry_logged:
            self._malformed_entry_logged = True
            logger.warning(
                "history.jsonl contains a malformed entry at cursor {}; dropping it. "
                "Usually caused by an external writer; further occurrences suppressed.",
                malformed_cursor,
            )

    @staticmethod
    def _valid_history_payload(entry: dict[str, Any]) -> bool:
        if not isinstance(entry.get("timestamp"), str):
            return False
        if not isinstance(entry.get("content"), str):
            return False
        session_key = entry.get("session_key")
        return session_key is None or isinstance(session_key, str)

    def _maybe_migrate_cursor_name(self) -> None:
        """Move a pre-existing ``memory/.cursor`` to ``memory/.log_navin``.

        The old name was read as Cursor IDE configuration by everyone who saw it
        in a file tree or in ``git status``, and it showed up as a node in the
        project graph next to real source files. It only ever held one integer:
        how far the raw logger has written into history.jsonl.

        Losing the counter is not harmful - ``_next_cursor`` recovers it from the
        last line of history.jsonl - so a failed rename is left alone rather than
        raised.
        """
        if self._cursor_file.exists() or not self._legacy_cursor_file.exists():
            return
        with suppress(OSError):
            self._legacy_cursor_file.replace(self._cursor_file)

    def _read_cursor_counter(self) -> int | None:
        """Return the persisted cursor counter when it is usable."""
        if not self._cursor_file.exists():
            return None
        with suppress(ValueError, OSError):
            cursor = int(self._cursor_file.read_text(encoding="utf-8").strip())
            if cursor >= 0:
                return cursor
        return None

    def _next_cursor(self) -> int:
        """Read the current cursor counter and return the next value."""
        cursor_counter = self._read_cursor_counter()
        last = self._read_last_entry() or {}
        last_cursor = self._valid_cursor(last.get("cursor"))
        if cursor_counter is not None:
            if last_cursor is not None:
                return max(cursor_counter, last_cursor) + 1
            max_history_cursor = max((c for _, c in self._iter_valid_entries()), default=0)
            return max(cursor_counter, max_history_cursor) + 1

        # Fast path: trust the tail when intact.  Otherwise scan the whole
        # file and take ``max`` - that stays correct even if the monotonic
        # invariant was broken by external writes.
        if last_cursor is not None:
            return last_cursor + 1
        return max((c for _, c in self._iter_valid_entries()), default=0) + 1

    def read_unprocessed_history(self, since_cursor: int) -> list[dict[str, Any]]:
        """Return history entries with a valid cursor > *since_cursor*."""
        return [e for e, c in self._iter_valid_entries() if c > since_cursor]

    @classmethod
    def _is_internal_history_session(cls, session_key: str | None) -> bool:
        if not session_key:
            return False
        return (
            session_key in cls._INTERNAL_HISTORY_SESSION_KEYS
            or session_key.startswith(cls._INTERNAL_HISTORY_SESSION_PREFIXES)
        )

    def read_recent_history_for_prompt(
        self,
        since_cursor: int,
        *,
        session_key: str | None,
        unified_session: bool = False,
    ) -> list[dict[str, Any]]:
        """Return unprocessed history entries safe to inject into a turn prompt."""
        entries = self.read_unprocessed_history(since_cursor=since_cursor)
        if session_key is None:
            return entries
        if not unified_session:
            return [e for e in entries if e.get("session_key") == session_key]

        return [
            entry
            for entry in entries
            if (entry_session := entry.get("session_key")) == session_key
            or not self._is_internal_history_session(entry_session)
        ]

    def compact_history(self) -> None:
        """Drop oldest entries if the file exceeds *max_history_entries*."""
        if self.max_history_entries <= 0:
            return
        entries = self._read_entries()
        if len(entries) <= self.max_history_entries:
            return
        kept = entries[-self.max_history_entries:]
        self._write_entries(kept)

    # -- JSONL helpers -------------------------------------------------------

    def _read_entries(self) -> list[dict[str, Any]]:
        """Read all entries from history.jsonl."""
        entries: list[dict[str, Any]] = []
        with suppress(FileNotFoundError):
            with open(self.history_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

        return entries

    def _read_last_entry(self) -> dict[str, Any] | None:
        """Read the last entry from the JSONL file efficiently."""
        try:
            with open(self.history_file, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return None
                read_size = min(size, 4096)
                f.seek(size - read_size)
                data = f.read().decode("utf-8")
                lines = [line for line in data.split("\n") if line.strip()]
                if not lines:
                    return None
                return json.loads(lines[-1])
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _write_entries(self, entries: list[dict[str, Any]]) -> None:
        """Overwrite history.jsonl with the given entries (atomic write)."""
        tmp_path = self.history_file.with_suffix(self.history_file.suffix + ".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.history_file)

            # fsync the directory so the rename is durable.
            # On Windows, opening a directory with O_RDONLY raises
            # PermissionError - skip the dir sync there (NTFS
            # journals metadata synchronously).
            with suppress(PermissionError):
                fd = os.open(str(self.history_file.parent), os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    # -- dream cursor --------------------------------------------------------

    def get_last_dream_cursor(self) -> int:
        if self._dream_cursor_file.exists():
            with suppress(ValueError, OSError):
                return int(self._dream_cursor_file.read_text(encoding="utf-8").strip())
        return 0

    def set_last_dream_cursor(self, cursor: int) -> None:
        self._dream_cursor_file.write_text(str(cursor), encoding="utf-8")

    def get_latest_cursor(self) -> int:
        return max(self._next_cursor() - 1, 0)

    @property
    def dream_prompt_file(self) -> Path:
        return workspace_prompt_file(self.workspace, "dream")

    def has_dream_prompt_override(self) -> bool:
        return has_workspace_prompt_override(self.dream_prompt_file)

    @staticmethod
    def default_dream_prompt() -> str:
        from navin.agent.skills import BUILTIN_SKILLS_DIR

        return render_template(
            "agent/dream.md",
            strip=True,
            skill_creator_path=str(BUILTIN_SKILLS_DIR / "skill-creator" / "SKILL.md"),
        )

    def _dream_template(self) -> str:
        text, original_chars = load_workspace_prompt_override(self.dream_prompt_file)
        if text is not None:
            if (
                original_chars > WORKSPACE_PROMPT_MAX_CHARS
                and not self._dream_prompt_oversize_logged
            ):
                self._dream_prompt_oversize_logged = True
                logger.warning(
                    "workspace Dream prompt exceeds {} chars ({}); truncating. "
                    "Further occurrences suppressed.",
                    WORKSPACE_PROMPT_MAX_CHARS, original_chars,
                )
            return text
        return self.default_dream_prompt()

    def build_dream_prompt(self, *, max_entries: int = 20) -> tuple[str, int] | None:
        """Build the Dream prompt with unprocessed history context.

        Returns ``(prompt, last_cursor)`` or ``None`` if nothing to process.

        The current contents of the durable memory files (SOUL.md, USER.md,
        memory/MEMORY.md) are embedded so the model edits the real files rather
        than a stale mental model - eliminating a class of failed/out-of-bounds
        edits that previously produced hallucinated audit records.
        """
        last_cursor = self.get_last_dream_cursor()
        entries = self.read_unprocessed_history(since_cursor=last_cursor)
        if not entries:
            return None

        batch = entries[:max_entries]
        history_text = "\n".join(
            f"[{e['timestamp']}] {truncate_text(e['content'], 500)}"
            for e in batch
        )
        template = self._dream_template()
        files_section = self._render_current_memory_files()
        prompt = (
            f"{template}\n\n{files_section}\n\n"
            f"## Conversation History\n{history_text}"
        )
        return (prompt, batch[-1]["cursor"])

    def _render_current_memory_files(self) -> str:
        """Render the durable memory files' current contents for the Dream prompt.

        Missing files render as ``(empty)``; oversized files are capped. The
        section is the ground truth the model must edit against.
        """
        files = [
            (".navin/SOUL.md", self.soul_file),
            (".navin/USER.md", self.user_file),
            (".navin/memory/MEMORY.md", self.memory_file),
        ]
        blocks = []
        for label, path in files:
            try:
                content = path.read_text(encoding="utf-8") if path.exists() else ""
            except OSError:
                content = ""
            if len(content) > self._DREAM_FILE_EMBED_CAP:
                content = truncate_text(content, self._DREAM_FILE_EMBED_CAP) + "\n...[truncated]"
            blocks.append(f"### {label}\n{content}" if content.strip() else f"### {label}\n(empty)")
        return "## Current Memory Files\n" + "\n\n".join(blocks)

    def dream_content_diff(self) -> str:
        """Structured summary of uncommitted changes to the durable memory files.

        Returns "" when git is unavailable or no content file changed. This is
        the ground-truth input for diff-grounded Dream commit messages and for
        gating cursor advance on real edits (never on LLM self-report).
        """
        if not self._git.is_initialized():
            return ""
        return self._git.summarize_working_tree(list(self._DREAM_CONTENT_PATHS))

    def build_dream_tools(self):
        """Build the restricted tool registry used by Dream runs."""
        from navin import workspace_layout
        from navin.agent.skills import BUILTIN_SKILLS_DIR
        from navin.agent.tools.apply_patch import ApplyPatchTool
        from navin.agent.tools.file_state import FileStates
        from navin.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
        from navin.agent.tools.registry import ToolRegistry

        tools = ToolRegistry()
        file_states = FileStates()
        workspace = self.workspace
        skills_dir = workspace_layout.coalesce_owned_skills(workspace)

        extra_read = [BUILTIN_SKILLS_DIR] if BUILTIN_SKILLS_DIR.exists() else None
        editable_files = [self.memory_file, self.soul_file, self.user_file]

        tools.register(ReadFileTool(
            workspace=workspace,
            allowed_dir=workspace,
            extra_read_allowed_dirs=extra_read,
            file_states=file_states,
        ))
        tools.register(EditFileTool(
            workspace=workspace,
            allowed_dir=skills_dir,
            extra_write_allowed_files=editable_files,
            file_states=file_states,
        ))
        tools.register(ApplyPatchTool(
            workspace=workspace,
            allowed_dir=skills_dir,
            extra_write_allowed_files=editable_files,
            file_states=file_states,
        ))
        tools.register(WriteFileTool(
            workspace=workspace,
            allowed_dir=skills_dir,
            file_states=file_states,
        ))
        return tools

    @staticmethod
    def dream_run_completed(resp: object | None) -> bool:
        """Return True only when an ephemeral Dream agent turn completed cleanly."""
        metadata = getattr(resp, "metadata", None)
        return isinstance(metadata, dict) and metadata.get("_stop_reason") == "completed"

    # -- message formatting utility ------------------------------------------

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        lines = []
        for message in messages:
            if not message.get("content"):
                continue
            tools = f" [tools: {', '.join(message['tools_used'])}]" if message.get("tools_used") else ""
            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] {message['role'].upper()}{tools}: {message['content']}"
            )
        return "\n".join(lines)

    def raw_archive(
        self,
        messages: list[dict],
        *,
        max_chars: int | None = None,
        session_key: str | None = None,
    ) -> None:
        """Fallback: dump raw messages to history.jsonl without LLM summarization."""
        limit = max_chars if max_chars is not None else _RAW_ARCHIVE_MAX_CHARS
        formatted = truncate_text(
            self._format_messages(public_history_messages(messages)),
            limit,
        )
        self.append_history(
            f"[RAW] {len(messages)} messages\n"
            f"{formatted}",
            session_key=session_key,
        )
        logger.warning(
            "Memory consolidation degraded: raw-archived {} messages", len(messages)
        )

    # ------------------------------------------------------------------
    # Dream helpers
    # ------------------------------------------------------------------

    @staticmethod
    def dream_session_key() -> str:
        """Return a unique session key for a Dream run, e.g. ``dream:20260528-100000``."""
        return f"dream:{datetime.now():%Y%m%d-%H%M%S}"

    @staticmethod
    def build_dream_commit_message(prefix: str, diff_body: str) -> str:
        """Build a Dream commit message grounded in the real working-tree diff.

        *diff_body* is a structured, machine-derived summary of the actual file
        changes (see :meth:`dream_content_diff` /
        :meth:`GitStore.summarize_working_tree`). The LLM narrative is
        deliberately excluded so the audit record (``/dream-log``) reflects the
        filesystem's truth, not the model's self-report.

        An empty *diff_body* yields the bare *prefix*, which ``auto_commit``
        turns into a no-op when there is nothing to stage.
        """
        diff_body = (diff_body or "").strip()
        if not diff_body:
            return prefix
        return f"{prefix}\n\n{diff_body}"

    @staticmethod
    def prune_dream_sessions(sessions_dir: Path, *, keep: int = 10) -> None:
        """Remove the oldest Dream session files, keeping only the N most recent.

        Only current base64url-encoded Dream session keys are considered.
        Non-dream session files are never touched.
        """
        dream_files = []
        for path in sessions_dir.glob("*.jsonl"):
            decoded_key = SessionManager._decode_storage_key(path.stem)
            if decoded_key is not None and decoded_key.startswith("dream:"):
                dream_files.append(path)
        dream_files.sort(key=lambda p: p.stat().st_mtime)
        if len(dream_files) <= keep:
            return

        to_remove = dream_files[: len(dream_files) - keep]
        for path in to_remove:
            try:
                path.unlink()
                logger.debug("Pruned old dream session: {}", path.stem)
            except OSError:
                logger.warning("Failed to prune dream session {}", path)


# ---------------------------------------------------------------------------
# Consolidator - lightweight token-budget triggered consolidation
# ---------------------------------------------------------------------------

# Individual history.jsonl writers cap their own payloads tightly; the
# _HISTORY_ENTRY_HARD_CAP at append_history() is a belt-and-suspenders default
# that catches any new caller that forgot to set its own cap.
_RAW_ARCHIVE_MAX_CHARS = 16_000       # fallback dump (LLM failed)
_ARCHIVE_SUMMARY_MAX_CHARS = 8_000    # LLM-produced consolidation summary
# Wall clock on one consolidation call. These calls run before a turn starts
# (or in the background after it ends) and go straight to chat_with_retry,
# whose ladder can otherwise sit on a hung provider for many minutes; a
# summary that has not arrived in three minutes will not, and the raw archive
# fallback keeps the messages.
_CONSOLIDATION_LLM_TIMEOUT_S = 180.0
_HISTORY_ENTRY_HARD_CAP = 64_000      # emergency cap in append_history


class Consolidator:
    """Lightweight consolidation: summarizes evicted messages into history.jsonl."""

    _MAX_CONSOLIDATION_ROUNDS = 5

    _SAFETY_BUFFER = 1024  # extra headroom for tokenizer estimation drift

    def __init__(
        self,
        store: MemoryStore,
        sessions: SessionManager,
        build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        consolidation_ratio: float = 0.5,
        unified_session: bool = False,
        on_compacted: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.store = store
        self.sessions = sessions
        self.consolidation_ratio = consolidation_ratio
        self.unified_session = unified_session
        self._build_messages = build_messages
        self._get_tool_definitions = get_tool_definitions
        self._on_compacted = on_compacted
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        # Last full token estimate per session: (tokens, history_chars, model).
        # Rebuilding the whole probe prompt and running tiktoken over it costs
        # ~20ms per BUILD even when the session is nowhere near its budget;
        # the cache lets clearly-idle turns skip that entirely (see
        # _clearly_under_budget).
        self._estimate_cache: dict[str, tuple[int, int, str]] = {}

    def _notify_compacted(self, info: dict[str, Any]) -> None:
        """Best-effort notification so UIs can surface compaction to the user."""
        if self._on_compacted is None:
            return
        try:
            self._on_compacted(info)
        except Exception:
            logger.exception("compaction notification failed for {}", info.get("session_key"))

    def get_lock(self, session_key: str) -> asyncio.Lock:
        """Return the shared consolidation lock for one session."""
        return self._locks.setdefault(session_key, asyncio.Lock())

    def pick_consolidation_boundary(
        self,
        session: Session,
        tokens_to_remove: int,
    ) -> tuple[int, int] | None:
        """Pick a user-turn boundary that removes enough old prompt tokens."""
        start = session.last_consolidated
        if start >= len(session.messages) or tokens_to_remove <= 0:
            return None

        removed_tokens = 0
        last_boundary: tuple[int, int] | None = None
        for idx in range(start, len(session.messages)):
            message = session.messages[idx]
            if idx > start and message.get("role") == "user":
                last_boundary = (idx, removed_tokens)
                if removed_tokens >= tokens_to_remove:
                    return last_boundary
            removed_tokens += estimate_message_tokens(message)

        return last_boundary

    # Skip the full estimate only when the projection sits below this share
    # of the budget; the grey zone above it always re-estimates for real.
    _LAZY_ESTIMATE_HEADROOM = 0.45
    # Start consolidating before hard overflow so tool-heavy audits do not
    # replay megabytes of read_file/grep results until the window is full.
    _PROACTIVE_CONSOLIDATION_RATIO = 0.55
    # Chars per token used to project growth since the last real estimate.
    # English prose runs ~4 chars/token; using 3 deliberately overestimates,
    # so a skipped estimate can only err on the side of consolidating late
    # by one turn, never of overflowing the window.
    _CHARS_PER_TOKEN_CONSERVATIVE = 3

    @staticmethod
    def _unconsolidated_chars(session: Session) -> int:
        """Rough char size of the unconsolidated tail (upper bound, no tokenizer)."""
        total = 0
        for message in session.messages[session.last_consolidated:]:
            content = message.get("content")
            if isinstance(content, str):
                total += len(content)
            elif content is not None:
                total += len(str(content))
            tool_calls = message.get("tool_calls")
            if tool_calls:
                total += len(str(tool_calls))
            reasoning = message.get("reasoning_content")
            if isinstance(reasoning, str):
                total += len(reasoning)
        return total

    def _clearly_under_budget(
        self,
        session: Session,
        *,
        runtime: LLMRuntime,
        budget: int,
    ) -> bool:
        """True when the cached estimate proves the session is far from full.

        Projects the last real estimate forward by the chars added since,
        using a conservative chars-per-token ratio. Shrinkage (archived
        messages) is ignored on purpose: it can only make the projection an
        overestimate, so a skip stays safe.
        """
        cached = self._estimate_cache.get(session.key)
        if cached is None:
            return False
        cached_tokens, cached_chars, cached_model = cached
        if cached_model != runtime.model:
            return False
        chars = self._unconsolidated_chars(session)
        grown = max(0, chars - cached_chars)
        projected = cached_tokens + grown // self._CHARS_PER_TOKEN_CONSERVATIVE
        return projected < int(budget * self._LAZY_ESTIMATE_HEADROOM)

    def _remember_estimate(
        self,
        session: Session,
        *,
        runtime: LLMRuntime,
        estimated: int,
    ) -> None:
        if estimated > 0:
            self._estimate_cache[session.key] = (
                estimated,
                self._unconsolidated_chars(session),
                runtime.model,
            )
        else:
            self._estimate_cache.pop(session.key, None)

    @staticmethod
    def _full_unconsolidated_history(
        session: Session,
    ) -> list[dict[str, Any]]:
        """Return the whole unconsolidated tail for consolidation decisions."""
        unconsolidated_count = len(session.messages) - session.last_consolidated
        if unconsolidated_count <= 0:
            return []
        return session.get_history(max_messages=unconsolidated_count)

    @staticmethod
    def _replay_overflow_boundary(
        session: Session,
        replay_max_messages: int | None,
    ) -> int | None:
        if not replay_max_messages or replay_max_messages <= 0:
            return None
        tail = list(enumerate(session.messages[session.last_consolidated:], session.last_consolidated))
        if len(tail) <= replay_max_messages:
            return None

        tail_messages = [message for _idx, message in tail]
        start_idx = recent_message_start_index(
            tail_messages,
            replay_max_messages,
            extend_to_user=True,
        )
        sliced = tail[start_idx:]
        for i, (_idx, message) in enumerate(sliced):
            if message.get("role") == "user":
                start = i
                if i > 0 and sliced[i - 1][1].get("_channel_delivery"):
                    start = i - 1
                sliced = sliced[start:]
                break

        legal_start = find_legal_message_start([message for _idx, message in sliced])
        if legal_start:
            sliced = sliced[legal_start:]
        if not sliced:
            return len(session.messages)

        first_visible_idx = sliced[0][0]
        if first_visible_idx <= session.last_consolidated:
            return None
        return first_visible_idx

    async def _consolidate_replay_overflow(
        self,
        session: Session,
        replay_max_messages: int | None,
        *,
        runtime: LLMRuntime,
    ) -> str | None:
        """Archive messages that would be hidden by the replay message window."""
        end_idx = self._replay_overflow_boundary(session, replay_max_messages)
        if end_idx is None:
            return None
        chunk = session.messages[session.last_consolidated:end_idx]
        if not chunk:
            return None
        logger.info(
            "Replay-window consolidation for {}: chunk={} msgs, replay_max={}",
            session.key,
            len(chunk),
            replay_max_messages,
        )
        summary = await self.archive(
            chunk,
            runtime=runtime,
            session_key=session.key,
        )
        session.last_consolidated = end_idx
        self.sessions.save(session)
        return summary

    def _persist_last_summary(self, session: Session, summary: str | None) -> None:
        if summary and summary != "(nothing)":
            session.metadata["_last_summary"] = {
                "text": summary,
                "last_active": session.updated_at.isoformat(),
            }
            self.sessions.save(session)

    _RESUME_AUTO_START = "<!-- navin:auto-handoff:start -->"
    _RESUME_AUTO_END = "<!-- navin:auto-handoff:end -->"
    _RESUME_AUTO_MAX_CHARS = 4_000

    def _persist_resume_brief(self, session: Session, brief: str | None) -> None:
        """Mirror the handoff brief into the project's RESUME.md.

        The metadata copy of the brief dies with ``/new`` or gets overwritten
        by a later compaction; the continuity file survives both and is
        injected into every turn, so resuming a project no longer depends on
        the agent having remembered to update RESUME.md itself.
        """
        if not brief or brief == "(nothing)":
            return
        scope = session.metadata.get("workspace_scope")
        project = scope.get("project_path") if isinstance(scope, dict) else None
        if not project:
            return
        try:
            resume_path = (
                Path(str(project)).expanduser() / ".navin" / "continuity" / "RESUME.md"
            )
            if not resume_path.parent.is_dir():
                return
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            body = truncate_text(brief.strip(), self._RESUME_AUTO_MAX_CHARS)
            block = (
                f"{self._RESUME_AUTO_START}\n"
                f"## Last auto handoff ({stamp}, session {session.key})\n\n"
                f"{body}\n"
                f"{self._RESUME_AUTO_END}"
            )
            existing = (
                resume_path.read_text(encoding="utf-8")
                if resume_path.is_file()
                else ""
            )
            start = existing.find(self._RESUME_AUTO_START)
            end = existing.find(self._RESUME_AUTO_END)
            if start != -1 and end != -1 and end > start:
                updated = (
                    existing[:start]
                    + block
                    + existing[end + len(self._RESUME_AUTO_END):]
                )
            else:
                prefix = existing.rstrip()
                updated = f"{prefix}\n\n{block}\n" if prefix else f"{block}\n"
            resume_path.write_text(updated, encoding="utf-8")
            try:
                from navin.continuity.resume_seed import append_decisions_from_brief

                append_decisions_from_brief(project, brief)
            except Exception:
                logger.debug(
                    "Could not append Decisions from handoff brief for {}",
                    session.key,
                )
        except Exception:
            logger.debug(
                "Could not mirror handoff brief to RESUME.md for {}", session.key
            )

    def estimate_session_prompt_tokens(
        self,
        session: Session,
        *,
        runtime: LLMRuntime,
    ) -> tuple[int, str]:
        """Estimate prompt size from the full unconsolidated session tail."""
        from navin.session.context_usage_meta import (
            last_context_usage_row,
            last_preload_skills,
        )

        history = self._full_unconsolidated_history(session)
        channel, chat_id = (session.key.split(":", 1) if ":" in session.key else (None, None))
        # Include archived summary in estimation so the budget accounts for it.
        meta = session.metadata.get("_last_summary")
        summary = meta.get("text") if isinstance(meta, dict) else (meta if isinstance(meta, str) else None)
        skill_names = last_preload_skills(session.metadata)
        probe_messages = self._build_messages(
            history=history,
            current_message="[token-probe]",
            channel=channel,
            chat_id=chat_id,
            sender_id=None,
            session_summary=summary,
            session_metadata=session.metadata,
            session_key=session.key,
            unified_session=self.unified_session,
            skill_names=skill_names,
        )
        estimated, source = estimate_prompt_tokens_chain(
            runtime.provider,
            runtime.model,
            probe_messages,
            self._get_tool_definitions(),
        )
        # Provider-reported peak from the last turn is ground truth for fill.
        # Prefer it when the probe under-counts (skills/tools drift).
        last = last_context_usage_row(session.metadata)
        if last is not None:
            try:
                peak = int(last.get("peak_prompt_tokens") or last.get("prompt_tokens") or 0)
            except (TypeError, ValueError):
                peak = 0
            if peak > estimated:
                return peak, f"{source}+last_peak"
        return estimated, source

    def _input_token_budget(self, runtime: LLMRuntime) -> int:
        """Available input token budget for consolidation LLM."""
        return (
            runtime.context_window_tokens
            - runtime.generation.max_tokens
            - self._SAFETY_BUFFER
        )

    def _truncate_to_token_budget(self, text: str, *, runtime: LLMRuntime) -> str:
        """Truncate text so it fits within the consolidation LLM's token budget."""
        budget = self._input_token_budget(runtime)
        if budget <= 0:
            return truncate_text(text, _RAW_ARCHIVE_MAX_CHARS)
        return truncate_text_to_tokens(text, budget)

    async def archive(
        self,
        messages: list[dict],
        *,
        runtime: LLMRuntime,
        session_key: str | None = None,
        summary_messages: list[dict] | None = None,
    ) -> str | None:
        """Summarize messages via LLM and append to history.jsonl.

        ``messages`` are the messages being archived (removed from the live
        session); they are what gets raw-dumped if the LLM call fails.
        ``summary_messages``, when given, lets callers include retained
        messages in the summary without archiving them.

        Returns the summary text on success, None if nothing to archive.
        """
        if not messages:
            return None
        messages_to_summarize = public_history_messages(
            summary_messages if summary_messages is not None else messages
        )
        try:
            formatted = MemoryStore._format_messages(messages_to_summarize)
            formatted = self._truncate_to_token_budget(formatted, runtime=runtime)
            response = await asyncio.wait_for(
                runtime.provider.chat_with_retry(
                    model=runtime.model,
                    messages=[
                        {
                            "role": "system",
                            "content": render_template(
                                "agent/consolidator_archive.md",
                                strip=True,
                            ),
                        },
                        {"role": "user", "content": formatted},
                    ],
                    tools=None,
                    tool_choice=None,
                    temperature=runtime.generation.temperature,
                    max_tokens=runtime.generation.max_tokens,
                    # Never inherit the turn's effort: this call runs *before* the
                    # visible turn, and a reasoning model told to think hard about
                    # writing a summary is exactly the multi-minute first-token
                    # stall users report on OpenRouter.
                    reasoning_effort="none",
                ),
                timeout=_CONSOLIDATION_LLM_TIMEOUT_S,
            )
            if response.finish_reason == "error":
                raise RuntimeError(f"LLM returned error: {response.content}")
            summary = response.content or "[no summary]"
            self.store.append_history(
                summary,
                max_chars=_ARCHIVE_SUMMARY_MAX_CHARS,
                session_key=session_key,
            )
            return summary
        except Exception:
            logger.warning("Consolidation LLM call failed, raw-dumping to history")
            self.store.raw_archive(messages, session_key=session_key)
            return None

    async def handoff_brief(
        self,
        messages: list[dict],
        *,
        runtime: LLMRuntime,
    ) -> str | None:
        """Summarize a chunk for continuing the work, not for remembering it.

        ``archive`` extracts durable memory facts, which is right for
        history.jsonl and wrong for the slot that is re-injected into the next
        prompt: its instructions deliberately skip anything derivable from the
        repository, so the plan, the files already edited and the tests already
        run are exactly what it drops. When the window fills mid-task that is the
        only state worth keeping, hence a second pass with its own brief.

        Returns None when the model has nothing to carry over, so the caller can
        leave the previous brief in place rather than overwrite it with noise.
        """
        if not messages:
            return None
        try:
            formatted = MemoryStore._format_messages(public_history_messages(messages))
            formatted = self._truncate_to_token_budget(formatted, runtime=runtime)
            response = await asyncio.wait_for(
                runtime.provider.chat_with_retry(
                    model=runtime.model,
                    messages=[
                        {
                            "role": "system",
                            "content": render_template(
                                "agent/consolidator_handoff.md",
                                strip=True,
                            ),
                        },
                        {"role": "user", "content": formatted},
                    ],
                    tools=None,
                    tool_choice=None,
                    temperature=runtime.generation.temperature,
                    max_tokens=runtime.generation.max_tokens,
                    # Same as archive(): a summary needs no chain-of-thought, and
                    # this call blocks the turn that is about to start.
                    reasoning_effort="none",
                ),
                timeout=_CONSOLIDATION_LLM_TIMEOUT_S,
            )
            if response.finish_reason == "error":
                raise RuntimeError(f"LLM returned error: {response.content}")
            brief = (response.content or "").strip()
        except Exception:
            # Losing the brief degrades the next turn; losing the archive would
            # lose the messages. The archive already ran, so this is survivable.
            logger.warning("Handoff brief failed; keeping the previous brief")
            return None
        if not brief or brief.lower() in {"(nothing)", "nothing"}:
            return None
        return brief

    async def maybe_consolidate_by_tokens(
        self,
        session: Session,
        *,
        runtime: LLMRuntime,
        replay_max_messages: int | None = None,
    ) -> None:
        """Loop: archive old messages until prompt fits within safe budget.

        The budget reserves space for completion tokens and a safety buffer
        so the LLM request never exceeds the context window.
        """
        if runtime.context_window_tokens <= 0:
            return

        lock = self.get_lock(session.key)
        async with lock:
            # Refresh session reference: AutoCompact may have replaced it.
            fresh = self.sessions.get_or_create(session.key)
            if fresh is not session:
                session = fresh
            if not session.messages:
                return

            budget = self._input_token_budget(runtime)
            target = int(budget * self.consolidation_ratio)
            last_summary = await self._consolidate_replay_overflow(
                session,
                replay_max_messages,
                runtime=runtime,
            )
            # Fast path: when the cached projection proves the session is far
            # below budget, skip the probe-prompt rebuild and tiktoken pass
            # that otherwise tax every BUILD of a healthy session.
            if self._clearly_under_budget(session, runtime=runtime, budget=budget):
                logger.debug(
                    "Token consolidation lazy-idle {} (projected under {}%% of budget)",
                    session.key,
                    int(self._LAZY_ESTIMATE_HEADROOM * 100),
                )
                self._persist_last_summary(session, last_summary)
                return
            try:
                estimated, source = self.estimate_session_prompt_tokens(
                    session,
                    runtime=runtime,
                )
            except Exception:
                logger.exception("Token estimation failed for {}", session.key)
                estimated, source = 0, "error"
            self._remember_estimate(session, runtime=runtime, estimated=estimated)
            if estimated <= 0:
                self._persist_last_summary(session, last_summary)
                return
            proactive = int(budget * self._PROACTIVE_CONSOLIDATION_RATIO)
            tool_heavy = sum(
                1
                for m in session.messages[session.last_consolidated:]
                if isinstance(m, dict) and m.get("role") == "tool"
            ) >= 8
            consolidate_floor = (
                min(proactive, budget) if tool_heavy else budget
            )
            if estimated < consolidate_floor:
                unconsolidated_count = len(session.messages) - session.last_consolidated
                logger.debug(
                    "Token consolidation idle {}: {}/{} via {}, msgs={} floor={}",
                    session.key,
                    estimated,
                    runtime.context_window_tokens,
                    source,
                    unconsolidated_count,
                    consolidate_floor,
                )
                self._persist_last_summary(session, last_summary)
                return

            tokens_before = estimated
            archived_messages = 0
            # Collected across rounds so the handoff brief is written once, over
            # everything this call dropped, instead of once per round.
            dropped: list[dict] = []
            for round_num in range(self._MAX_CONSOLIDATION_ROUNDS):
                if estimated <= target:
                    break

                boundary = self.pick_consolidation_boundary(session, max(1, estimated - target))
                if boundary is None:
                    logger.debug(
                        "Token consolidation: no safe boundary for {} (round {})",
                        session.key,
                        round_num,
                    )
                    break

                end_idx = boundary[0]

                chunk = session.messages[session.last_consolidated:end_idx]
                if not chunk:
                    break

                logger.info(
                    "Token consolidation round {} for {}: {}/{} via {}, chunk={} msgs",
                    round_num,
                    session.key,
                    estimated,
                    runtime.context_window_tokens,
                    source,
                    len(chunk),
                )
                summary = await self.archive(
                    chunk,
                    runtime=runtime,
                    session_key=session.key,
                )
                # Advance the cursor either way: on success the chunk was
                # summarized; on failure archive() already raw-archived it as
                # a breadcrumb. Re-archiving the same chunk on the next call
                # would just emit duplicate [RAW] entries.
                if summary:
                    last_summary = summary
                dropped.extend(chunk)
                archived_messages += len(chunk)
                session.last_consolidated = end_idx
                self.sessions.save(session)
                if not summary:
                    # LLM is degraded - stop hammering it this call;
                    # the next invocation can retry a fresh chunk.
                    break

                try:
                    estimated, source = self.estimate_session_prompt_tokens(
                        session,
                        runtime=runtime,
                    )
                except Exception:
                    logger.exception("Token estimation failed for {}", session.key)
                    estimated, source = 0, "error"
                if estimated <= 0:
                    break
            self._remember_estimate(session, runtime=runtime, estimated=estimated)

            # Persist a summary to session metadata so it can be injected into
            # the runtime context on the next prepare_session() call. What goes
            # in is the handoff brief when the work was summarised here; the
            # memory-fact archive is only a fallback, since it is written for
            # long-term recall rather than for resuming the task.
            if dropped:
                brief = await self.handoff_brief(dropped, runtime=runtime)
                if brief:
                    last_summary = brief
                    self._persist_resume_brief(session, brief)
            self._persist_last_summary(session, last_summary)
            if archived_messages:
                self._notify_compacted({
                    "session_key": session.key,
                    "kind": "consolidation",
                    "messages_archived": archived_messages,
                    "tokens_before": tokens_before,
                    "tokens_after": estimated,
                })

    async def compact_idle_session(
        self,
        session_key: str,
        *,
        runtime: LLMRuntime,
        max_suffix: int = 8,
    ) -> str | None:
        """Hard-truncate an idle session under the consolidation lock.

        Used by AutoCompact so all session mutation goes through a single
        lock-protected path.  Returns the summary text on success, ``None``
        if the LLM failed (raw_archive fallback), or ``""`` if there was
        nothing to archive.
        """
        lock = self.get_lock(session_key)
        async with lock:
            self.sessions.invalidate(session_key)
            session = self.sessions.get_or_create(session_key)

            messages_to_summarize = list(session.messages[session.last_consolidated:])
            if not messages_to_summarize:
                self.sessions.save(session)
                return ""

            probe = Session(
                key=session.key,
                messages=messages_to_summarize.copy(),
                created_at=session.created_at,
                updated_at=session.updated_at,
                metadata={},
                last_consolidated=0,
            )
            result = probe.retain_recent_legal_suffix(max_suffix, extend_to_user=True)
            messages_to_keep = probe.messages
            messages_to_remove = result.dropped[result.already_consolidated_count:]

            if not messages_to_remove and not messages_to_keep:
                self.sessions.save(session)
                return ""

            last_active = session.updated_at
            summary: str | None = ""
            if messages_to_remove:
                # Summarize the retained suffix too, but only remove/raw-dump
                # the messages that are no longer kept in the live session.
                summary = await self.archive(
                    messages_to_remove,
                    runtime=runtime,
                    session_key=session_key,
                    summary_messages=messages_to_summarize,
                )

            # The re-injected text describes where the work stood, not what to
            # remember about the user; the archive above already covers the
            # latter. A user coming back to an idle session needs the former.
            resume = None
            if messages_to_remove:
                resume = await self.handoff_brief(
                    messages_to_summarize, runtime=runtime
                )
            carried = resume or summary
            if carried and carried != "(nothing)":
                session.metadata["_last_summary"] = {
                    "text": carried,
                    "last_active": last_active.isoformat(),
                }
            if resume:
                self._persist_resume_brief(session, resume)

            session.messages = messages_to_keep
            session.last_consolidated = 0
            self.sessions.save(session)

            if messages_to_remove:
                logger.info(
                    "Idle-session compact for {}: archived={}, kept={}, summary={}",
                    session_key,
                    len(messages_to_remove),
                    len(messages_to_keep),
                    bool(summary),
                )
                self._notify_compacted({
                    "session_key": session_key,
                    "kind": "idle",
                    "messages_archived": len(messages_to_remove),
                })

            return summary
