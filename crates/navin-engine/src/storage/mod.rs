//! Engine-local persistence. SQLite keeps metadata and references;
//! large artefacts (traces, reports, proofs) live on disk next to it.

pub mod db;
pub mod migrations;
pub mod models;
