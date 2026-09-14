//! Shadow isolation: every experiment runs in `.navin/shadow/<run-id>`,
//! never in the user's workspace. Git worktrees pin the exact SHA; a
//! filesystem copy is the fallback for non-git projects.

pub mod cleanup;
pub mod deps;
pub mod filesystem;
pub mod manager;
pub mod sandbox;
pub mod worktree;

pub use manager::{Shadow, ShadowManager, ShadowMode};
