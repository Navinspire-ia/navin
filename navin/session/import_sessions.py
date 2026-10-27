"""Import chat sessions from external CLI coding agents into Navin.

Sources parsed today:
- claude-code: ~/.claude/projects/<encoded-path>/<uuid>.jsonl
- codex: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
- opencode: ~/.local/share/opencode/storage/{session,message,part}

Sources detected but not converted in this version (their data is reported
with a clear note instead of being silently ignored):
- oh-my-pi: ~/.omp (and ~/.oh-my-pi)
- cursor: workspaceStorage state.vscdb databases

Converted sessions are written with the standard SessionManager format so
they show up in both the CLI and the desktop session lists of the target
workspace. Import is idempotent: a session key ``import:<source>:<id>``
already present in the workspace is skipped unless ``overwrite`` is set.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from loguru import logger

from navin.session.manager import Session, SessionManager

_MAX_TITLE_CHARS = 80

# Codex prepends injected instruction blocks to user turns; they are runtime
# context, not conversation, and would pollute titles and previews.
_CODEX_INJECTED_PREFIXES = (
    "<permissions instructions>",
    "<environment_context>",
    "<user_instructions>",
    "<env>",
    "#agents.md instructions for",
    "# agents.md instructions for",
    "#agents.md",
    "# agents.md",
    "#claude.md",
    "# claude.md",
    "<ENVIRONMENT",
)

# Claude Code prepends runtime context to user turns (slash-command
# envelopes, continuation summaries, project instruction files like
# AGENTS.md / CLAUDE.md). They are not conversation and would produce
# sidebar titles like "#Agents.md instructions for /home/...". Kept out
# of both messages and titles.
_CLAUDE_CODE_INJECTED_PREFIXES = (
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "caveat: the messages below",
    "[request interrupted",
    "this session is being continued from a previous conversation",
    "#agents.md instructions for",
    "# agents.md instructions for",
    "#agents.md",
    "# agents.md",
    "#claude.md",
    "# claude.md",
    "# instructions from",
)


def _utc_now_iso() -> str:
    # Naive local time only: Navin's autocompact does
    # datetime.now() - session.updated_at with a naive now(), so any
    # tz-aware stored timestamp crashes the gateway at startup.
    return datetime.now().isoformat()


def _to_naive_local(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def _epoch_ms_to_iso(value: Any) -> str | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return _to_naive_local(
        datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    ).isoformat()


def _rfc3339_to_iso(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _to_naive_local(parsed).isoformat()


# Wrappers tools put around pasted or injected text: a title is the user's
# words, not "<pasted_content id=...>" or a system reminder.
_TITLE_NOISE = re.compile(
    r"<(pasted_content|system-reminder|command-name|command-message|command-args|"
    r"local-command-stdout|local-command-caveat|environment_context|user_instructions|"
    r"ide_selection|ide_opened_file)\b[^>]*>.*?</\1>|<[^>]{1,80}>",
    re.DOTALL | re.IGNORECASE,
)


def _title_from_text(text: str) -> str:
    """One-line, bounded title taken from the first user message."""
    cleaned = _TITLE_NOISE.sub(" ", text)
    from navin.session.webui_turns import limit_title_words

    line = limit_title_words(" ".join((cleaned if cleaned.strip() else text).split()))
    if len(line) > _MAX_TITLE_CHARS:
        line = line[: _MAX_TITLE_CHARS - 1].rstrip() + "..."
    return line


_MIN_TITLE_LEN = 4


def _title_from_messages(messages: list[dict[str, Any]]) -> str:
    """Best-effort title: first substantial user turn, else first assistant turn.

    Some transcripts start with throwaway keystrokes (".", ".exit"); those
    make useless sidebar titles, so skip them when a real turn exists.
    """
    for msg in messages:
        if msg.get("role") != "user":
            continue
        text = _TITLE_NOISE.sub(" ", str(msg.get("content", ""))).strip()
        if (
            len(text) >= _MIN_TITLE_LEN
            and not text.startswith(".")
            and any(c.isalnum() for c in text)
        ):
            return _title_from_text(text)
    for msg in messages:
        if msg.get("role") == "assistant":
            return _title_from_text(str(msg.get("content", "")))
    return ""


@dataclass
class ExternalSession:
    """A conversation transcript recovered from an external tool."""

    source: str
    session_id: str
    title: str
    messages: list[dict[str, Any]]
    origin_path: Path
    created_at: datetime | None = None
    updated_at: datetime | None = None
    origin_cwd: str | None = None


@dataclass
class SourceReport:
    """Discovery result for one external source."""

    name: str
    label: str
    root: Path | None
    status: str  # ready | missing | unsupported
    sessions: list[ExternalSession] = field(default_factory=list)
    note: str = ""
    # Set by a count-only discovery (sessions stays empty then).
    count: int | None = None


@dataclass
class SourceImportStats:
    name: str
    label: str
    status: str
    discovered: int = 0
    imported: int = 0
    skipped_existing: int = 0
    note: str = ""


def _session_key(source: str, session_id: str) -> str:
    # "websocket:" prefix is required: the WebUI sidebar only surfaces
    # channel sessions whose key starts with it (ws_http.py
    # _is_websocket_channel_session_key). Plain "import:" keys are
    # invisible in the desktop chat list.
    return f"websocket:import-{source}-{session_id}"


def _message(role: str, text: str, timestamp: str | None) -> dict[str, Any] | None:
    cleaned = text.strip()
    if not cleaned:
        return None
    msg: dict[str, Any] = {"role": role, "content": cleaned}
    if timestamp:
        msg["timestamp"] = timestamp
    return msg


def _bounds(timestamps: Iterable[str]) -> tuple[datetime | None, datetime | None]:
    values = sorted(ts for ts in timestamps if ts)
    if not values:
        return None, None
    try:
        first = datetime.fromisoformat(values[0])
        last = datetime.fromisoformat(values[-1])
    except ValueError:
        return None, None
    return first, last


def _parse_claude_code(root: Path) -> list[ExternalSession]:
    """Parse ~/.claude/projects/<encoded>/<uuid>.jsonl transcripts."""
    sessions: list[ExternalSession] = []
    if not root.is_dir():
        return sessions
    for path in sorted(root.glob("*/*.jsonl")):
        messages: list[dict[str, Any]] = []
        timestamps: list[str] = []
        # One transcript per file: the id stays the file stem. Never take
        # entry["sessionId"] - resumed/continued transcripts carry the id
        # of the session they were forked from, which made two distinct
        # sessions collapse into one key (dedup then dropped one).
        session_id = path.stem
        summary_title = ""
        origin_cwd: str | None = None
        try:
            with open(path, encoding="utf-8") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(entry, dict):
                        continue
                    kind = entry.get("type")
                    if kind == "summary" and not summary_title:
                        summary = entry.get("summary")
                        if isinstance(summary, str) and summary.strip():
                            summary_title = summary
                        continue
                    if kind not in {"user", "assistant"}:
                        continue
                    if entry.get("isSidechain") is True or entry.get("isMeta") is True:
                        continue
                    if isinstance(entry.get("cwd"), str) and entry["cwd"]:
                        origin_cwd = entry["cwd"]
                    payload = entry.get("message")
                    if not isinstance(payload, dict):
                        continue
                    role = payload.get("role")
                    if role not in {"user", "assistant"}:
                        continue
                    content = payload.get("content")
                    ts = _rfc3339_to_iso(entry.get("timestamp"))
                    if ts:
                        timestamps.append(ts)
                    text_parts: list[str] = []
                    has_tool_result = False
                    if isinstance(content, str):
                        text_parts.append(content)
                    elif isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            block_type = block.get("type")
                            if block_type == "tool_result":
                                # A tool-output turn, not something the user said.
                                has_tool_result = True
                            if block_type == "text" and isinstance(block.get("text"), str):
                                text_parts.append(block["text"])
                    if has_tool_result:
                        continue
                    text = "\n".join(text_parts)
                    if role == "user" and text.lstrip().lower().startswith(
                        _CLAUDE_CODE_INJECTED_PREFIXES
                    ):
                        continue
                    msg = _message(role, text, ts)
                    if msg:
                        messages.append(msg)
        except OSError:
            continue
        if not messages:
            continue
        created, updated = _bounds(timestamps)
        sessions.append(
            ExternalSession(
                source="claude-code",
                session_id=session_id,
                title=summary_title or _title_from_messages(messages),
                messages=messages,
                origin_path=path,
                created_at=created,
                updated_at=updated,
                origin_cwd=origin_cwd,
            )
        )
    return sessions


def _parse_codex(root: Path) -> list[ExternalSession]:
    """Parse ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl transcripts."""
    sessions: list[ExternalSession] = []
    if not root.is_dir():
        return sessions
    for path in sorted(root.glob("*/*/*/rollout-*.jsonl")):
        messages: list[dict[str, Any]] = []
        timestamps: list[str] = []
        session_id = path.stem
        origin_cwd: str | None = None
        try:
            with open(path, encoding="utf-8") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(entry, dict):
                        continue
                    kind = entry.get("type")
                    payload = entry.get("payload")
                    if kind == "session_meta" and isinstance(payload, dict):
                        sid = payload.get("id")
                        if isinstance(sid, str) and sid:
                            session_id = sid
                        cwd = payload.get("cwd")
                        if isinstance(cwd, str) and cwd:
                            origin_cwd = cwd
                        continue
                    if kind != "response_item" or not isinstance(payload, dict):
                        continue
                    if payload.get("type") != "message":
                        continue
                    role = payload.get("role")
                    if role not in {"user", "assistant"}:
                        continue
                    content = payload.get("content")
                    text_parts: list[str] = []
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            text = block.get("text")
                            if isinstance(text, str):
                                text_parts.append(text)
                    text = "\n".join(text_parts)
                    if role == "user" and text.lstrip().lower().startswith(_CODEX_INJECTED_PREFIXES):
                        continue
                    ts = _rfc3339_to_iso(entry.get("timestamp"))
                    if ts:
                        timestamps.append(ts)
                    msg = _message(role, text, ts)
                    if msg:
                        messages.append(msg)
        except OSError:
            continue
        if not messages:
            continue
        created, updated = _bounds(timestamps)
        sessions.append(
            ExternalSession(
                source="codex",
                session_id=session_id,
                title=_title_from_messages(messages),
                messages=messages,
                origin_path=path,
                created_at=created,
                updated_at=updated,
                origin_cwd=origin_cwd,
            )
        )
    return sessions


def _parse_opencode(root: Path) -> list[ExternalSession]:
    """Parse OpenCode storage trees (sessions, messages, parts).

    Layout: storage/session/<project>/<ses>.json carries the session
    metadata; storage/message/<ses>/<msg>.json the ordered turns; and
    storage/part/<msg>/prt_*.json the text pieces of each turn.
    """
    sessions: list[ExternalSession] = []
    if not root.is_dir():
        return sessions
    for path in sorted(root.glob("session/*/ses_*.json")):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(meta, dict):
            continue
        ses_id = meta.get("id")
        if not isinstance(ses_id, str) or not ses_id:
            continue
        title = meta.get("title")
        if not isinstance(title, str) or not title.strip():
            summary = meta.get("summary")
            title = (
                summary.get("title", "")
                if isinstance(summary, dict)
                else ""
            )
            if not isinstance(title, str):
                title = str(meta.get("slug") or ses_id)
        times = meta.get("time") or {}
        created_iso = _epoch_ms_to_iso(times.get("created")) if isinstance(times, dict) else None
        updated_iso = _epoch_ms_to_iso(times.get("updated")) if isinstance(times, dict) else None

        message_dir = root / "message" / ses_id
        part_dir = root / "part"
        messages: list[dict[str, Any]] = []
        if message_dir.is_dir():
            entries = []
            for mpath in sorted(message_dir.glob("msg_*.json")):
                try:
                    mdata = json.loads(mpath.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(mdata, dict):
                    continue
                entries.append((mpath, mdata))
            entries.sort(
                key=lambda pair: (
                    (pair[1].get("time") or {}).get("created", 0)
                    if isinstance(pair[1].get("time"), dict)
                    else 0,
                    pair[0].name,
                )
            )
            for mpath, mdata in entries:
                role = mdata.get("role")
                if role not in {"user", "assistant"}:
                    continue
                msg_id = mdata.get("id")
                if not isinstance(msg_id, str) or not msg_id:
                    continue
                mtime = mdata.get("time")
                msg_ts = (
                    _epoch_ms_to_iso(mtime.get("created"))
                    if isinstance(mtime, dict)
                    else None
                )
                text_parts: list[str] = []
                for ppath in sorted((part_dir / msg_id).glob("prt_*.json")):
                    try:
                        pdata = json.loads(ppath.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    if not isinstance(pdata, dict):
                        continue
                    if pdata.get("type") == "text" and isinstance(pdata.get("text"), str):
                        text_parts.append(pdata["text"])
                msg = _message(role, "\n".join(text_parts), msg_ts)
                if msg:
                    messages.append(msg)
        if not messages:
            continue
        sessions.append(
            ExternalSession(
                source="opencode",
                session_id=ses_id,
                title=title.strip(),
                messages=messages,
                origin_path=path,
                created_at=(
                    datetime.fromisoformat(created_iso) if created_iso else None
                ),
                updated_at=(
                    datetime.fromisoformat(updated_iso) if updated_iso else None
                ),
                origin_cwd=(
                    meta["directory"]
                    if isinstance(meta.get("directory"), str) and meta["directory"].strip()
                    else None
                ),
            )
        )
    return sessions


def _omp_timestamp(value: Any) -> str | None:
    """OMP events carry either an epoch-ms number or an ISO string."""
    if isinstance(value, (int, float)):
        return _epoch_ms_to_iso(value)
    return _rfc3339_to_iso(value) if isinstance(value, str) else None


def _omp_content_to_text(content: Any) -> str:
    """Flatten an OMP message content (string or parts list) to text."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for piece in content:
        if isinstance(piece, str):
            parts.append(piece)
        elif isinstance(piece, dict):
            if piece.get("type") == "text" and isinstance(piece.get("text"), str):
                parts.append(piece["text"])
            elif piece.get("type") in {"toolCall", "tool_call", "toolUse"}:
                name = piece.get("name") or piece.get("toolName") or "tool"
                parts.append(f"[tool call: {name}]")
            else:
                parts.append(json.dumps(piece, ensure_ascii=False))
    return "\n".join(p for p in parts if p).strip()


