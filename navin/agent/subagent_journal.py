# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Private, durable execution history for background agents.

SQLite transactions commit before a tool batch is allowed to start. A crash
in that batch is deliberately ambiguous and must never replay its side effects.
Provider credentials and hidden reasoning are not part of this journal.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Collection
from pathlib import Path
from threading import RLock
from typing import Any


def visible_message(message: dict[str, Any]) -> dict[str, Any]:
    """Keep observable conversation content, excluding hidden model reasoning."""
    result = {key: value for key, value in message.items()
              if key in {"role", "content", "tool_calls", "tool_call_id", "name"}}
    if isinstance(result.get("content"), list):
        result["content"] = [item for item in result["content"] if not isinstance(item, dict)
                             or item.get("type") not in {"thinking", "redacted_thinking", "reasoning"}]
    return result


def process_identity(pid: int) -> str:
    """Distinguish reused PIDs and fresh containers from the previous owner."""
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        stat = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
        return f"{boot}:{stat[19]}"
    except (OSError, IndexError):
        return ""


def owner_alive(pid: int, identity: str = "") -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # On Windows signal 0 is CTRL_C_EVENT, not a liveness probe: os.kill
        # raised WinError 87 and broke the Subagents panel.
        from navin.process_runtime import _windows_process_identity

        return _windows_process_identity(pid) is not None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return not identity or identity == process_identity(pid)


