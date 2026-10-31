# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Session management for conversation history."""

import base64
import json
import os
import re
import shutil
import threading
import weakref
from collections import OrderedDict
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from loguru import logger

from navin.config.paths import get_legacy_sessions_dir
from navin.runtime_context import (
    RUNTIME_CONTEXT_HISTORY_META,
    public_history_message,
)
from navin.security.workspace_access import WORKSPACE_SCOPE_METADATA_KEY
from navin.utils.helpers import (
    ensure_dir,
    estimate_message_tokens,
    find_legal_message_start,
    image_placeholder_text,
    recent_message_start_index,
    safe_filename,
    strip_think,
)
from navin.utils.subagent_channel_display import scrub_subagent_announce_body

FILE_MAX_MESSAGES = 2000
# Messages that leave the live session (idle compaction, /compact, the file
# cap) stay readable on screen: they go, oldest first, to a display-only file
# in ``.navin/sessions/earlier/``. The model never replays it.
EARLIER_DIR_NAME = "earlier"
EARLIER_MAX_MESSAGES = 4000
# Rewriting the file on every turn past the cap would cost a full write per
# turn: it is trimmed back to EARLIER_MAX_MESSAGES once it is this much over.
_EARLIER_TRIM_SLACK = 500
# A tool result shows in a folded card; its full text stays out of the file.
_EARLIER_TOOL_RESULT_CHARS = 4000
_EARLIER_HEADER_TYPE = "earlier"
# Session metadata: when the conversation on this key last started over (/new),
# as epoch seconds. Absent for a conversation never restarted.
CONVERSATION_STARTED_KEY = "conversation_started_at"
MIN_REPLAY_MAX_MESSAGES = 120
REPLAY_TOKENS_PER_MESSAGE = 100
_MESSAGE_TIME_PREFIX_RE = re.compile(r"^\[Message Time: [^\]]+\]\n?")
_LOCAL_IMAGE_BREADCRUMB_RE = re.compile(r"^\[image: (?:/|~)[^\]]+\]\s*$")
_TOOL_CALL_ECHO_RE = re.compile(r'^\s*(?:generate_image|message)\([^)]*\)\s*$')
_SESSION_PREVIEW_MAX_CHARS = 120
_SESSION_LIST_PREVIEW_MAX_RECORDS = 200
_SESSION_LIST_PREVIEW_MAX_CHARS = 1_000_000
_FORK_VOLATILE_METADATA_KEYS = {
    "goal_state",
    "pending_user_turn",
    "runtime_checkpoint",
    "thread_goal",
    "title",
    "title_user_edited",
    # navin.session.turn_recovery.RECOVERY_KEY: a fork of a chat cut mid-turn
    # must not replay the source's request on the next start.
    "turn_recovery",
}


