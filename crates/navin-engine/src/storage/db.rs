//! SQLite handle for the engine, one file per project workspace:
//! `.navin/evolve/engine.db`.

use anyhow::{Context, Result};
use rusqlite::Connection;
use std::path::Path;
use std::sync::Mutex;

use super::migrations;
use super::models::ProjectRow;

pub struct Database {
    conn: Mutex<Connection>,
}

impl Database {
    pub fn open(engine_dir: &Path) -> Result<Self> {
        std::fs::create_dir_all(engine_dir)
            .with_context(|| format!("cannot create {}", engine_dir.display()))?;
        let path = engine_dir.join("engine.db");
        let mut conn = Connection::open(&path)
            .with_context(|| format!("cannot open {}", path.display()))?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "foreign_keys", "ON")?;
        migrations::apply(&mut conn).context("schema migration failed")?;
        Ok(Database { conn: Mutex::new(conn) })
    }

    /// Upsert the project row and refresh its last-seen timestamp.
    pub fn record_project(&self, root: &Path) -> Result<i64> {
        let conn = self.conn.lock().expect("db lock");
        conn.execute(
            "INSERT INTO projects (root) VALUES (?1)
             ON CONFLICT(root) DO UPDATE SET
               last_seen_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')",
            [root.to_string_lossy()],
        )?;
        let id = conn.query_row(
            "SELECT id FROM projects WHERE root = ?1",
            [root.to_string_lossy()],
            |row| row.get(0),
        )?;
        Ok(id)
    }

    pub fn projects(&self) -> Result<Vec<ProjectRow>> {
        let conn = self.conn.lock().expect("db lock");
        let mut stmt = conn.prepare(
            "SELECT id, root, first_seen_at, last_seen_at FROM projects ORDER BY id",
        )?;
        let rows = stmt
            .query_map([], |row| {
                Ok(ProjectRow {
                    id: row.get(0)?,
                    root: row.get(1)?,
                    first_seen_at: row.get(2)?,
                    last_seen_at: row.get(3)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(rows)
    }

    pub fn audit(&self, actor: &str, action: &str, detail: Option<&str>) -> Result<()> {
        let conn = self.conn.lock().expect("db lock");
        conn.execute(
            "INSERT INTO audit_events (actor, action, detail) VALUES (?1, ?2, ?3)",
            rusqlite::params![actor, action, detail],
        )?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn open_migrate_and_record_project() {
        let tmp = tempfile::tempdir().unwrap();
        let db = Database::open(tmp.path()).unwrap();
        let id = db.record_project(Path::new("/tmp/demo")).unwrap();
        // Idempotent: the same root keeps its row.
        let again = db.record_project(Path::new("/tmp/demo")).unwrap();
        assert_eq!(id, again);
        let projects = db.projects().unwrap();
        assert_eq!(projects.len(), 1);
        assert_eq!(projects[0].root, "/tmp/demo");
        db.audit("cli", "test", None).unwrap();
    }
}
