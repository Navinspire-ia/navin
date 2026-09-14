//! The Evolve daemon: owns the scheduler, the IPC server and the storage,
//! and stays near-zero cost while idle (event-driven, no hot polling).

pub mod lifecycle;
pub mod resource_guard;
pub mod scheduler;
pub mod watcher;
pub mod worker;

pub use lifecycle::run_daemon;
