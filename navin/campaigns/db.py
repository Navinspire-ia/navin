# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Campaigns storage: one SQLite book per Navin instance.

Global like the Leads book (not per project): the send loop and the reply
poller run without a conversation, and lists outlive any one project.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_DDL = """
CREATE TABLE IF NOT EXISTS contacts (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    attrs TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'subscribed',
    source TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS contacts_email ON contacts(lower(email));
CREATE INDEX IF NOT EXISTS contacts_phone ON contacts(phone);

CREATE TABLE IF NOT EXISTS lists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS list_members (
    list_id TEXT NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    PRIMARY KEY (list_id, contact_id)
);

CREATE TABLE IF NOT EXISTS templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'email',
    subject TEXT NOT NULL DEFAULT '',
    preheader TEXT NOT NULL DEFAULT '',
    html TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    wa_template TEXT NOT NULL DEFAULT '',
    wa_language TEXT NOT NULL DEFAULT '',
    wa_params TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'email',
    status TEXT NOT NULL DEFAULT 'draft',
    subject TEXT NOT NULL DEFAULT '',
    preheader TEXT NOT NULL DEFAULT '',
    from_name TEXT NOT NULL DEFAULT '',
    reply_to TEXT NOT NULL DEFAULT '',
    html TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    wa_template TEXT NOT NULL DEFAULT '',
    wa_language TEXT NOT NULL DEFAULT '',
    wa_params TEXT NOT NULL DEFAULT '[]',
    template_id TEXT NOT NULL DEFAULT '',
    list_ids TEXT NOT NULL DEFAULT '[]',
    contact_ids TEXT NOT NULL DEFAULT '[]',
    attachments TEXT NOT NULL DEFAULT '[]',
    schedule_at TEXT NOT NULL DEFAULT '',
    throttle_per_minute INTEGER NOT NULL DEFAULT 30,
    track_opens INTEGER NOT NULL DEFAULT 1,
    track_clicks INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    launched_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sends (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    contact_id TEXT NOT NULL,
    address TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    sent_at TEXT NOT NULL DEFAULT '',
    opened_at TEXT NOT NULL DEFAULT '',
    open_count INTEGER NOT NULL DEFAULT 0,
    clicked_at TEXT NOT NULL DEFAULT '',
    click_count INTEGER NOT NULL DEFAULT 0,
    replied_at TEXT NOT NULL DEFAULT '',
    bounced INTEGER NOT NULL DEFAULT 0,
    UNIQUE (campaign_id, contact_id)
);
CREATE INDEX IF NOT EXISTS sends_status ON sends(campaign_id, status);
CREATE INDEX IF NOT EXISTS sends_message ON sends(message_id);
CREATE INDEX IF NOT EXISTS sends_address ON sends(lower(address));

CREATE TABLE IF NOT EXISTS replies (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL DEFAULT '',
    send_id TEXT NOT NULL DEFAULT '',
    contact_id TEXT NOT NULL DEFAULT '',
    from_addr TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL,
    classification TEXT NOT NULL DEFAULT 'other',
    confidence REAL NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    assigned_to TEXT NOT NULL DEFAULT '',
    handoff_brief TEXT NOT NULL DEFAULT '',
    draft_reply TEXT NOT NULL DEFAULT '',
    answered_at TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT 'email'
);
CREATE UNIQUE INDEX IF NOT EXISTS replies_message ON replies(message_id) WHERE message_id != '';

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL DEFAULT '',
    send_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    at TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_kind_at ON events(kind, at);

CREATE TABLE IF NOT EXISTS followups (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    send_id TEXT NOT NULL,
    contact_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'sent',
    error TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    sent_at TEXT NOT NULL DEFAULT '',
    opened_at TEXT NOT NULL DEFAULT '',
    UNIQUE (send_id, step)
);
CREATE INDEX IF NOT EXISTS followups_campaign ON followups(campaign_id, step);
CREATE INDEX IF NOT EXISTS followups_message ON followups(message_id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_LOCK = threading.RLock()
_INITIALISED: set[str] = set()


def campaigns_root() -> Path:
    from navin.config.paths import get_runtime_subdir

    return get_runtime_subdir("campaigns")


def db_path(root: Path | None = None) -> Path:
    return (root or campaigns_root()) / "campaigns.sqlite"


def files_dir(root: Path | None = None) -> Path:
    path = (root or campaigns_root()) / "files"
    path.mkdir(parents=True, exist_ok=True)
    return path


# Columns added after a table first shipped: (table, column, definition).
_ADDED_COLUMNS = (
    ("replies", "channel", "TEXT NOT NULL DEFAULT 'email'"),
    ("campaigns", "steps", "TEXT NOT NULL DEFAULT '[]'"),
    ("campaigns", "subject_variants", "TEXT NOT NULL DEFAULT '[]'"),
    ("sends", "variant", "TEXT NOT NULL DEFAULT ''"),
)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, column, definition in _ADDED_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


@contextmanager
def connect(root: Path | None = None) -> Iterator[sqlite3.Connection]:
    """One serialized connection: the book is small and writes must not race."""
    path = db_path(root)
    with _LOCK:
        conn = sqlite3.connect(path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            if str(path) not in _INITIALISED:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(_DDL)
                _ensure_columns(conn)
                _INITIALISED.add(str(path))
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