def _parse_oh_my_pi(root: Path) -> list[ExternalSession]:
    """Parse oh-my-pi session transcripts.

    Layout: <root>/agent/sessions/<cwd-slug>/<iso>_<sid>.jsonl (older
    versions used <root>/sessions/...). Each line is an event; the ones
    that matter are ``session`` (id, cwd, timestamp), ``title`` /
    ``title_change`` and ``message`` (message.role user/assistant/tool
    with string or parts content).
    """
    sessions: list[ExternalSession] = []
    search_roots = [root / "agent" / "sessions", root / "sessions"]
    for base in search_roots:
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*/*.jsonl")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            session_id = ""
            origin_cwd: str | None = None
            title = ""
            messages: list[dict[str, Any]] = []
            for raw in lines:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ev, dict):
                    continue
                kind = ev.get("type")
                if kind in {"title", "title_change"}:
                    candidate = ev.get("title")
                    if isinstance(candidate, str) and candidate.strip():
                        title = candidate.strip()
                elif kind == "session":
                    if isinstance(ev.get("id"), str):
                        session_id = ev["id"]
                    if isinstance(ev.get("cwd"), str):
                        origin_cwd = ev["cwd"]
                    if not title and isinstance(ev.get("title"), str):
                        title = ev["title"]
                elif kind == "message":
                    msg = ev.get("message")
                    if not isinstance(msg, dict):
                        continue
                    role = msg.get("role")
                    if role not in {"user", "assistant", "tool", "system"}:
                        continue
                    text = _omp_content_to_text(msg.get("content"))
                    if role == "assistant" and not text:
                        continue
                    stamp = _omp_timestamp(ev.get("timestamp")) or _omp_timestamp(
                        msg.get("timestamp")
                    )
                    entry = _message(role, text, stamp)
                    if entry:
                        messages.append(entry)
            if not session_id:
                session_id = path.stem.split("_", 1)[-1] or path.stem
            if not messages:
                continue
            stamps = [str(m["timestamp"]) for m in messages if m.get("timestamp")]
            created_at, updated_at = _bounds(stamps)
            sessions.append(
                ExternalSession(
                    source="oh-my-pi",
                    session_id=session_id,
                    title=title.strip() or _title_from_messages(messages),
                    messages=messages,
                    origin_path=path,
                    created_at=created_at,
                    updated_at=updated_at,
                    origin_cwd=origin_cwd,
                )
            )
    return sessions


def _cursor_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        text = value.get("text") or value.get("content")
        if isinstance(text, str):
            return text.strip()
        return json.dumps(value, ensure_ascii=False)
    return ""


def _cursor_messages(conversation: Any) -> list[dict[str, Any]]:
    """Flatten a Cursor conversation list into Navin messages.

    Bubbles carry type 1 (user) or 2 (assistant); timestamps are rare,
    so messages usually stay undated and the session bounds come from
    the composer createdAt.
    """
    messages: list[dict[str, Any]] = []
    if not isinstance(conversation, list):
        return messages
    for bubble in conversation:
        if not isinstance(bubble, dict):
            continue
        role = {1: "user", 2: "assistant"}.get(bubble.get("type"))
        if role is None:
            continue
        entry = _message(role, _cursor_text(bubble.get("text")), None)
        if entry:
            messages.append(entry)
    return messages


def _cursor_composer_sessions(
    db_path: Path,
    rows: list[tuple[str, str]],
    origin_cwd: str | None,
) -> list[ExternalSession]:
    sessions: list[ExternalSession] = []
    for key, raw in rows:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        composer_id = str(data.get("composerId") or key.split(":", 1)[-1])
        messages = _cursor_messages(data.get("conversation"))
        if not messages:
            continue
        created_at = _epoch_ms_to_iso(data.get("createdAt"))
        title = str(data.get("name") or "").strip() or _title_from_messages(messages)
        sessions.append(
            ExternalSession(
                source="cursor",
                session_id=composer_id,
                title=title,
                messages=messages,
                origin_path=db_path,
                created_at=(
                    datetime.fromisoformat(created_at) if created_at else None
                ),
                updated_at=(
                    datetime.fromisoformat(created_at) if created_at else None
                ),
                origin_cwd=origin_cwd,
            )
        )
    return sessions


def _cursor_workspace_folder(conn: Any) -> str | None:
    """Best-effort workspace path from recentlyOpenedPathsList."""
    try:
        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key = ?",
            ("history.recentlyOpenedPathsList",),
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    try:
        data = json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        uri = entry.get("folderUri") or ""
        if isinstance(uri, str) and uri.startswith("file://"):
            return uri[len("file://"):]
    return None


def _cursor_uri_path(identifier: Any) -> str | None:
    """Project folder from a composer's workspaceIdentifier (local or WSL remote)."""
    uri = identifier.get("uri") if isinstance(identifier, dict) else None
    if not isinstance(uri, dict):
        return None
    path = uri.get("path") or uri.get("fsPath")
    return path if isinstance(path, str) and path.strip() else None


# Cursor's databases belong to another program, often still running (on the
# Windows side under WSL) and sometimes huge: a real globalStorage/state.vscdb
# weighs over 100 GB, read through /mnt/c. They are opened read-only and
# immutable (no lock taken, no -wal/-shm touched, so a live Cursor can never
# block us), with a short busy timeout, and every scan of one Cursor root
# runs under a wall-clock budget enforced by a SQLite progress handler: a
# query past the deadline is interrupted instead of grinding on.
_CURSOR_SCAN_BUDGET_S = 180.0
_CURSOR_COUNT_BUDGET_S = 30.0
_SQLITE_BUSY_TIMEOUT_S = 2.0


def _foreign_sqlite_uri(db_path: Path) -> str:
    # Only the characters that end or escape a URI path are encoded; the
    # path itself stays native (a Windows UNC share keeps its backslashes).
    raw = str(db_path).replace("%", "%25").replace("?", "%3F").replace("#", "%23")
    return f"file:{raw}?mode=ro&immutable=1"


def _open_foreign_sqlite(db_path: Path, deadline: float | None = None) -> Any:
    """Open another program's SQLite file read-only, never waiting on its locks.

    With *deadline* (a ``time.monotonic()`` value), any statement still
    running past it fails with ``sqlite3.OperationalError`` (interrupted).
    """
    import sqlite3

    conn = sqlite3.connect(
        _foreign_sqlite_uri(db_path), uri=True, timeout=_SQLITE_BUSY_TIMEOUT_S
    )
    if deadline is not None:
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    return conn


def _parse_cursor_global(db_path: Path, deadline: float | None = None) -> list[ExternalSession]:
    """Parse the composer conversations Cursor keeps in globalStorage.

    Current Cursor writes every chat to globalStorage/state.vscdb, table
    cursorDiskKV: ``composerData:<id>`` (title, project, ordered message
    ids) and ``bubbleId:<id>:<bubble>`` (each message, type 1 user / 2
    assistant). The workspaceStorage databases the older parser reads are
    empty there, so a machine with hundreds of Cursor chats imported none.
    The file can weigh tens of GB: it is opened read-only in place, and each
    conversation's messages come back in one range query.
    """
    import sqlite3

    sessions: list[ExternalSession] = []
    if not db_path.is_file():
        return sessions
    try:
        conn = _open_foreign_sqlite(db_path, deadline)
    except sqlite3.Error:
        return sessions
    try:
        try:
            heads = conn.execute(
                "SELECT composerId, createdAt, lastUpdatedAt FROM composerHeaders "
                "WHERE COALESCE(isSubagent, 0) = 0"
            ).fetchall()
        except sqlite3.Error:
            return sessions
        for composer_id, created, updated in heads:
            if deadline is not None and time.monotonic() > deadline:
                logger.warning(
                    "session import: Cursor history scan stopped after its time budget "
                    "({} of {} conversations read)", len(sessions), len(heads)
                )
                break
            try:
                row = conn.execute(
                    "SELECT value FROM cursorDiskKV WHERE key = ?", (f"composerData:{composer_id}",)
                ).fetchone()
            except sqlite3.Error:
                break
            if not row:
                continue
            try:
                data = json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            order = [
                (head.get("bubbleId"), head.get("type"))
                for head in data.get("fullConversationHeadersOnly") or []
                if isinstance(head, dict) and head.get("bubbleId")
            ]
            texts: dict[str, str] = {}
            try:
                bubbles = conn.execute(
                    "SELECT key, value FROM cursorDiskKV WHERE key >= ? AND key < ?",
                    (f"bubbleId:{composer_id}:", f"bubbleId:{composer_id};"),
                ).fetchall()
            except sqlite3.Error:
                break
            for key, value in bubbles:
                try:
                    bubble = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(bubble, dict) and isinstance(bubble.get("text"), str):
                    texts[str(key).rsplit(":", 1)[-1]] = bubble["text"]
            stamp_created = _epoch_ms_to_iso(created or data.get("createdAt"))
            messages: list[dict[str, Any]] = []
            for bubble_id, kind in order:
                text = texts.get(str(bubble_id), "")
                message = _message("user" if kind == 1 else "assistant", text, None)
                if message:
                    messages.append(message)
            # Older composers kept the conversation inline.
            if not messages and isinstance(data.get("conversation"), list):
                messages = _cursor_messages(data["conversation"])
            if not messages:
                continue
            title = str(data.get("name") or "").strip() or _title_from_messages(messages)
            sessions.append(
                ExternalSession(
                    source="cursor",
                    session_id=str(composer_id),
                    title=title,
                    messages=messages,
                    origin_path=db_path,
                    created_at=datetime.fromisoformat(stamp_created) if stamp_created else None,
                    updated_at=(
                        datetime.fromisoformat(iso)
                        if (iso := _epoch_ms_to_iso(updated)) else None
                    ),
                    origin_cwd=_cursor_uri_path(data.get("workspaceIdentifier")),
                )
            )
    finally:
        conn.close()
    return sessions


def _parse_cursor(root: Path) -> list[ExternalSession]:
    """Parse Cursor chat history: the global composer store, then workspaceStorage.

    Formats, newest first: globalStorage cursorDiskKV composers (see
    _parse_cursor_global), per-workspace ``composerData:<id>`` keys, and the
    legacy ``workbench.panel.aichat.view.aichat.chatdata`` blob.
    """
    import sqlite3

    sessions: list[ExternalSession] = []
    if not root.is_dir():
        return sessions
    deadline = time.monotonic() + _CURSOR_SCAN_BUDGET_S
    sessions.extend(_parse_cursor_global(root.parent / "globalStorage" / "state.vscdb", deadline))
    for db_path in sorted(root.glob("*/state.vscdb")):
        if time.monotonic() > deadline:
            logger.warning("session import: Cursor workspace scan stopped after its time budget")
            break
        try:
            conn = _open_foreign_sqlite(db_path, deadline)
        except sqlite3.Error:
            continue
        try:
            origin_cwd = _cursor_workspace_folder(conn)
            try:
                rows = conn.execute(
                    "SELECT key, value FROM ItemTable WHERE key LIKE ?",
                    ("composerData:%",),
                ).fetchall()
            except sqlite3.Error:
                rows = []
            if rows:
                sessions.extend(
                    _cursor_composer_sessions(db_path, rows, origin_cwd)
                )
            # Legacy single-blob format (used when no composer rows
            # exist in this database).
            try:
                row = conn.execute(
                    "SELECT value FROM ItemTable WHERE key = ?",
                    ("workbench.panel.aichat.view.aichat.chatdata",),
                ).fetchone()
            except sqlite3.Error:
                row = None
            if not row:
                continue
            try:
                data = json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                continue
            chats = data.get("chats") if isinstance(data, dict) else None
            if not isinstance(chats, list):
                continue
            for index, chat in enumerate(chats):
                if not isinstance(chat, dict):
                    continue
                messages = _cursor_messages(chat.get("messages"))
                if not messages:
                    continue
                title = (
                    str(chat.get("chatTitle") or "").strip()
                    or _title_from_messages(messages)
                )
                sessions.append(
                    ExternalSession(
                        source="cursor",
                        session_id=f"{db_path.parent.name}-chat{index}",
                        title=title,
                        messages=messages,
                        origin_path=db_path,
                        origin_cwd=origin_cwd,
                    )
                )
        finally:
            conn.close()
    return sessions


