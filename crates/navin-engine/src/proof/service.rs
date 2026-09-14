//! Lifecycle wrapper around a supervised service for the Proof engine:
//! start, kill, restart and health-probe the app under test.

use anyhow::Result;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use crate::baseline::latency::{request_once, ProbeSpec};
use crate::runner::health::wait_for_port;
use crate::runner::process::SupervisedProcess;
use crate::runner::supervisor::start_service;
use crate::shadow::sandbox::SandboxLimits;

pub struct ServiceManager {
    pub start_cmd: String,
    pub work_dir: PathBuf,
    pub log_path: PathBuf,
    pub host: String,
    pub port: u16,
    /// Every request the probes rotate over (the first one doubles as the
    /// health check). Never empty.
    pub specs: Vec<ProbeSpec>,
    pub ready_timeout: Duration,
    pub limits: Option<SandboxLimits>,
    process: Option<SupervisedProcess>,
}

impl ServiceManager {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        start_cmd: String,
        work_dir: PathBuf,
        log_path: PathBuf,
        host: String,
        port: u16,
        specs: Vec<ProbeSpec>,
        ready_timeout: Duration,
        limits: Option<SandboxLimits>,
    ) -> Self {
        let specs = if specs.is_empty() { vec![ProbeSpec::get("/")] } else { specs };
        ServiceManager {
            start_cmd,
            work_dir,
            log_path,
            host,
            port,
            specs,
            ready_timeout,
            limits,
            process: None,
        }
    }

    /// Start the service and wait until it accepts connections; returns
    /// the observed startup duration.
    pub async fn start(&mut self) -> Result<Duration> {
        let handle = start_service(
            &self.start_cmd,
            &self.work_dir,
            &self.log_path,
            self.port,
            self.ready_timeout,
            self.limits,
        )
        .await?;
        let startup = handle.startup;
        self.process = Some(handle.process);
        Ok(startup)
    }

    pub fn pid(&self) -> Option<u32> {
        self.process.as_ref().map(|p| p.pid)
    }

    /// Hard-kill the running process tree (the fault under test).
    pub async fn kill(&mut self) -> Result<()> {
        if let Some(process) = self.process.take() {
            process.kill_tree().await?;
        }
        Ok(())
    }

    /// Restart after a kill, measuring time until healthy again.
    pub async fn restart(&mut self) -> Result<Duration> {
        self.kill().await.ok();
        self.start().await
    }

    /// A real HTTP request succeeds (stronger than a bare TCP accept).
    pub async fn is_healthy(&self) -> bool {
        request_once(&self.host, self.port, &self.specs[0]).await.is_ok()
    }

    /// Poll until healthy or the bound elapses; returns time taken.
    pub async fn wait_healthy(&self, bound: Duration) -> Option<Duration> {
        let started = Instant::now();
        // First make sure the port is back, then confirm HTTP works.
        wait_for_port(&self.host, self.port, bound).await?;
        while started.elapsed() < bound {
            if self.is_healthy().await {
                return Some(started.elapsed());
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        None
    }

    /// Stop the service for good (end of a proof run).
    pub async fn shutdown(mut self) {
        self.kill().await.ok();
    }
}
