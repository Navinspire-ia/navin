//! Resource limits applied to supervised processes.
//!
//! Sprint 2 scope: address-space ceiling on Unix via rlimit, plus a wall
//! clock deadline enforced by the supervisor. Full namespace/cgroup
//! isolation (Linux) and Job Objects (Windows) arrive with later sprints;
//! the interface is what matters now.

use crate::policy::config::ResourceLimits;

#[derive(Debug, Clone, Copy)]
pub struct SandboxLimits {
    pub max_memory_mb: u64,
    pub max_runtime_secs: u64,
}

impl SandboxLimits {
    pub fn from_policy(limits: &ResourceLimits) -> Self {
        SandboxLimits {
            max_memory_mb: limits.max_memory_mb,
            max_runtime_secs: limits.max_runtime_minutes.saturating_mul(60),
        }
    }
}

/// Apply enforceable limits to a command before it spawns.
#[cfg(unix)]
pub fn apply(cmd: &mut std::process::Command, limits: SandboxLimits) {
    use std::os::unix::process::CommandExt;
    let bytes = limits.max_memory_mb.saturating_mul(1024 * 1024);
    if bytes == 0 {
        return;
    }
    // SAFETY: setrlimit is async-signal-safe and called in the child
    // before exec; it cannot touch parent state.
    unsafe {
        cmd.pre_exec(move || {
            let limit = libc::rlimit {
                rlim_cur: bytes as libc::rlim_t,
                rlim_max: bytes as libc::rlim_t,
            };
            libc::setrlimit(libc::RLIMIT_AS, &limit);
            Ok(())
        });
    }
}

#[cfg(not(unix))]
pub fn apply(_cmd: &mut std::process::Command, _limits: SandboxLimits) {
    // Windows Job Objects land with the Windows build of the engine.
}
