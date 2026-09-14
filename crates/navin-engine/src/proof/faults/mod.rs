//! Faults injected into the running service. Each fault returns a
//! [`FaultOutcome`]; none of them touch anything outside the shadow.

pub mod flood;
pub mod kill;
pub mod load;
pub mod malformed;
pub mod netchaos;

use serde::{Deserialize, Serialize};

/// The catalogue of faults, selectable per profile.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FaultKind {
    /// Concurrent request storm well above the baseline load.
    Load,
    /// Hard-kill the process and require it to recover.
    KillRecovery,
    /// Garbage / oversized HTTP requests; must not crash the server.
    Malformed,
    /// Many half-open connections held at once; must not crash the server.
    ConnectionFlood,
    /// Degraded wire (added latency + connection resets) via a chaos proxy;
    /// the service must keep serving and be clean again afterwards.
    NetworkChaos,
}

impl FaultKind {
    pub fn as_str(self) -> &'static str {
        match self {
            FaultKind::Load => "load",
            FaultKind::KillRecovery => "kill_recovery",
            FaultKind::Malformed => "malformed",
            FaultKind::ConnectionFlood => "connection_flood",
            FaultKind::NetworkChaos => "network_chaos",
        }
    }
}