class SubagentJournal:
    """Indexed session-scoped history. Events are paginated, never truncated."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        path.chmod(0o600)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS agents (
                session TEXT NOT NULL, task_id TEXT NOT NULL,
                metadata TEXT NOT NULL, descriptor TEXT NOT NULL,
                checkpoint TEXT, checkpoint_phase TEXT, owner_pid INTEGER NOT NULL,
                owner_identity TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(session, task_id)
            );
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                session TEXT NOT NULL, task_id TEXT NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS agent_event_page
                ON events(session, task_id, seq);
            CREATE INDEX IF NOT EXISTS agent_session_page
                ON agents(session, updated_at DESC);
        """)
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(agents)")}
        if "owner_identity" not in columns:
            self._db.execute("ALTER TABLE agents ADD COLUMN owner_identity TEXT NOT NULL DEFAULT ''")
        if "created_at" not in columns:
            self._db.execute("ALTER TABLE agents ADD COLUMN created_at REAL NOT NULL DEFAULT 0")
            self._db.execute("UPDATE agents SET created_at=updated_at WHERE created_at=0")
        if "checkpoint_phase" not in columns:
            self._db.execute("ALTER TABLE agents ADD COLUMN checkpoint_phase TEXT")
            self._db.execute("UPDATE agents SET checkpoint_phase=json_extract(checkpoint,'$.phase')")
        self._db.execute("CREATE INDEX IF NOT EXISTS agent_stable_page ON agents(session,created_at,task_id)")
        self._db.commit()
        self.prune()

    def prune(self, max_age_days: float = 30.0) -> int:
        """Drop finished agents (and their events) untouched for max_age_days.

        Unfinished agents are kept whatever their age: they may still be
        resumed or relaunched.
        """
        cutoff = time.time() - max_age_days * 86400
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT session,task_id FROM agents WHERE updated_at<? "
                "AND COALESCE(json_extract(metadata,'$.done'),0)<>0", (cutoff,),
            ).fetchall()
            for session, task_id in rows:
                self._db.execute("DELETE FROM events WHERE session=? AND task_id=?", (session, task_id))
                self._db.execute("DELETE FROM agents WHERE session=? AND task_id=?", (session, task_id))
        return len(rows)

    def create(self, session: str, task_id: str, metadata: dict, descriptor: dict) -> None:
        with self._lock, self._db:
            created_at = time.time()
            self._db.execute("INSERT INTO agents(session,task_id,metadata,descriptor,checkpoint,"
                             "owner_pid,owner_identity,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)", (
                session, task_id, json.dumps(metadata), json.dumps(descriptor),
                None, os.getpid(), process_identity(os.getpid()), created_at, created_at,
            ))
            self._append(session, task_id, "task", {"role": "user", "content": descriptor["task"]})

    def _append(self, session: str, task_id: str, kind: str, payload: Any) -> None:
        self._db.execute(
            "INSERT INTO events(session,task_id,kind,payload,created_at) VALUES (?,?,?,?,?)",
            (session, task_id, kind, json.dumps(payload, ensure_ascii=False), time.time()),
        )

    def append(self, session: str, task_id: str, kind: str, payload: Any) -> None:
        with self._lock, self._db:
            self._append(session, task_id, kind, payload)

    def append_many(self, rows: list[tuple[str, str]], kind: str, payload: Any) -> None:
        """Append the same event to many agents in one transaction."""
        with self._lock, self._db:
            for session, task_id in rows:
                self._append(session, task_id, kind, payload)

    def status(self, session: str, task_id: str, metadata: dict) -> None:
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT metadata FROM agents WHERE session=? AND task_id=?", (session, task_id),
            ).fetchone()
            if row is None:
                return
            previous = json.loads(row[0])
            previous.update(metadata)
            self._db.execute(
                "UPDATE agents SET metadata=?,updated_at=? WHERE session=? AND task_id=?",
                (json.dumps(previous), time.time(), session, task_id),
            )

    def checkpoint(self, session: str, task_id: str, payload: dict) -> None:
        clean = dict(payload)
        if isinstance(clean.get("assistant_message"), dict):
            clean["assistant_message"] = visible_message(clean["assistant_message"])
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT checkpoint FROM agents WHERE session=? AND task_id=?", (session, task_id),
            ).fetchone()
            if row is None:
                return
            previous = json.loads(row[0]) if row[0] else {}
            # awaiting_tools and tools_completed carry the same assistant message.
            repeats_batch_message = (
                clean.get("phase") == "tools_completed" and previous.get("phase") == "awaiting_tools"
                and previous.get("iteration") == clean.get("iteration")
                and previous.get("assistant_message") == clean.get("assistant_message")
            )
            if clean.get("assistant_message") and not repeats_batch_message:
                self._append(session, task_id, "message", clean["assistant_message"])
            for message in clean.get("completed_tool_results") or []:
                self._append(session, task_id, "message", visible_message(message))
            self._db.execute(
                "UPDATE agents SET checkpoint=?,checkpoint_phase=?,updated_at=? WHERE session=? AND task_id=?",
                (json.dumps(clean), clean.get("phase"), time.time(), session, task_id),
            )

    def get(self, session: str, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM agents WHERE session=? AND task_id=?", (session, task_id),
            ).fetchone()
            return self._decode(row) if row else None

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict:
        return {"metadata": json.loads(row["metadata"]),
                "descriptor": json.loads(row["descriptor"]),
                "checkpoint": json.loads(row["checkpoint"]) if row["checkpoint"] else None,
                "owner_pid": row["owner_pid"], "owner_identity": row["owner_identity"]}

    def list(
        self, session: str, offset: int = 0, limit: int = 200, *,
        workspaces: Collection[str] | None = None, since: float | None = None,
    ) -> list[dict]:
        """Agents of ``session``, oldest first.

        A session key is not unique across projects (every CLI starts on
        ``cli:direct``) nor across /new. ``workspaces`` keeps the agents started
        for one of those project folders, ``since`` those started at or after
        that time (epoch seconds).
        """
        where, params = ["session=?"], [session]
        if workspaces is not None:
            folders = sorted(set(workspaces))
            if not folders:
                return []
            where.append(f"json_extract(descriptor,'$.workspace') IN ({','.join('?' * len(folders))})")
            params.extend(folders)
        if since is not None:
            where.append("created_at>=?")
            params.append(since)
        with self._lock:
            rows = self._db.execute(
                "SELECT metadata,owner_pid,owner_identity,checkpoint_phase,"
                "json_extract(descriptor,'$.isolate') AS isolated FROM agents "
                f"WHERE {' AND '.join(where)} ORDER BY created_at,task_id LIMIT ? OFFSET ?",
                (*params, min(1000, max(1, limit)), max(0, offset)),
            ).fetchall()
            return [{"metadata": json.loads(row["metadata"]),
                     "owner_pid": row["owner_pid"], "owner_identity": row["owner_identity"],
                     "descriptor": {"isolate": bool(row["isolated"])},
                     "checkpoint": {"phase": row["checkpoint_phase"]}} for row in rows]

    def history(self, session: str, task_id: str, after: int = 0, limit: int = 200) -> dict:
        limit = min(1000, max(1, limit))
        events, size, has_more = [], 0, False
        with self._lock:
            cursor = self._db.execute(
                "SELECT * FROM events WHERE session=? AND task_id=? AND seq>? ORDER BY seq LIMIT ?",
                (session, task_id, max(0, after), limit + 1),
            )
            for row in cursor:
                row_size = len(row["payload"].encode("utf-8"))
                if len(events) >= limit or (events and size + row_size > 512 * 1024):
                    has_more = True
                    break
                size += row_size
                events.append({"seq": row["seq"], "kind": row["kind"],
                               "payload": json.loads(row["payload"]), "created_at": row["created_at"]})
            cursor.close()
        return {"events": events, "next_cursor": events[-1]["seq"] if events else after,
                "has_more": has_more}

    def unfinished(self, limit: int = 1000) -> list[tuple[str, str]]:
        """(session, task_id) of agents whose owning process is gone and that never finished."""
        with self._lock:
            rows = self._db.execute(
                "SELECT session,task_id,owner_pid,owner_identity FROM agents "
                "WHERE COALESCE(json_extract(metadata,'$.done'),0)=0 ORDER BY created_at LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [(row[0], row[1]) for row in rows
                if not owner_alive(row[2], row[3] or "")]

    def deliver_injection(self, session: str, task_id: str, message: dict, injection: dict) -> None:
        """Record consumption and model-visible injection in one transaction."""
        with self._lock, self._db:
            self._append(session, task_id, "injection", injection)
            self._append(session, task_id, "team_delivered", {"message_id": message["message_id"]})

    def messages(self, session: str, task_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT payload FROM events WHERE session=? AND task_id=? "
                "AND kind IN ('task','message','injection') ORDER BY seq", (session, task_id),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def claim(self, session: str, task_id: str) -> bool:
        """Atomically claim abandoned work, never steal a living process's task."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT owner_pid,owner_identity,metadata FROM agents WHERE session=? AND task_id=?", (session, task_id),
                ).fetchone()
                metadata = json.loads(row[2]) if row else {}
                cancelled = metadata.get("done") and str(metadata.get("error") or "").lower() in {"cancelled", "canceled"}
                if row is None or (owner_alive(row[0], row[1]) and not cancelled):
                    self._db.rollback()
                    return False
                metadata.update(done=False, error=None, phase="initializing", status_line="Resuming agent")
                self._db.execute("UPDATE agents SET owner_pid=?,owner_identity=?,metadata=? "
                                 "WHERE session=? AND task_id=?",
                                 (os.getpid(), process_identity(os.getpid()), json.dumps(metadata), session, task_id))
                self._append(session, task_id, "recovery", {"status": "resumed"})
                self._db.commit()
                return True
            except BaseException:
                self._db.rollback()
                raise

    def pending_messages(self, session: str, task_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT kind,payload FROM events WHERE session=? AND task_id=? "
                "AND kind IN ('team_received','team_delivered') ORDER BY seq", (session, task_id),
            ).fetchall()
        pending = {}
        for row in rows:
            item = json.loads(row["payload"])
            if row["kind"] == "team_received":
                pending[item["message_id"]] = item
            else:
                pending.pop(item["message_id"], None)
        return list(pending.values())

    def abandon(self, session: str, task_id: str) -> None:
        """Release a claimed checkpoint when recovery admission is cancelled."""
        with self._lock, self._db:
            self._db.execute("UPDATE agents SET owner_pid=-1,owner_identity='' "
                             "WHERE session=? AND task_id=? AND owner_pid=? AND owner_identity=?",
                             (session, task_id, os.getpid(), process_identity(os.getpid())))
