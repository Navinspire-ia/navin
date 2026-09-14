//! CPU sampling of the process under test (and its children's leader).

use sysinfo::{Pid, ProcessesToUpdate, System};

pub struct CpuSampler {
    system: System,
    pid: Pid,
}

impl CpuSampler {
    pub fn new(pid: u32) -> Self {
        CpuSampler { system: System::new(), pid: Pid::from_u32(pid) }
    }

    /// Current CPU usage in percent; needs two samples spaced apart to be
    /// meaningful, which the collector's sampling loop provides.
    pub fn sample(&mut self) -> Option<f32> {
        self.system
            .refresh_processes(ProcessesToUpdate::Some(&[self.pid]), true);
        self.system.process(self.pid).map(|p| p.cpu_usage())
    }
}
