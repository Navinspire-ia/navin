//! Evolve orchestrator: the single entry point that runs the whole
//! Break -> Diagnose -> Fix -> Prove -> Evolve -> Certify loop in one pass,
//! wiring the pluggable candidate generator (e.g. the desktop LLM bridge)
//! into the verified fix-and-promote machinery.

pub mod engine;
pub mod model;

pub use engine::{run_evolve, EvolveContext};
pub use model::{EvolveReport, FindingOutcome};
