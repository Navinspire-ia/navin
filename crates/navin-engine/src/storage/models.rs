//! Typed rows for the engine database.

use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct ProjectRow {
    pub id: i64,
    pub root: String,
    pub first_seen_at: String,
    pub last_seen_at: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct RunRow {
    pub id: i64,
    pub project_id: i64,
    pub kind: String,
    pub state: String,
    pub started_at: String,
    pub finished_at: Option<String>,
    pub detail: Option<String>,
}
