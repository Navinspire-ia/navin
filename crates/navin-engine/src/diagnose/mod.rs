//! Diagnose engine: the "Diagnose" stage between Proof and Fix. It reads a
//! robustness proof plus the service log and produces evidence-backed
//! root-cause findings, each tagged with a remediation family so the
//! Fix/Evolve stage knows where to look. Pure and offline: it analyses
//! artefacts, it does not run anything against the workspace.

pub mod engine;
pub mod log_scan;
pub mod model;
pub mod rules;
pub mod symptoms;

pub use engine::{diagnose, diagnose_project};
pub use model::{Confidence, Diagnosis, Finding, Severity};
