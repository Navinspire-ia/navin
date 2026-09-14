//! The `ProjectManifest` is the engine's model of a project: what it is,
//! how to build/test/start it, and which services it talks to.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

use super::runtime::Runtime;

/// One buildable/runnable unit inside the workspace (a monorepo has many).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ProjectUnit {
    /// Directory of the unit, relative to the workspace root.
    pub path: String,
    pub runtime: Runtime,
    /// Framework hint (react, next, vite, fastapi, django, axum, ...).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub framework: Option<String>,
    /// Package manager (npm, pnpm, yarn, cargo, uv, pip, go, maven).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub package_manager: Option<String>,
    /// Resolved lifecycle commands, best effort.
    pub commands: LifecycleCommands,
}

/// Commands the engine can run to build, test and start the unit.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct LifecycleCommands {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub build: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub test: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub start: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dev: Option<String>,
}

/// A service declared by the topology (docker-compose, procfile, ...).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Service {
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub image: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub ports: Vec<u16>,
    /// Coarse category: database, cache, queue, app, other.
    pub kind: ServiceKind,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ServiceKind {
    Database,
    Cache,
    Queue,
    App,
    Other,
}

/// Everything discovery learned about a workspace.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectManifest {
    /// Manifest schema version, bumped on breaking changes.
    pub schema: String,
    /// Absolute workspace root the manifest was built from.
    pub root: PathBuf,
    /// True when more than one unit was found.
    pub monorepo: bool,
    pub units: Vec<ProjectUnit>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub services: Vec<Service>,
    /// Environment files present at the root (never their contents).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub env_files: Vec<String>,
    /// True when a Dockerfile exists at the root.
    pub dockerfile: bool,
    /// True when the root is a git repository (worktree isolation needs it).
    pub git: bool,
    /// Command the engine would use to start the application, resolved
    /// across every unit and entry point it knows about.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub start_command: Option<String>,
}

pub const MANIFEST_SCHEMA: &str = "navin-manifest/v1";
