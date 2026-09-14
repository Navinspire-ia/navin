//! Proof engine: the "Break -> Diagnose -> Prove" stage. It injects faults
//! into the app running inside a shadow, checks that invariants hold, and
//! emits a robustness report with a numeric score. Nothing here ever runs
//! against the user's real workspace.

pub mod checks;
pub mod engine;
pub mod faults;
pub mod model;
pub mod service;
pub mod worker;

pub use engine::{run_proof, run_proof_in_shadow, ProofPlan, ProofTarget, WORKER_URL};
pub use model::{ProofReport, Verdict};

/// Seconds since the Unix epoch, tagged, matching the baseline reports.
pub(crate) fn now_epoch() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format!("epoch:{secs}")
}
