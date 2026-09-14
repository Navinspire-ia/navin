//! Forward-only schema migrations, applied inside one transaction each.

use rusqlite::Connection;

/// Ordered list; the index + 1 is the schema version.
pub const MIGRATIONS: &[&str] = &[
    // v1: projects, runs and audit skeleton.
    "
    CREATE TABLE projects (
        id INTEGER PRIMARY KEY,
        root TEXT NOT NULL UNIQUE,
        first_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        last_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    );
    CREATE TABLE runs (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id),
        kind TEXT NOT NULL,
        state TEXT NOT NULL,
        started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        finished_at TEXT,
        detail TEXT
    );
    CREATE INDEX runs_project ON runs(project_id, started_at);
    CREATE TABLE audit_events (
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        detail TEXT
    );
    ",
];

pub fn apply(conn: &mut Connection) -> rusqlite::Result<u32> {
    let current: u32 = conn.query_row("PRAGMA user_version", [], |row| row.get(0))?;
    for (index, sql) in MIGRATIONS.iter().enumerate() {
        let version = (index + 1) as u32;
        if version <= current {
            continue;
        }
        let tx = conn.transaction()?;
        tx.execute_batch(sql)?;
        tx.pragma_update(None, "user_version", version)?;
        tx.commit()?;
    }
    Ok(MIGRATIONS.len() as u32)
}
