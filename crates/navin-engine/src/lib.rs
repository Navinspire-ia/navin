//! Navin Evolve engine.
//!
//! Sprint 1 foundation: daemon lifecycle, versioned local IPC, project
//! discovery, SQLite-backed storage and the policy configuration loader.
//! Every destructive or experimental feature built on top of this crate
//! must operate inside `.navin/` and never touch the user's workspace
//! without an explicit promotion.

pub mod baseline;
pub mod daemon;
pub mod diagnose;
pub mod evolve;
pub mod fix;
pub mod ipc;
pub mod mcp;
pub mod optimize;
pub mod policy;
pub mod progress;
pub mod project;
pub mod promote;
pub mod proof;
pub mod report;
pub mod runner;
pub mod shadow;
pub mod storage;
pub mod target;
pub mod verify;

/// Version reported by `engine.status` and embedded in every artefact.
pub const ENGINE_VERSION: &str = env!("CARGO_PKG_VERSION");

/// The engine's private directory inside a project workspace.
pub const NAVIN_DIR: &str = ".navin";

/// Subdirectory of [`NAVIN_DIR`] holding engine state (endpoint, db, runs).
pub const EVOLVE_DIR: &str = "evolve";

use std::path::{Path, PathBuf};

/// Root of the engine state for a given project workspace.
pub fn engine_dir(project_root: &Path) -> PathBuf {
    project_root.join(NAVIN_DIR).join(EVOLVE_DIR)
}
