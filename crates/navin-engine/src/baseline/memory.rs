//! RSS sampling of the process under test.

use sysinfo::{Pid, ProcessesToUpdate, System};

pub struct MemorySampler {
    system: System,
    pid: Pid,
}

impl MemorySampler {
    pub fn new(pid: u32) -> Self {
        MemorySampler { system: System::new(), pid: Pid::from_u32(pid) }
    }

    /// Resident set size in bytes.
    pub fn sample(&mut self) -> Option<u64> {
        self.system
            .refresh_processes(ProcessesToUpdate::Some(&[self.pid]), true);
        self.system.process(self.pid).map(|p| p.memory())
    }
}