def replay_max_messages_for_context(context_window_tokens: int | None) -> int:
    if not context_window_tokens or context_window_tokens <= 0:
        return FILE_MAX_MESSAGES
    return min(
        FILE_MAX_MESSAGES,
        max(MIN_REPLAY_MAX_MESSAGES, context_window_tokens // REPLAY_TOKENS_PER_MESSAGE),
    )


def _sanitize_assistant_replay_text(content: str) -> str:
    """Remove internal replay artifacts that the model may have copied before.

    These strings are useful as runtime/session metadata, but when they appear
    in assistant examples they become demonstrations for the model to repeat.
    """
    content = _MESSAGE_TIME_PREFIX_RE.sub("", content, count=1)
    lines = [
        line
        for line in content.splitlines()
        if not _LOCAL_IMAGE_BREADCRUMB_RE.match(line)
        and not _TOOL_CALL_ECHO_RE.match(line)
    ]
    return "\n".join(lines).strip()


def _text_preview(content: Any) -> str:
    """Return compact display text for session lists."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                value = block.get("text")
                if isinstance(value, str):
                    parts.append(value)
        text = " ".join(parts)
    else:
        return ""
    text = _sanitize_assistant_replay_text(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > _SESSION_PREVIEW_MAX_CHARS:
        text = text[: _SESSION_PREVIEW_MAX_CHARS - 1].rstrip() + "…"
    return text


def _subagent_result_row(message: dict[str, Any]) -> bool:
    """A subagent result persisted in the assistant shape (late delivery).

    Results drained during a turn are already user rows; only this shape
    needs a different role when the history is replayed to the model.
    """
    return (
        message.get("role") == "assistant"
        and message.get("injected_event") == "subagent_result"
        and not message.get("tool_calls")
    )


def _message_preview_text(message: dict[str, Any]) -> str:
    """Session list preview text; subagent inject blobs are shortened for display."""
    message = public_history_message(message)
    content: Any = message.get("content")
    if message.get("injected_event") == "subagent_result" and isinstance(content, str):
        content = scrub_subagent_announce_body(content)
    if isinstance(content, str):
        from navin.cognition.episodes import strip_runtime_context

        content = strip_runtime_context(content)
    if isinstance(content, str) and content.lstrip().startswith("["):
        # Expanded workflow briefs must not become the sidebar label.
        from navin.session.webui_turns import title_source_from_user_text

        focused = title_source_from_user_text(content)
        if focused:
            content = focused
        elif "Skills for this mission" in content:
            return ""
    return _text_preview(content)


def _metadata_scope(metadata: Any) -> dict[str, Any] | None:
    """The project a desktop chat was opened in, as saved in its metadata."""
    if not isinstance(metadata, dict):
        return None
    scope = metadata.get(WORKSPACE_SCOPE_METADATA_KEY)
    return dict(scope) if isinstance(scope, dict) else None


def _metadata_title(metadata: Any) -> str:
    if not isinstance(metadata, dict):
        return ""
    title = metadata.get("title")
    if not isinstance(title, str):
        return ""
    if metadata.get("title_user_edited") is True:
        return title
    return strip_think(title)


@dataclass
class RetentionResult:
    dropped: list[dict]
    already_consolidated_count: int


@dataclass
class Session:
    """A conversation session."""

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # Number of messages already consolidated to files
    # Fingerprint of the state last loaded from or handed to disk; set per
    # instance by SessionManager (ClassVar keeps it out of the dataclass fields).
    _clean_fingerprint: ClassVar[tuple[Any, ...] | None] = None

    def __post_init__(self) -> None:
        # An out-of-range offset (corrupt metadata) would hide all history; reset it.
        if (
            isinstance(self.last_consolidated, bool)
            or not isinstance(self.last_consolidated, int)
            or not 0 <= self.last_consolidated <= len(self.messages)
        ):
            self.last_consolidated = 0

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(
        self,
        max_messages: int = FILE_MAX_MESSAGES,
        *,
        max_tokens: int = 0,
        extend_to_user: bool = False,
        include_runtime_context: bool = True,
        with_media_refs: bool = False,
        subagent_results_as_input: bool = False,
    ) -> list[dict[str, Any]]:
        """Return unconsolidated messages for LLM input.

        History is sliced by message count first (``max_messages``), then by
        token budget from the tail (``max_tokens``) when provided.

        ``with_media_refs`` keeps the persisted attachment paths of user turns
        under ``_meta["media"]`` so the agent loop can show a vision model the
        images again; providers strip ``_meta`` before the call.

        ``subagent_results_as_input`` replays a subagent result stored in the
        assistant shape (it landed after its turn) as a user turn: the model
        reads it as input. Display callers keep the stored role.
        """
        unconsolidated = self.messages[self.last_consolidated:]
        max_messages = max_messages if max_messages > 0 else FILE_MAX_MESSAGES
        start_idx = recent_message_start_index(
            unconsolidated,
            max_messages,
            extend_to_user=extend_to_user,
        )
        sliced = unconsolidated[start_idx:]

        # Avoid starting mid-turn when possible, except for proactive
        # assistant deliveries that the user may be replying to.
        for i, message in enumerate(sliced):
            if message.get("role") == "user":
                start = i
                if i > 0 and sliced[i - 1].get("_channel_delivery"):
                    start = i - 1
                sliced = sliced[start:]
                break

        # Drop orphan tool results at the front.
        start = find_legal_message_start(sliced)
        if start:
            sliced = sliced[start:]

        out: list[dict[str, Any]] = []
        replayed_as_input: set[int] = set()
        for message in sliced:
            if message.get("_command"):
                continue
            has_persisted_runtime_context = isinstance(
                message.get(RUNTIME_CONTEXT_HISTORY_META),
                dict,
            )
            if not include_runtime_context:
                message = public_history_message(message)
            content = message.get("content", "")
            role = message.get("role")
            if role == "assistant" and isinstance(content, str):
                content = _sanitize_assistant_replay_text(content)
            # Synthesize an ``[image: path]`` breadcrumb from the persisted
            # ``media`` kwarg so LLM replay still sees *something* where the
            # image used to be. Without this, an image-only user turn
            # replays as an empty user message - the assistant's reply then
            # looks like it's responding to nothing.
            media = message.get("media")
            if role == "user" and isinstance(media, list) and media and isinstance(content, str):
                breadcrumbs = "\n".join(
                    image_placeholder_text(p) for p in media if isinstance(p, str) and p
                )
                content = f"{content}\n{breadcrumbs}" if content else breadcrumbs
            cli_apps = message.get("cli_apps")
            if (
                include_runtime_context
                and not has_persisted_runtime_context
                and role == "user"
                and isinstance(cli_apps, list)
                and cli_apps
                and isinstance(content, str)
            ):
                cli_lines: list[str] = []
                for item in cli_apps[:8]:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip().lower()
                    if not name:
                        continue
                    entry = str(item.get("entry_point") or "unknown").strip() or "unknown"
                    cli_lines.append(
                        f"[CLI App Attachment: @{name}; tool=run_cli_app; entry_point={entry}; "
                        f"skill=skills/cli-app-{name}/SKILL.md]"
                    )
                if cli_lines:
                    breadcrumbs = "\n".join(cli_lines)
                    content = f"{content}\n{breadcrumbs}" if content else breadcrumbs
            if role == "assistant" and isinstance(content, str) and not content.strip():
                if not any(key in message for key in ("tool_calls", "reasoning_content", "thinking_blocks")):
                    continue
            entry: dict[str, Any] = {"role": message["role"], "content": content}
            for key in ("tool_calls", "tool_call_id", "name", "reasoning_content", "thinking_blocks"):
                if key in message:
                    entry[key] = message[key]
            if with_media_refs and role == "user" and isinstance(media, list):
                refs = [p for p in media if isinstance(p, str) and p]
                if refs:
                    entry["_meta"] = {"media": refs}
            out.append(entry)
            if subagent_results_as_input and _subagent_result_row(message):
                replayed_as_input.add(id(entry))

        if max_tokens > 0 and out:
            kept: list[dict[str, Any]] = []
            used = 0
            for message in reversed(out):
                tokens = estimate_message_tokens(message)
                if kept and used + tokens > max_tokens:
                    break
                kept.append(message)
                used += tokens
            kept.reverse()

            # Keep history aligned to the first visible user turn.
            first_user = next((i for i, m in enumerate(kept) if m.get("role") == "user"), None)
            if first_user is not None:
                kept = kept[first_user:]
            else:
                # Tight token budgets can otherwise leave assistant-only tails.
                # If a user turn exists in the unsliced output, recover the
                # nearest one even if it slightly exceeds the token budget.
                recovered_user = next(
                    (i for i in range(len(out) - 1, -1, -1) if out[i].get("role") == "user"),
                    None,
                )
                if recovered_user is not None:
                    kept = out[recovered_user:]

            # And keep a legal tool-call boundary at the front.
            start = find_legal_message_start(kept)
            if start:
                kept = kept[start:]
            out = kept
        # A subagent result that lands after its turn ended is stored as an
        # assistant row (the chat shows it as a result card). Replayed as an
        # assistant turn it merged with the previous answer and, at the end of
        # the conversation, every provider dropped it (no assistant prefill):
        # the model never saw the result and repeated its last answer. For the
        # model it is input. Converted last, so the alignment above still
        # starts the window on a request the user typed.
        for entry in out:
            if id(entry) in replayed_as_input:
                entry["role"] = "user"
        return out

    def clear(self) -> None:
        """Clear all messages and reset session to initial state.

        The key stays (the CLI keeps ``cli:direct`` across /new), so the time
        the new conversation started is kept: work recorded under the same key
        before it, such as earlier subagents, belongs to a previous conversation.
        """
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()
        self.metadata.pop("_last_summary", None)
        self.metadata[CONVERSATION_STARTED_KEY] = self.updated_at.timestamp()

    def retain_recent_legal_suffix(
        self,
        max_messages: int,
        *,
        extend_to_user: bool = False,
    ) -> RetentionResult:
        """Keep a legal recent suffix, optionally extending it back to a user turn.

        Returns a RetentionResult with dropped messages and how many of those
        were in the already-consolidated prefix. This method mutates
        self.messages and self.last_consolidated in place.
        """
        if max_messages <= 0:
            dropped = list(self.messages)
            lc = self.last_consolidated
            self.clear()
            return RetentionResult(
                dropped=dropped,
                already_consolidated_count=min(lc, len(dropped)),
            )
        if len(self.messages) <= max_messages:
            return RetentionResult(
                dropped=[],
                already_consolidated_count=0,
            )

        original = list(self.messages)
        before_lc = self.last_consolidated

        start_idx = max(0, len(self.messages) - max_messages)
        if extend_to_user:
            start_idx = next(
                (i for i in range(start_idx, -1, -1) if self.messages[i].get("role") == "user"),
                start_idx,
            )

        retained = self.messages[start_idx:]

        # Prefer starting at a user turn when one exists within the retained window.
        first_user = next((i for i, m in enumerate(retained) if m.get("role") == "user"), None)
        if first_user is not None:
            retained = retained[first_user:]
        elif not extend_to_user:
            # If the hard-capped tail is assistant/tool-only, anchor to the
            # latest user in the full session and take a capped forward window.
            latest_user = next(
                (i for i in range(len(self.messages) - 1, -1, -1)
                 if self.messages[i].get("role") == "user"),
                None,
            )
            if latest_user is not None:
                retained = self.messages[latest_user: latest_user + max_messages]

        # Mirror get_history(): avoid persisting orphan tool results at the front.
        start = find_legal_message_start(retained)
        if start:
            retained = retained[start:]

        # Hard-cap guarantee unless the caller requested user-turn extension.
        if not extend_to_user and len(retained) > max_messages:
            retained = retained[-max_messages:]
            start = find_legal_message_start(retained)
            if start:
                retained = retained[start:]

        # Compute actually-dropped messages using identity comparison so that
        # even when retained is a non-contiguous slice of original (the else
        # branch above), we never duplicate or lose messages.
        retained_ids = set(id(m) for m in retained)
        dropped = [m for m in original if id(m) not in retained_ids]

        # Count how many dropped messages were in the already-consolidated
        # prefix of the original list.  This cannot be a simple min() because
        # dropped may include messages from *after* the consolidated prefix
        # (e.g. in the else branch).
        already_consolidated = sum(
            1 for i, m in enumerate(original)
            if i < before_lc and id(m) not in retained_ids
        )

        # New last_consolidated = count of retained messages that were inside
        # the old consolidated prefix.
        new_lc = sum(
            1 for i, m in enumerate(original)
            if i < before_lc and id(m) in retained_ids
        )

        self.messages = retained
        self.last_consolidated = new_lc
        self.updated_at = datetime.now()
        return RetentionResult(
            dropped=dropped,
            already_consolidated_count=already_consolidated,
        )

    def enforce_file_cap(
        self,
        on_archive: Any = None,
        limit: int = FILE_MAX_MESSAGES,
        on_earlier: Any = None,
    ) -> None:
        """Bound session message growth by archiving and trimming old prefixes.

        ``on_earlier`` receives the dropped messages that came before the kept
        ones, oldest first, so the chat can still show them.
        """
        if limit <= 0 or len(self.messages) <= limit:
            return

        original = list(self.messages)
        result = self.retain_recent_legal_suffix(limit)
        if not result.dropped:
            return

        if on_earlier:
            on_earlier(messages_before_kept(original, self.messages))
        archive_chunk = result.dropped[result.already_consolidated_count:]
        if archive_chunk and on_archive:
            on_archive(archive_chunk)
        logger.info(
            "Session file cap hit for {}: dropped {}, raw-archived {}, kept {}",
            self.key,
            len(result.dropped),
            len(archive_chunk),
            len(self.messages),
        )



def messages_before_kept(
    original: list[dict[str, Any]], kept: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """The prefix of ``original`` that sits before the first kept message.

    Retention keeps a suffix (the same dict objects), so this is what left the
    live session, in reading order. Messages dropped after a kept window (an
    assistant-only tail) are not included: shown before the kept ones they
    would be out of order.
    """
    kept_ids = {id(message) for message in kept}
    first = next(
        (index for index, message in enumerate(original) if id(message) in kept_ids),
        len(original),
    )
    return original[:first]


def _earlier_row(message: dict[str, Any]) -> dict[str, Any]:
    """A message as the earlier-messages file keeps it (display only)."""
    content = message.get("content")
    if (
        message.get("role") == "tool"
        and isinstance(content, str)
        and len(content) > _EARLIER_TOOL_RESULT_CHARS
    ):
        row = dict(message)
        row["content"] = content[:_EARLIER_TOOL_RESULT_CHARS] + "\n... (truncated)"
        return row
    return message


DEFAULT_MAX_CACHED_SESSIONS = 256


def _session_fingerprint(session: Session) -> tuple[Any, ...] | None:
    """Cheap identity of a session's state, compared to detect unsaved edits.

    Returns ``None`` when the state cannot be fingerprinted, which callers
    treat as dirty (never evicted).
    """
    try:
        meta = json.dumps(session.metadata, sort_keys=True, default=str)
        last = session.messages[-1] if session.messages else None
        last_text = json.dumps(last, sort_keys=True, default=str) if last is not None else ""
    except Exception:
        return None
    return (
        id(session.messages),
        len(session.messages),
        id(last),
        hash(last_text),
        session.updated_at,
        session.last_consolidated,
        hash(meta),
    )


def _mark_clean(session: Session) -> None:
    session._clean_fingerprint = _session_fingerprint(session)


def _is_dirty(session: Session) -> bool:
    saved = session._clean_fingerprint
    return saved is None or saved != _session_fingerprint(session)


def _metadata_copy(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """A copy of a metadata record the caller may change (two levels, no deep walk)."""
    if record is None:
        return None
    metadata = record.get("metadata")
    return {**record, "metadata": dict(metadata) if isinstance(metadata, dict) else {}}


class _SessionCache:
    """Bounded session cache that never holds two copies of one session.

    The ``max_strong`` most recently used sessions are kept alive by strong
    references. Every session handed out is also tracked weakly, so a session
    that fell out of the LRU but is still referenced (an agent turn holding it
    across awaits) is found again as the very same object instead of being
    reloaded as a second copy whose save would clobber the first. A session is
    only released from the LRU when it looks clean (state equals what was last
    loaded or saved) and has no save in flight; otherwise it stays pinned.

    Mapping-style helpers (``get``, ``in``, ``items``...) see every live
    session, strong or weak.
    """

    def __init__(self, manager: "SessionManager", max_strong: int) -> None:
        self._manager = manager
        self.max_strong = max_strong
        self._strong: OrderedDict[str, Session] = OrderedDict()
        self._weak: weakref.WeakValueDictionary[str, Session] = weakref.WeakValueDictionary()

    def get(self, key: str, default: Session | None = None) -> Session | None:
        session = self._strong.get(key)
        if session is None:
            session = self._weak.get(key)
        return session if session is not None else default

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and self.get(key) is not None

    def __getitem__(self, key: str) -> Session:
        session = self.get(key)
        if session is None:
            raise KeyError(key)
        return session

    def __setitem__(self, key: str, session: Session) -> None:
        self.put(key, session)

    def put(self, key: str, session: Session) -> None:
        """Store *session* as the live object for *key* and mark it recently used."""
        self._weak[key] = session
        self._strong[key] = session
        self._strong.move_to_end(key)
        self._shrink()

    def touch(self, key: str, session: Session) -> None:
        """Re-pin a live session (it may have been released from the LRU)."""
        if self._strong.get(key) is session:
            self._strong.move_to_end(key)
            return
        self.put(key, session)

    def pop(self, key: str, default: Session | None = None) -> Session | None:
        session = self.get(key)
        self._strong.pop(key, None)
        self._weak.pop(key, None)
        return session if session is not None else default

    def _shrink(self) -> None:
        # A released session can still be edited by whoever holds it; pin it
        # again as soon as it differs from disk so dropping the last reference
        # does not lose the edit.
        for key, session in list(self._weak.items()):
            if key in self._strong:
                continue
            if self._manager._save_in_flight(key) or _is_dirty(session):
                self._strong[key] = session
                self._strong.move_to_end(key, last=False)
        excess = len(self._strong) - self.max_strong
        if excess <= 0:
            return
        for key in list(self._strong):
            if excess <= 0:
                break
            session = self._strong[key]
            if self._manager._save_in_flight(key) or _is_dirty(session):
                continue  # unsaved state: keep it pinned
            # Still in the weak map: whoever holds it keeps getting this object.
            del self._strong[key]
            excess -= 1

    def keys(self) -> list[str]:
        return [key for key, _ in self.items()]

    def values(self) -> list[Session]:
        return [session for _, session in self.items()]

    def items(self) -> list[tuple[str, Session]]:
        seen: dict[str, Session] = dict(self._strong)
        for key, session in list(self._weak.items()):
            seen.setdefault(key, session)
        return list(seen.items())

    def __iter__(self) -> Iterator[str]:
        return iter(self.keys())

    def __len__(self) -> int:
        return len(self.items())

    @property
    def strong_count(self) -> int:
        return len(self._strong)


def sessions_dir_for(workspace: Path) -> Path:
    """Where a workspace keeps its conversations: inside ``.navin/``, so they
    never land in the user's project tree (or its git commits)."""
    return Path(workspace) / ".navin" / "sessions"


# Conversation files Navin wrote at ``<workspace>/sessions`` before they moved
# under ``.navin/``. Only these are moved: a project's own ``sessions/``
# folder (app code, fixtures) is never touched.
_SESSION_FILE_RE = re.compile(r"^(?:[A-Za-z0-9_-]+\.jsonl|\.webui_session_index\.json)$")


def _migrate_root_sessions(workspace: Path) -> Path:
    """Move legacy ``<workspace>/sessions`` conversations into ``.navin/sessions``.

    Idempotent and never overwrites: a file already present at the new place
    is kept and the old copy stays put. The old folder is removed only when
    the move left it empty.
    """
    target = sessions_dir_for(workspace)
    legacy = workspace / "sessions"
    try:
        if not legacy.is_dir() or legacy.is_symlink():
            return target
        entries = list(legacy.iterdir())
        if not entries or not all(p.is_file() and _SESSION_FILE_RE.match(p.name) for p in entries):
            return target
        target.mkdir(parents=True, exist_ok=True)
        for path in entries:
            dest = target / path.name
            if not dest.exists():
                shutil.move(str(path), str(dest))
        with suppress(OSError):
            legacy.rmdir()
        logger.info("Moved conversations from {} to {}", legacy, target)
    except OSError as exc:
        logger.warning("Could not move legacy sessions from {}: {}", legacy, exc)
    return target


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in ``<workspace>/.navin/sessions``.
    """

    def __init__(self, workspace: Path, *, max_cached_sessions: int = DEFAULT_MAX_CACHED_SESSIONS):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(_migrate_root_sessions(Path(workspace)))
        self.legacy_sessions_dir = get_legacy_sessions_dir()
        # Bounded: only the most recently used sessions are pinned; others stay
        # reachable (same object) while anything still references them.
        self._cache = _SessionCache(self, max(1, int(max_cached_sessions)))
        # Metadata lines read from disk, by path, valid while (mtime, size) match.
        self._metadata_reads: dict[str, tuple[tuple[int, int], dict[str, Any] | None]] = {}
        self._pending_fsync: set[Path] = set()
        # Writes of one session are ordered: every save takes the next number
        # when it is asked for, and a write never replaces a newer one, even
        # when a background checkpoint write finishes after a later save.
        self._save_locks: dict[str, threading.Lock] = {}
        self._save_requested: dict[str, int] = {}
        self._save_written: dict[str, int] = {}
        # Earlier-messages files: written in order by one worker thread, with
        # the message count of each file once it has been counted.
        self._earlier_guard = threading.Lock()
        self._earlier_executor: ThreadPoolExecutor | None = None
        self._earlier_pending: dict[str, Future[None]] = {}
        self._earlier_counts: dict[str, int] = {}

    @property
    def max_cached_sessions(self) -> int:
        """How many recently used sessions are kept in memory unconditionally."""
        return self._cache.max_strong

    @max_cached_sessions.setter
    def max_cached_sessions(self, value: int) -> None:
        self._cache.max_strong = max(1, int(value))
        self._cache._shrink()

    def _save_in_flight(self, key: str) -> bool:
        """True while a requested save of *key* has not reached the disk yet."""
        return self._save_requested.get(key, 0) > self._save_written.get(key, 0)

    @staticmethod
    def safe_key(key: str) -> str:
        """Public helper used by HTTP handlers to map an arbitrary key to a stable filename stem."""
        return safe_filename(key.replace(":", "_"))

    @staticmethod
    def _storage_key(key: str) -> str:
        """Collision-resistant encoding for internal session storage filenames."""
        return base64.urlsafe_b64encode(key.encode()).decode().rstrip("=")

    @staticmethod
    def _decode_storage_key(stem: str) -> str | None:
        """Reverse _storage_key(): decode a base64url (no-padding) stem back to the original key."""
        try:
            # Restore padding stripped by rstrip("=")
            padding = 4 - len(stem) % 4
            if padding != 4:
                stem += "=" * padding
            return base64.urlsafe_b64decode(stem).decode("utf-8")
        except Exception:
            return None

    def _get_session_path(self, key: str) -> Path:
        """Get the collision-resistant workspace path for a session."""
        return self.sessions_dir / f"{self._storage_key(key)}.jsonl"

    def _get_legacy_lossy_path(self, key: str) -> Path:
        """Previous workspace session path using lossy ':' to '_' replacement."""
        return self.sessions_dir / f"{safe_filename(key.replace(':', '_'))}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        """Legacy global session path (~/.navin/sessions/)."""
        return self.legacy_sessions_dir / f"{self.safe_key(key)}.jsonl"

    def earlier_messages_path(self, key: str) -> Path:
        """Display-only file of the messages that left the live session."""
        return self.sessions_dir / EARLIER_DIR_NAME / f"{self._storage_key(key)}.jsonl"

    def keep_earlier_messages(
        self, session: Session, messages: list[dict[str, Any]],
    ) -> Future[None] | None:
        """Keep messages that left the live session readable in the chat.

        Idle compaction, ``/compact`` and the file cap drop the oldest
        messages from the session the model replays; the CLI paints its
        history from that session, so they vanished from the screen. They are
        appended here, oldest first, for this conversation only (a ``/new``
        on the same key starts the file over).

        The write runs on one worker thread (appends stay in order and the
        event loop never waits for the disk); the returned future completes
        when the rows are on disk. Readers of this session wait for it.
        """
        rows = [_earlier_row(message) for message in messages if isinstance(message, dict)]
        if not rows:
            return None
        key = session.key
        stamp = session.metadata.get(CONVERSATION_STARTED_KEY)
        with self._earlier_guard:
            if self._earlier_executor is None:
                self._earlier_executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="session-earlier",
                )
            future = self._earlier_executor.submit(self._write_earlier_rows, key, stamp, rows)
            self._earlier_pending[key] = future
        return future

    def _wait_earlier_write(self, key: str) -> None:
        with self._earlier_guard:
            future = self._earlier_pending.get(key)
        if future is None:
            return
        # _write_earlier_rows logs its own failures and never raises.
        future.result()
        with self._earlier_guard:
            if self._earlier_pending.get(key) is future:
                del self._earlier_pending[key]

    def _write_earlier_rows(self, key: str, stamp: Any, rows: list[dict[str, Any]]) -> None:
        try:
            self._append_earlier_rows(key, stamp, rows)
        except (OSError, ValueError) as exc:
            # A display copy of messages the session already dropped: losing
            # it must not fail the turn or the compaction. ValueError covers a
            # file that does not decode as UTF-8 and a row that does not
            # serialise.
            logger.warning("Could not keep the earlier messages of {}: {}", key, exc)

    def _append_earlier_rows(self, key: str, stamp: Any, rows: list[dict[str, Any]]) -> None:
        path = self.earlier_messages_path(key)
        cache_key = str(path)
        if self._earlier_header_stamp(path) == (True, stamp):
            count = self._earlier_counts.get(cache_key)
            if count is None:
                count = self._count_earlier_rows(path)
            if count + len(rows) <= EARLIER_MAX_MESSAGES + _EARLIER_TRIM_SLACK:
                lines = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write(lines)
                self._pending_fsync.add(path)
                self._earlier_counts[cache_key] = count + len(rows)
                return
            rows = self._read_earlier_rows(path, key) + rows
        kept = rows[-EARLIER_MAX_MESSAGES:]
        ensure_dir(path.parent)
        header = {
            "_type": _EARLIER_HEADER_TYPE,
            "key": key,
            CONVERSATION_STARTED_KEY: stamp,
        }
        self._write_file(path, header, kept, fsync=False)
        self._earlier_counts[cache_key] = len(kept)

    def earlier_messages(self, session: Session) -> list[dict[str, Any]]:
        """Messages that left the live session, oldest first (display only).

        Blocking (waits for a pending write, then reads the file): call it
        from a worker thread.
        """
        self._wait_earlier_write(session.key)
        path = self.earlier_messages_path(session.key)
        stamp = session.metadata.get(CONVERSATION_STARTED_KEY)
        if self._earlier_header_stamp(path) != (True, stamp):
            return []
        return self._read_earlier_rows(path, session.key)

    @staticmethod
    def _earlier_header_stamp(path: Path) -> tuple[bool, Any]:
        """``(True, conversation stamp)`` of a readable file, else ``(False, None)``."""
        try:
            with open(path, encoding="utf-8") as handle:
                header = json.loads(handle.readline() or "null")
        except FileNotFoundError:
            return False, None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("Unreadable earlier-messages file {}: {}", path, exc)
            return False, None
        if not isinstance(header, dict) or header.get("_type") != _EARLIER_HEADER_TYPE:
            logger.warning("Earlier-messages file {} has no header; it starts over", path)
            return False, None
        return True, header.get(CONVERSATION_STARTED_KEY)

    @staticmethod
    def _count_earlier_rows(path: Path) -> int:
        with open(path, encoding="utf-8") as handle:
            next(handle, None)
            return sum(1 for line in handle if line.strip())

    @staticmethod
    def _read_earlier_rows(path: Path, key: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        skipped = 0
        try:
            with open(path, encoding="utf-8") as handle:
                next(handle, None)
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        # A line cut by a crash mid-append: the rest is fine.
                        skipped += 1
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
        except FileNotFoundError:
            return []
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Could not read the earlier messages of {}: {}", key, exc)
            return []
        if skipped:
            logger.warning("Skipped {} unreadable earlier message(s) of {}", skipped, key)
        return rows

    @staticmethod
    def _stored_key_for_path(path: Path) -> str | None:
        """Read the stored session key from a JSONL metadata row, if present."""
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("_type") == "metadata":
                        stored_key = data.get("key")
                        return stored_key if isinstance(stored_key, str) else None
                    return None
        except Exception:
            return None
        return None

    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.

        Args:
            key: Session key (usually channel:chat_id).

        Returns:
            The session.
        """
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.touch(key, cached)
            return cached

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        _mark_clean(session)
        self._cache.put(key, session)
        return session

    def peek(self, key: str) -> Session | None:
        """Return an existing session, or ``None`` when it was never created.

        Unlike :meth:`get_or_create` this never materialises a blank session,
        so read-only paths (hydrate on connect, status probes) cannot turn a
        throwaway chat id into a phantom chat in the sidebar.
        """
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.touch(key, cached)
            return cached
        session = self._load(key)
        if session is None:
            return None
        _mark_clean(session)
        self._cache.put(key, session)
        return session

    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        path = self._get_session_path(key)
        if not path.exists():
            fallback_paths = [
                (self._get_legacy_lossy_path(key), "legacy lossy path"),
                (self._get_legacy_session_path(key), "legacy path"),
            ]
            for fallback_path, description in fallback_paths:
                if not fallback_path.exists():
                    continue
                stored_key = self._stored_key_for_path(fallback_path)
                if stored_key and stored_key != key:
                    logger.info(
                        "Skipping migration for {} from {} because it belongs to {}",
                        key,
                        description,
                        stored_key,
                    )
                    continue
                try:
                    shutil.move(str(fallback_path), str(path))
                    logger.info("Migrated session {} from {}", key, description)
                except Exception:
                    logger.exception("Failed to migrate session {}", key)
                break

        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            updated_at = None
            last_consolidated = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                        updated_at = datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None
                        last_consolidated = data.get("last_consolidated", 0)
                    else:
                        messages.append(data)

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated
            )
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            repaired = self._repair(key)
            if repaired is not None:
                logger.info("Recovered session {} from corrupt file ({} messages)", key, len(repaired.messages))
            return repaired

    def _repair(self, key: str, *, path: Path | None = None) -> Session | None:
        """Attempt to recover a session from a corrupt JSONL file."""
        if path is None:
            path = self._get_session_path(key)
        if not path.exists():
            return None

        try:
            messages: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            created_at: datetime | None = None
            updated_at: datetime | None = None
            last_consolidated = 0
            skipped = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        skipped += 1
                        continue

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        if data.get("created_at"):
                            with suppress(ValueError, TypeError):
                                created_at = datetime.fromisoformat(data["created_at"])
                        if data.get("updated_at"):
                            with suppress(ValueError, TypeError):
                                updated_at = datetime.fromisoformat(data["updated_at"])
                        last_consolidated = data.get("last_consolidated", 0)
                    else:
                        messages.append(data)

            if skipped:
                logger.warning("Skipped {} corrupt lines in session {}", skipped, key)

            if not messages and not metadata:
                return None

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated
            )
        except Exception as e:
            logger.warning("Repair failed for session {}: {}", key, e)
            return None

    @staticmethod
    def _session_payload(session: Session) -> dict[str, Any]:
        return {
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
            "messages": session.messages,
        }

    def set_title(self, key: str, title: str) -> str:
        """Persist a human chat name. Later auto-titles will not overwrite it."""
        from navin.session.webui_turns import (
            WEBUI_TITLE_METADATA_KEY,
            WEBUI_TITLE_PROVISIONAL_METADATA_KEY,
            WEBUI_TITLE_USER_EDITED_METADATA_KEY,
        )

        cleaned = " ".join((title or "").split())
        if not cleaned:
            raise ValueError("title is empty")
        if len(cleaned) > 80:
            cleaned = cleaned[:79].rstrip() + "…"
        session = self.get_or_create(key)
        session.metadata[WEBUI_TITLE_METADATA_KEY] = cleaned
        session.metadata[WEBUI_TITLE_USER_EDITED_METADATA_KEY] = True
        session.metadata.pop(WEBUI_TITLE_PROVISIONAL_METADATA_KEY, None)
        session.updated_at = datetime.now()
        self.save(session)
        return cleaned

    def cached_sessions(self) -> list[Session]:
        """Sessions held in memory right now (what a restart could lose unsaved)."""
        return self._cache.values()

    def save(self, session: Session, *, fsync: bool = False) -> None:
        """Save a session to disk atomically.

        When *fsync* is ``True`` the final file and its parent directory are
        explicitly flushed to durable storage.  This is intentionally off by
        default (the OS page-cache is sufficient for normal operation) but
        should be enabled during graceful shutdown so that filesystems with
        write-back caching (e.g. rclone VFS, NFS, FUSE mounts) do not lose
        the most recent writes.
        """
        path = self._get_session_path(session.key)

        if not session.messages and not session.metadata and not path.exists():
            # Nothing to persist: no message, no metadata, no file yet. Writing
            # here would create a phantom "New chat" that only exists because
            # some read path touched the key.
            _mark_clean(session)
            self._cache.touch(session.key, session)
            return

        snapshot = self._save_snapshot(session, copy=False)
        self._write_snapshot(snapshot, fsync=fsync)
        self._cache.touch(session.key, session)

    def save_in_background(self, session: Session) -> Any:
        """Write the session from a worker thread; returns the asyncio task.

        For mid-turn checkpoints: rewriting a long transcript (20-40 ms of JSON
        for 500 messages) must not stall the event loop and every other chat
        on it. The state is copied now; a later save always wins on disk.
        """
        import asyncio

        if not session.messages and not session.metadata and not self._get_session_path(session.key).exists():
            return None
        snapshot = self._save_snapshot(session, copy=True)
        self._cache.touch(session.key, session)
        task = asyncio.get_running_loop().create_task(
            asyncio.to_thread(self._write_snapshot_logged, snapshot)
        )
        return task

    async def save_async(self, session: Session, *, fsync: bool = False) -> None:
        """Save from async code without blocking the event loop.

        The state is copied now (taking its place in the write order, like
        ``save``) and the JSON is written in a worker thread, so one long
        transcript does not stall every other session on the loop. Unlike a
        background checkpoint this waits for the write: when the thread fails
        (for example a message dict changed while it was serialised) the
        session is saved again synchronously, so the data is never dropped.
        A cancelled caller does not cancel the write: the thread finishes it.
        """
        import asyncio

        path = self._get_session_path(session.key)
        if not session.messages and not session.metadata and not path.exists():
            _mark_clean(session)
            self._cache.touch(session.key, session)
            return
        snapshot = self._save_snapshot(session, copy=True)
        self._cache.touch(session.key, session)
        try:
            await asyncio.to_thread(self._write_snapshot, snapshot, fsync=fsync)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "threaded save of session {} failed; saving on the loop",
                snapshot["key"],
                exc_info=True,
            )
            self.save(session, fsync=fsync)

    def _save_snapshot(self, session: Session, *, copy: bool) -> dict[str, Any]:
        key = session.key
        number = self._save_requested.get(key, 0) + 1
        self._save_requested[key] = number
        # The snapshot is what reaches the disk; until it does, the pending
        # save number keeps the session pinned in the cache.
        _mark_clean(session)
        return {
            "number": number,
            "key": key,
            "path": self._get_session_path(key),
            "metadata_line": {
                "_type": "metadata",
                "key": key,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "metadata": dict(session.metadata) if copy else session.metadata,
                "last_consolidated": session.last_consolidated,
            },
            "messages": list(session.messages) if copy else session.messages,
        }

    def _write_snapshot_logged(self, snapshot: dict[str, Any]) -> None:
        try:
            self._write_snapshot(snapshot, fsync=False)
        except Exception:
            # A checkpoint only serves crash recovery and the end of the turn
            # saves again: losing one is harmless, crashing the turn is not.
            logger.warning("background save of session {} failed", snapshot["key"], exc_info=True)

    def _write_snapshot(self, snapshot: dict[str, Any], *, fsync: bool) -> None:
        key = snapshot["key"]
        lock = self._save_locks.setdefault(key, threading.Lock())
        with lock:
            if snapshot["number"] <= self._save_written.get(key, 0):
                return  # a newer save already reached the disk
            self._write_file(snapshot["path"], snapshot["metadata_line"], snapshot["messages"], fsync=fsync)
            self._save_written[key] = snapshot["number"]

    def _write_file(
        self,
        path: Path,
        metadata_line: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        fsync: bool,
    ) -> None:
        tmp_path = path.with_suffix(".jsonl.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
                for msg in messages:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                if fsync:
                    f.flush()
                    os.fsync(f.fileno())

            os.replace(tmp_path, path)

            if fsync:
                # fsync the directory so the rename is durable.
                # On Windows, opening a directory with O_RDONLY raises
                # PermissionError - skip the dir sync there (NTFS
                # journals metadata synchronously).
                with suppress(PermissionError):
                    fd = os.open(str(path.parent), os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

        if fsync:
            self._pending_fsync.discard(path)
        else:
            self._pending_fsync.add(path)

    def flush_saved(self) -> int:
        """Make already saved history durable without rewriting cached chats."""
        flushed = 0
        directories: set[Path] = set()
        for path in list(self._pending_fsync):
            try:
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
                directories.add(path.parent)
                self._pending_fsync.discard(path)
                flushed += 1
            except FileNotFoundError:
                self._pending_fsync.discard(path)
            except OSError:
                logger.warning("Failed to sync session {}", path, exc_info=True)
        for directory in directories:
            with suppress(PermissionError):
                fd = os.open(str(directory), os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        return flushed

    def flush_all(self) -> int:
        """Re-save every cached session with fsync for durable shutdown.

        Returns the number of sessions flushed.  Errors on individual
        sessions are logged but do not prevent other sessions from being
        flushed.
        """
        flushed = 0
        for key, session in list(self._cache.items()):
            try:
                self.save(session, fsync=True)
                flushed += 1
            except Exception:
                logger.warning("Failed to flush session {}", key, exc_info=True)
        return flushed

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)

    def delete_session(self, key: str) -> bool:
        """Remove a session from disk (both workspace and legacy locations) and cache.

        Returns True if at least one JSONL file was found and unlinked.
        """
        paths = [
            self._get_session_path(key),
            self._get_legacy_lossy_path(key),
            self._get_legacy_session_path(key),
        ]
        self.invalidate(key)
        # A write still queued would bring the file back after the unlink.
        self._wait_earlier_write(key)
        earlier = self.earlier_messages_path(key)
        self._earlier_counts.pop(str(earlier), None)
        try:
            earlier.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Failed to delete earlier messages {}: {}", earlier, e)
        deleted = False
        for path in paths:
            if not path.exists():
                continue
            try:
                path.unlink()
                deleted = True
            except OSError as e:
                logger.warning("Failed to delete session file {}: {}", path, e)
        return deleted

    def fork_session_before_user_index(
        self,
        source_key: str,
        target_key: str,
        before_user_index: int,
    ) -> Session | None:
        """Create *target_key* from *source_key* before a global user-message index.

        ``before_user_index`` is zero-based over user messages in the full session:
        ``0`` means "before the first user message", ``1`` means "before the
        second user message", and so on. A value equal to or greater than the
        total user-message count copies the full session prefix. WebUI
        assistant-reply forks pass the next user index so the selected
        completed assistant turn is included.
        """
        if before_user_index < 0:
            return None
        source = self._cache.get(source_key) or self._load(source_key)
        if source is None:
            return None

        copied: list[dict[str, Any]] = []
        user_index = 0
        found_target = False
        for message in source.messages:
            if message.get("role") == "user":
                if user_index == before_user_index:
                    found_target = True
                    break
                user_index += 1
            copied.append(public_history_message(message))
        if user_index <= before_user_index:
            found_target = True
        if not found_target:
            return None

        metadata = deepcopy(source.metadata)
        for key in _FORK_VOLATILE_METADATA_KEYS:
            metadata.pop(key, None)

        last_consolidated = min(source.last_consolidated, len(copied))
        if source.last_consolidated > len(copied):
            metadata.pop("_last_summary", None)
            last_consolidated = 0

        now = datetime.now()
        target = Session(
            key=target_key,
            messages=copied,
            created_at=now,
            updated_at=now,
            metadata=metadata,
            last_consolidated=last_consolidated,
        )
        self.save(target, fsync=True)
        return target

    def read_session_file(self, key: str) -> dict[str, Any] | None:
        """Load a session from disk without caching; intended for read-only HTTP endpoints.

        Returns ``{"key", "created_at", "updated_at", "metadata", "messages"}`` or
        ``None`` when the session file does not exist or fails to parse.
        """
        path = self._get_session_path(key)
        if not path.exists():
            return None
        try:
            messages: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            created_at: str | None = None
            updated_at: str | None = None
            stored_key: str | None = None
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = data.get("created_at")
                        updated_at = data.get("updated_at")
                        stored_key = data.get("key")
                    else:
                        messages.append(data)
            return {
                "key": stored_key or key,
                "created_at": created_at,
                "updated_at": updated_at,
                "metadata": metadata,
                "messages": messages,
            }
        except Exception as e:
            logger.warning("Failed to read session {}: {}", key, e)
            repaired = self._repair(key)
            if repaired is not None:
                logger.info("Recovered read-only session view {} from corrupt file", key)
                return self._session_payload(repaired)
            return None

    def read_session_metadata(self, key: str) -> dict[str, Any] | None:
        """Load only the metadata record from a session file.

        This is used by WebUI routes that need session-level metadata but not the
        full conversation transcript. Many of them run on the engine loop, so a
        session already loaded answers from memory, and a file read once is
        reused while its size and modification time stay the same (5.6 s on
        the loop for one read in the Desktop engine log of 2026-10-26).
        """
        loaded = self._cache.get(key)
        if loaded is not None:
            return {
                "key": loaded.key,
                "created_at": loaded.created_at.isoformat(),
                "updated_at": loaded.updated_at.isoformat(),
                "metadata": dict(loaded.metadata) if isinstance(loaded.metadata, dict) else {},
            }
        path = self._get_session_path(key)
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        except OSError:
            # Not readable as a file right now: the full read reports and repairs.
            return self._read_session_metadata_file(key, path)
        stamp = (stat.st_mtime_ns, stat.st_size)
        hit = self._metadata_reads.get(str(path))
        if hit is not None and hit[0] == stamp:
            return _metadata_copy(hit[1])
        result = self._read_session_metadata_file(key, path)
        if len(self._metadata_reads) >= 512:
            self._metadata_reads.pop(next(iter(self._metadata_reads)))
        self._metadata_reads[str(path)] = (stamp, _metadata_copy(result))
        return result

    def _read_session_metadata_file(self, key: str, path: Path) -> dict[str, Any] | None:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("_type") != "metadata":
                        return None
                    metadata = data.get("metadata", {})
                    return {
                        "key": data.get("key") or key,
                        "created_at": data.get("created_at"),
                        "updated_at": data.get("updated_at"),
                        "metadata": metadata if isinstance(metadata, dict) else {},
                    }
            return None
        except Exception as e:
            logger.warning("Failed to read session metadata {}: {}", key, e)
            repaired = self._repair(key)
            if repaired is not None:
                logger.info("Recovered read-only session metadata {} from corrupt file", key)
                return {
                    "key": repaired.key,
                    "created_at": repaired.created_at.isoformat(),
                    "updated_at": repaired.updated_at.isoformat(),
                    "metadata": repaired.metadata,
                }
            return None

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.

        Returns:
            List of session info dicts.
        """
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            decoded = self._decode_storage_key(path.stem)
            fallback_key = decoded or path.stem.replace("_", ":", 1)
            try:
                # Read the metadata line and a small preview for session lists.
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            key = data.get("key") or fallback_key
                            metadata = data.get("metadata", {})
                            title = _metadata_title(metadata)
                            preview = ""
                            fallback_preview = ""
                            scanned_records = 0
                            scanned_chars = 0
                            for line in f:
                                if not line.strip():
                                    continue
                                scanned_records += 1
                                scanned_chars += len(line)
                                if (
                                    scanned_records > _SESSION_LIST_PREVIEW_MAX_RECORDS
                                    or scanned_chars > _SESSION_LIST_PREVIEW_MAX_CHARS
                                ):
                                    break
                                item = json.loads(line)
                                if item.get("_type") == "metadata":
                                    continue
                                text = _message_preview_text(item)
                                if not text:
                                    continue
                                if item.get("role") == "user":
                                    preview = text
                                    break
                                if not fallback_preview and item.get("role") == "assistant":
                                    fallback_preview = text
                            preview = preview or fallback_preview
                            fallback_time = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
                            sessions.append(
                                {
                                    "key": key,
                                    "created_at": data.get("created_at") or fallback_time,
                                    "updated_at": data.get("updated_at") or fallback_time,
                                    "title": title,
                                    "preview": preview,
                                    "path": str(path),
                                    "workspace_scope": _metadata_scope(metadata),
                                }
                            )
            except Exception:
                repaired = self._repair(fallback_key, path=path)
                if repaired is not None:
                    sessions.append(
                        {
                            "key": repaired.key,
                            "created_at": repaired.created_at.isoformat(),
                            "updated_at": repaired.updated_at.isoformat(),
                            "title": _metadata_title(repaired.metadata),
                            "preview": next(
                                (
                                    text
                                    for msg in repaired.messages
                                    if (text := _message_preview_text(msg))
                                ),
                                "",
                            ),
                            "path": str(path),
                            "workspace_scope": _metadata_scope(repaired.metadata),
                        }
                    )
                continue
        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
