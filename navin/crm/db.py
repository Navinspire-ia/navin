# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""SQLite WAL connection and one-time JSON import for the project CRM."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()

_DDL = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS companies (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  industry TEXT DEFAULT '',
  country TEXT DEFAULT '',
  website TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  owner TEXT DEFAULT '',
  tags TEXT DEFAULT '[]',
  notes TEXT DEFAULT '',
  created_by TEXT DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
  id TEXT PRIMARY KEY,
  first_name TEXT DEFAULT '',
  last_name TEXT DEFAULT '',
  title TEXT DEFAULT '',
  email TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  whatsapp TEXT DEFAULT '',
  linkedin TEXT DEFAULT '',
  company_id TEXT DEFAULT '',
  country TEXT DEFAULT '',
  owner TEXT DEFAULT '',
  source TEXT DEFAULT '',
  tags TEXT DEFAULT '[]',
  status TEXT DEFAULT 'actif',
  created_by TEXT DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS leads (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  company TEXT DEFAULT '',
  email TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  country TEXT DEFAULT '',
  source TEXT DEFAULT '',
  score INTEGER DEFAULT 0,
  owner TEXT DEFAULT '',
  status TEXT DEFAULT 'nouveau',
  converted_opportunity_id TEXT DEFAULT '',
  created_by TEXT DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS opportunities (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  amount REAL DEFAULT 0,
  currency TEXT DEFAULT 'EUR',
  probability INTEGER DEFAULT 10,
  stage TEXT DEFAULT 'nouveau',
  close_date TEXT DEFAULT '',
  company_id TEXT DEFAULT '',
  contact_ids TEXT DEFAULT '[]',
  owner TEXT DEFAULT '',
  source TEXT DEFAULT '',
  next_action TEXT DEFAULT '',
  loss_reason TEXT DEFAULT '',
  created_by TEXT DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS activities (
  id TEXT PRIMARY KEY,
  kind TEXT DEFAULT 'note',
  title TEXT NOT NULL,
  body TEXT DEFAULT '',
  at INTEGER NOT NULL,
  contact_id TEXT DEFAULT '',
  company_id TEXT DEFAULT '',
  opportunity_id TEXT DEFAULT '',
  lead_id TEXT DEFAULT '',
  owner TEXT DEFAULT '',
  created_by TEXT DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  default_price REAL DEFAULT 0,
  currency TEXT DEFAULT 'EUR',
  owner TEXT DEFAULT '',
  created_by TEXT DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS opportunity_lines (
  id TEXT PRIMARY KEY,
  opportunity_id TEXT NOT NULL,
  product_id TEXT DEFAULT '',
  name TEXT DEFAULT '',
  qty REAL DEFAULT 1,
  unit_price REAL DEFAULT 0,
  total REAL DEFAULT 0,
  created_by TEXT DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
  id TEXT PRIMARY KEY,
  identity TEXT NOT NULL,
  email TEXT DEFAULT '',
  handle TEXT DEFAULT '',
  user_id TEXT DEFAULT '',
  display_name TEXT DEFAULT '',
  role TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS invitations (
  id TEXT PRIMARY KEY,
  identity TEXT NOT NULL,
  email TEXT DEFAULT '',
  handle TEXT DEFAULT '',
  user_id TEXT DEFAULT '',
  role TEXT NOT NULL,
  status TEXT NOT NULL,
  invited_by TEXT DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
  id TEXT PRIMARY KEY,
  action TEXT NOT NULL,
  kind TEXT DEFAULT '',
  record_id TEXT DEFAULT '',
  actor TEXT DEFAULT '',
  detail TEXT DEFAULT '',
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
  id TEXT PRIMARY KEY,
  name TEXT DEFAULT '',
  mime TEXT DEFAULT '',
  size INTEGER DEFAULT 0,
  path TEXT DEFAULT '',
  contact_id TEXT DEFAULT '',
  company_id TEXT DEFAULT '',
  opportunity_id TEXT DEFAULT '',
  lead_id TEXT DEFAULT '',
  owner TEXT DEFAULT '',
  created_by TEXT DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  id TEXT PRIMARY KEY,
  company_name TEXT DEFAULT '',
  legal_name TEXT DEFAULT '',
  industry TEXT DEFAULT '',
  country TEXT DEFAULT '',
  website TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  email TEXT DEFAULT '',
  currency TEXT DEFAULT 'EUR',
  currency_symbol TEXT DEFAULT '€',
  locale TEXT DEFAULT 'fr-FR',
  configured INTEGER DEFAULT 0,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id);
CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_opps_stage ON opportunities(stage);
CREATE INDEX IF NOT EXISTS idx_opps_company ON opportunities(company_id);
CREATE INDEX IF NOT EXISTS idx_acts_at ON activities(at);
CREATE INDEX IF NOT EXISTS idx_acts_opp ON activities(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_acts_contact ON activities(contact_id);
CREATE INDEX IF NOT EXISTS idx_lines_opp ON opportunity_lines(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_members_identity ON members(identity);
CREATE INDEX IF NOT EXISTS idx_invites_identity ON invitations(identity);
CREATE INDEX IF NOT EXISTS idx_audit_record ON audit(kind, record_id, created_at);
"""


def crm_root(project: Path) -> Path:
    return Path(project) / ".navin" / "crm"


def sqlite_path(project: Path) -> Path:
    return crm_root(project) / "crm.sqlite"


def project_lock(project: Path) -> threading.Lock:
    key = str(Path(project).resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def connect(project: Path) -> sqlite3.Connection:
    root = crm_root(project)
    root.mkdir(parents=True, exist_ok=True)
    path = sqlite_path(project)
    existed = path.is_file()
    connection = sqlite3.connect(str(path), timeout=15, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=15000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(_DDL)
    _ensure_columns(connection)
    _ensure_version(connection)
    if not existed or _is_empty(connection):
        _import_json(project, connection)
    connection.commit()
    return connection


def _ensure_columns(connection: sqlite3.Connection) -> None:
    specs = {
        "contacts": [("country", "TEXT DEFAULT ''")],
        "leads": [("country", "TEXT DEFAULT ''")],
    }
    for table, columns in specs.items():
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _ensure_version(connection: sqlite3.Connection) -> None:
    row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None:
        connection.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )


def _is_empty(connection: sqlite3.Connection) -> bool:
    tables = (
        "companies",
        "contacts",
        "leads",
        "opportunities",
        "activities",
        "products",
        "members",
    )
    for table in tables:
        count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if int(count) > 0:
            return False
    return True


def _load_json_kind(project: Path, kind: str) -> list[dict[str, Any]]:
    path = crm_root(project) / f"{kind}.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    rows = data.get(kind) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("id")]


def _import_json(project: Path, connection: sqlite3.Connection) -> None:
    from navin.crm.store import _row_to_sql, _table_for

    imported = 0
    for kind in (
        "companies",
        "contacts",
        "leads",
        "opportunities",
        "activities",
        "products",
    ):
        rows = _load_json_kind(project, kind)
        table = _table_for(kind)
        for row in rows:
            cols, values = _row_to_sql(kind, row)
            placeholders = ", ".join("?" for _ in cols)
            connection.execute(
                f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )
            imported += 1
    settings_path = crm_root(project) / "settings.json"
    if settings_path.is_file():
        try:
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raw = None
        if isinstance(raw, dict) and str(raw.get("companyName") or "").strip():
            connection.execute(
                "INSERT OR IGNORE INTO settings("
                "id, company_name, legal_name, industry, country, website, phone, email, "
                "currency, currency_symbol, locale, configured, updated_at) "
                "VALUES ('default', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (
                    str(raw.get("companyName") or "")[:120],
                    str(raw.get("legalName") or "")[:160],
                    str(raw.get("industry") or "")[:80],
                    str(raw.get("country") or "")[:80],
                    str(raw.get("website") or "")[:200],
                    str(raw.get("phone") or "")[:40],
                    str(raw.get("email") or "")[:120],
                    str(raw.get("currency") or "EUR")[:8] or "EUR",
                    str(raw.get("currencySymbol") or "€")[:8] or "€",
                    str(raw.get("locale") or "fr-FR")[:16] or "fr-FR",
                    int(raw.get("updatedAt") or 0) or int(time.time()),
                ),
            )
    if imported:
        connection.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('imported_json', '1')"
        )


def wal_status(project: Path) -> dict[str, Any]:
    path = sqlite_path(project)
    wal = path.with_name(path.name + "-wal")
    shm = path.with_name(path.name + "-shm")
    return {
        "path": str(path),
        "exists": path.is_file(),
        "wal": wal.is_file(),
        "shm": shm.is_file(),
        "bytes": path.stat().st_size if path.is_file() else 0,
        "walBytes": wal.stat().st_size if wal.is_file() else 0,
    }
