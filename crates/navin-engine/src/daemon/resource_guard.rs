//! Engine self-limits from policy. The guard turns configured budgets into
//! values the rest of the daemon consults before starting work; OS-level
//! enforcement (cgroups, Job Objects) arrives with the sandbox sprint.

use serde::Serialize;

use crate::policy::config::ResourceLimits;

#[derive(Debug, Clone, Serialize)]
pub struct ResourceGuard {
    pub max_cpu_percent: u8,
    pub max_memory_mb: u64,
    pub max_disk_mb: u64,
    pub max_runtime_minutes: u64,
}

impl ResourceGuard {
    pub fn from_limits(limits: &ResourceLimits) -> Self {
        ResourceGuard {
            max_cpu_percent: limits.max_cpu_percent,
            max_memory_mb: limits.max_memory_mb,
            max_disk_mb: limits.max_disk_mb,
            max_runtime_minutes: limits.max_runtime_minutes,
        }
    }

    /// Ceiling for one run, in seconds, derived from policy.
    pub fn run_deadline_secs(&self) -> u64 {
        self.max_runtime_minutes.saturating_mul(60)
    }
}
