//! Project discovery: understand how a project is built, tested and run
//! before the engine ever measures or mutates it.

pub mod commands;
pub mod detector;
pub mod manifest;
pub mod resolve;
pub mod runtime;
pub mod topology;

pub use detector::inspect_project;
pub use manifest::ProjectManifest;
pub use resolve::{start_candidates, start_command, suggested_ports};