@dataclass(frozen=True)
class _SourceSpec:
    name: str
    label: str
    default_root: Path
    parse: Callable[[Path], list[ExternalSession]] | None
    extra_roots: tuple[Path, ...] = ()


def _windows_homes() -> list[Path]:
    """Windows user folders reachable from WSL (/mnt/c/Users/<name>).

    A WSL user very often runs Cursor, Claude Code or Codex on the Windows
    side: their history lives under the Windows profile, not ~ in Linux.
    """
    if os.name == "nt" or not (
        os.environ.get("WSL_DISTRO_NAME") or "microsoft" in _kernel_release().lower()
    ):
        return []
    # Only for the account's real home: a relocated home (a sandbox, a test,
    # another profile) must not reach into the Windows user's data.
    try:
        import pwd

        if Path(pwd.getpwuid(os.getuid()).pw_dir).resolve() != Path.home().resolve():
            return []
    except (ImportError, KeyError, OSError):
        return []
    users = Path("/mnt/c/Users")
    skip = {"public", "default", "default user", "all users", "wsiaccount", "defaultapppool"}
    try:
        return sorted(p for p in users.iterdir() if p.is_dir() and p.name.lower() not in skip)
    except OSError:
        return []


def _wsl_homes() -> list[Path]:
    """Linux home folders of the WSL distributions, seen from Windows.

    The other way round from _windows_homes: Navin runs on Windows while
    Claude Code, Codex or OpenCode run inside WSL (Ubuntu...), so their history
    lives under the distribution share (wsl.localhost, distro, home, user). Distributions come
    from the registry, where WSL lists them.
    """
    if os.name != "nt":
        return []
    try:
        import winreg

        names: list[str] = []
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Lxss") as lxss:
            index = 0
            while True:
                try:
                    guid = winreg.EnumKey(lxss, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(lxss, guid) as entry:
                        name, _ = winreg.QueryValueEx(entry, "DistributionName")
                        if name and not str(name).lower().startswith("docker-desktop"):
                            names.append(str(name))
                except OSError:
                    continue
    except (ImportError, OSError):
        return []
    homes: list[Path] = []
    for name in names:
        for share in ("wsl.localhost", "wsl$"):
            root = Path(f"\\\\{share}\\{name}")
            try:
                users = [p for p in (root / "home").iterdir() if p.is_dir()]
            except OSError:
                continue
            homes.extend(sorted(users))
            if (root / "root").is_dir():
                homes.append(root / "root")
            break
    return homes


def _kernel_release() -> str:
    try:
        return Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8")
    except OSError:
        return ""


def _source_specs() -> list[_SourceSpec]:
    home = Path.home()
    # The other side of the machine: Windows profiles from WSL, WSL homes from Windows.
    # Best effort: a registry or share error here must not hide the local sources.
    try:
        win_homes = _windows_homes() + _wsl_homes()
    except Exception:  # noqa: BLE001
        logger.exception("session import: could not list the other side of this machine")
        win_homes = []
    # Cursor stores its workspaceStorage under platform-specific locations:
    # Linux ~/.config/Cursor, macOS ~/Library/Application Support/Cursor,
    # Windows %APPDATA%/Cursor. All candidates are probed on every platform
    # so a synced/mounted folder from another OS is still detected.
    cursor_roots = (
        home / ".cursor" / "User" / "workspaceStorage",
        home / "Library" / "Application Support" / "Cursor" / "User"
        / "workspaceStorage",
        home / "AppData" / "Roaming" / "Cursor" / "User" / "workspaceStorage",
        *(win / "AppData" / "Roaming" / "Cursor" / "User" / "workspaceStorage" for win in win_homes),
    )
    return [
        _SourceSpec(
            "claude-code",
            "Claude Code",
            home / ".claude" / "projects",
            _parse_claude_code,
            extra_roots=tuple(win / ".claude" / "projects" for win in win_homes),
        ),
        _SourceSpec(
            "codex",
            "Codex",
            home / ".codex" / "sessions",
            _parse_codex,
            extra_roots=tuple(win / ".codex" / "sessions" for win in win_homes),
        ),
        _SourceSpec(
            "opencode",
            "OpenCode",
            home / ".local" / "share" / "opencode" / "storage",
            _parse_opencode,
            extra_roots=tuple(win / ".local" / "share" / "opencode" / "storage" for win in win_homes),
        ),
        _SourceSpec(
            "oh-my-pi",
            "oh-my-pi",
            home / ".omp",
            _parse_oh_my_pi,
            extra_roots=(home / ".oh-my-pi", *(win / ".omp" for win in win_homes)),
        ),
        _SourceSpec(
            "cursor",
            "Cursor",
            home / ".config" / "Cursor" / "User" / "workspaceStorage",
            _parse_cursor,
            extra_roots=cursor_roots,
        ),
    ]


PARSEABLE_SOURCES = ("claude-code", "codex", "opencode", "oh-my-pi", "cursor")
DETECTED_ONLY_SOURCES: tuple[str, ...] = ()
ALL_SOURCES = PARSEABLE_SOURCES + DETECTED_ONLY_SOURCES


def resolve_sources(source: str) -> tuple[str, ...]:
    """Map the CLI ``--source`` value to a tuple of source names.

    Accepts ``auto``/``all``, a single name, or a comma-separated list
    (``claude-code,codex``) so callers can import a subset of sources.
    """
    value = (source or "auto").strip().lower()
    if value in {"auto", "all"}:
        return PARSEABLE_SOURCES + DETECTED_ONLY_SOURCES
    known = {spec.name for spec in _source_specs()}
    if "," in value:
        names = tuple(
            dict.fromkeys(part.strip() for part in value.split(",") if part.strip())
        )
        unknown = [name for name in names if name not in known]
        if unknown:
            raise ValueError(
                f"Unknown source(s) {', '.join(unknown)}. Expected one of: auto, "
                + ", ".join(sorted(known))
            )
        if not names:
            raise ValueError("No source selected.")
        return names
    if value not in known:
        raise ValueError(
            f"Unknown source '{source}'. Expected one of: auto, "
            + ", ".join(sorted(known))
        )
    return (value,)


_EXTRA_ROOTS_FILE = Path.home() / ".navin" / "session_import_roots.json"


def load_extra_roots() -> dict[str, list[str]]:
    """User-saved roots per source (mounted/synced dirs from other machines)."""
    try:
        data = json.loads(_EXTRA_ROOTS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for name, roots in data.items():
        if isinstance(roots, list):
            out[str(name)] = [str(p) for p in roots if isinstance(p, str)]
    return out


def save_extra_root(source: str, path: str) -> dict[str, list[str]]:
    """Persist one extra root for *source*; returns the updated map."""
    if source not in ALL_SOURCES:
        raise ValueError(f"Unknown source '{source}'")
    clean = str(Path(path).expanduser())
    if not clean:
        raise ValueError("path must not be empty")
    roots = load_extra_roots()
    current = roots.setdefault(source, [])
    if clean not in current:
        current.append(clean)
    _EXTRA_ROOTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _EXTRA_ROOTS_FILE.write_text(
        json.dumps(roots, indent=2), encoding="utf-8"
    )
    return roots


def remove_extra_root(source: str, path: str) -> dict[str, list[str]]:
    """Drop one saved root; returns the updated map."""
    roots = load_extra_roots()
    current = roots.get(source)
    if current and path in current:
        current.remove(path)
        if not current:
            roots.pop(source, None)
    _EXTRA_ROOTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _EXTRA_ROOTS_FILE.write_text(
        json.dumps(roots, indent=2), encoding="utf-8"
    )
    return roots


def _cursor_ids(root: Path) -> set[str]:
    """Conversation ids Cursor indexes, without reading any conversation."""
    import sqlite3

    ids: set[str] = set()
    deadline = time.monotonic() + _CURSOR_COUNT_BUDGET_S
    global_db = root.parent / "globalStorage" / "state.vscdb"
    if global_db.is_file():
        try:
            conn = _open_foreign_sqlite(global_db, deadline)
            try:
                ids.update(
                    str(row[0]) for row in conn.execute(
                        "SELECT composerId FROM composerHeaders WHERE COALESCE(isSubagent, 0) = 0"
                    )
                )
            finally:
                conn.close()
        except sqlite3.Error:
            pass
    for db_path in root.glob("*/state.vscdb"):
        if time.monotonic() > deadline:
            logger.warning("session import: Cursor count stopped after its time budget")
            break
        try:
            conn = _open_foreign_sqlite(db_path, deadline)
            try:
                ids.update(
                    str(row[0]).split(":", 1)[-1] for row in conn.execute(
                        "SELECT key FROM ItemTable WHERE key LIKE 'composerData:%'"
                    )
                )
            finally:
                conn.close()
        except sqlite3.Error:
            continue
    return ids


def _file_ids(patterns: Sequence[str]) -> Callable[[Path], set[str]]:
    def count(root: Path) -> set[str]:
        return {path.stem for pattern in patterns for path in root.glob(pattern)}

    return count


# Count-only discovery: the scan shown before an import lists files or one
# index, never the conversations. Reading every transcript there (hundreds
# of Cursor chats in a database of tens of GB, through /mnt/c under WSL)
# took minutes; the content is read by the import job itself.
_COUNTERS: dict[str, Callable[[Path], set[str]]] = {
    "claude-code": _file_ids(("*/*.jsonl",)),
    "codex": _file_ids(("*/*/*/rollout-*.jsonl",)),
    "opencode": _file_ids(("session/*/ses_*.json",)),
    "oh-my-pi": _file_ids(("agent/sessions/*/*.jsonl", "sessions/*/*.jsonl")),
    "cursor": _cursor_ids,
}


def _root_exists(path: Path) -> bool:
    """``Path.exists`` that never raises.

    On Windows a path under ``\\\\wsl.localhost\\<distro>`` or a mapped drive that
    is asleep or unreachable can raise (WinError 53, 1326, a permission error)
    instead of answering "no". One such root must not sink the whole scan.
    """
    try:
        return path.exists()
    except (OSError, ValueError):
        return False


def discover(
    sources: Sequence[str],
    extra_roots: Mapping[str, Sequence[Path | str]] | None = None,
    *,
    include_saved_roots: bool = True,
    count_only: bool = False,
) -> list[SourceReport]:
    """Locate each requested source on this machine and parse what is ready.

    Roots per source are, in order: the built-in default and fallback
    locations, roots saved by the user (~/.navin/session_import_roots.json,
    e.g. a mounted Windows/macOS Cursor directory) and call-time
    *extra_roots*. Every existing root is parsed; sessions are deduped by
    session id in encounter order.
    """
    wanted = set(sources)
    saved = load_extra_roots() if include_saved_roots else {}
    call_time = {
        name: [Path(p) for p in roots]
        for name, roots in (extra_roots or {}).items()
    }
    reports: list[SourceReport] = []
    for spec in _source_specs():
        if spec.name not in wanted:
            continue
        roots = [
            spec.default_root,
            *spec.extra_roots,
            *(Path(p) for p in saved.get(spec.name, [])),
            *call_time.get(spec.name, []),
        ]
        existing = list(dict.fromkeys(r for r in roots if _root_exists(r)))
        if not existing:
            reports.append(
                SourceReport(
                    name=spec.name,
                    label=spec.label,
                    root=spec.default_root,
                    status="missing",
                    note="no data found on this machine",
                )
            )
            continue
        root = existing[0]
        if spec.parse is None:
            reports.append(
                SourceReport(
                    name=spec.name,
                    label=spec.label,
                    root=root,
                    status="unsupported",
                    note=(
                        f"data found at {root} but this format is not "
                        "supported by the importer in this version"
                    ),
                )
            )
            continue
        counter = _COUNTERS.get(spec.name)
        if count_only and counter is not None:
            ids: set[str] = set()
            failure: str | None = None
            for scan_root in existing:
                try:
                    ids |= counter(scan_root)
                except OSError:
                    continue
                except Exception as exc:  # noqa: BLE001 - one unreadable source must not sink the scan
                    logger.exception("session import: counting {} in {} failed", spec.name, scan_root)
                    failure = f"{type(exc).__name__}: {exc}"
            if failure and not ids:
                reports.append(
                    SourceReport(
                        name=spec.name,
                        label=spec.label,
                        root=root,
                        status="unsupported",
                        note=f"could not read this source ({failure[:160]})",
                    )
                )
                continue
            reports.append(
                SourceReport(
                    name=spec.name,
                    label=spec.label,
                    root=root,
                    status="ready",
                    note="" if ids else "no readable sessions found",
                    count=len(ids),
                )
            )
            continue
        sessions: list[ExternalSession] = []
        seen_ids: set[str] = set()
        failure = None
        for scan_root in existing:
            try:
                for session in spec.parse(scan_root):
                    if session.session_id in seen_ids:
                        continue
                    seen_ids.add(session.session_id)
                    sessions.append(session)
            except Exception as exc:  # noqa: BLE001 - one unreadable source must not sink the import
                logger.exception("session import: reading {} in {} failed", spec.name, scan_root)
                failure = f"{type(exc).__name__}: {exc}"
        if failure and not sessions:
            reports.append(
                SourceReport(
                    name=spec.name,
                    label=spec.label,
                    root=root,
                    status="unsupported",
                    note=f"could not read this source ({failure[:160]})",
                )
            )
            continue
        reports.append(
            SourceReport(
                name=spec.name,
                label=spec.label,
                root=root,
                status="ready",
                sessions=sessions,
                note="" if sessions else "no readable sessions found",
            )
        )
    return reports


def _naive_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Defense in depth: no aware timestamp may reach a session file."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        clone = dict(msg)
        ts = clone.get("timestamp")
        if isinstance(ts, str):
            clone["timestamp"] = _rfc3339_to_iso(ts) or ts
        out.append(clone)
    return out


def _existing_session_paths(manager: SessionManager) -> set[str]:
    return {path.name for path in manager.sessions_dir.glob("*.jsonl")}


def _origin_project(origin_cwd: str | None) -> Path | None:
    """The project a session was about, when it still exists here.

    Pinning the import to that folder is what makes Navin load the project's
    own AGENTS.md / CLAUDE.md, rules (.cursor/rules, .claude, .navin/rules),
    skills and agents when the conversation is resumed, exactly as if it had
    started in Navin there. The home directory or a filesystem root is not a
    project (Codex records ~ when launched there): those fall back to the
    import workspace.
    """
    if not origin_cwd:
        return None
    raw = origin_cwd.strip()
    # Cursor and Windows-side tools record Windows paths; under WSL they are
    # reachable through /mnt/<drive>.
    win = re.match(r"^/?([A-Za-z]):[\\/](.*)$", raw)
    if win and os.name != "nt":
        raw = f"/mnt/{win.group(1).lower()}/{win.group(2).replace(chr(92), '/')}"
    try:
        path = Path(raw).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    if not path.is_dir() or path == Path.home().resolve() or len(path.parts) <= 1:
        return None
    return path


def import_to_workspace(
    reports: Sequence[SourceReport],
    workspace: Path,
    *,
    overwrite: bool = False,
    limit: int = 0,
) -> list[SourceImportStats]:
    """Write discovered sessions into *workspace* with the Navin format."""
    manager = SessionManager(workspace)
    present = _existing_session_paths(manager)
    stats: list[SourceImportStats] = []
    for report in reports:
        stat = SourceImportStats(
            name=report.name,
            label=report.label,
            status=report.status,
            note=report.note,
        )
        stat.discovered = len(report.sessions)
        if report.status != "ready":
            stats.append(stat)
            continue
        for external in report.sessions:
            if limit and stat.imported >= limit:
                break
            key = _session_key(external.source, external.session_id)
            filename = f"{SessionManager._storage_key(key)}.jsonl"
            if filename in present and not overwrite:
                stat.skipped_existing += 1
                continue
            session = Session(
                key=key,
                messages=_naive_messages(external.messages),
                created_at=(
                    _to_naive_local(external.created_at)
                    if external.created_at
                    else datetime.now()
                ),
                updated_at=(
                    _to_naive_local(external.updated_at)
                    if external.updated_at
                    else datetime.now()
                ),
                metadata={
                    # Same markers a chat opened in the desktop carries
                    # (webui + workspace_scope with an access mode), so the
                    # imported conversation lists, resumes and loads its
                    # project exactly like one started in Navin.
                    "webui": True,
                    # Sessions made of throwaway keystrokes (".", ".exit")
                    # have no real title; show something instead of blank.
                    "title": external.title.strip() or "Session importée",
                    "import_source": external.source,
                    "origin_path": str(external.origin_path),
                    "imported_at": _utc_now_iso(),
                    # Pin the session to the target workspace so the
                    # desktop shows it under that project instead of
                    # falling back to the default scope.
                    "workspace_scope": {
                        "project_path": str(
                            _origin_project(external.origin_cwd)
                            or workspace.expanduser().resolve()
                        ),
                        "access_mode": "full",
                    },
                    **(
                        {"origin_cwd": external.origin_cwd}
                        if external.origin_cwd
                        else {}
                    ),
                },
            )
            manager.save(session)
            present.add(filename)
            stat.imported += 1
        stats.append(stat)
    return stats
