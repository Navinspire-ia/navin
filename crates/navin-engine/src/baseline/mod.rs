//! Baseline engine: measure the project before any mutation, so every
//! later claim ("+37% throughput") has a number to compare against.

pub mod collector;
pub mod cpu;
pub mod latency;
pub mod memory;
pub mod report;

pub use collector::{collect_baseline, BaselineOptions};
pub use report::BaselineReport;
