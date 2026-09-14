//! Process supervision for the applications under test: spawn in a
//! process group, capture logs, health-check, and kill the whole tree.

pub mod discover;
pub mod health;
pub mod logs;
pub mod ports;
pub mod process;
pub mod supervisor;

pub use process::SupervisedProcess;
pub use supervisor::{start_service, ServiceHandle};
