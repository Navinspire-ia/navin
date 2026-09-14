//! Optimize (ASSE): generate N variants of healthy code, benchmark them
//! under identical load in isolated shadows, and promote only the measured
//! winner - verified by tests and a fresh proof, certified and signed.

pub mod engine;
pub mod model;
pub mod stats;

pub use engine::{run_optimize, OptimizeContext};
pub use model::{Objective, OptimizeReport, VariantOutcome};
