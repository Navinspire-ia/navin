//! Start a service and hand back a handle once it is actually ready.

use anyhow::Result;
use std::path::Path;
use std::time::Duration;

use crate::shadow::sandbox::SandboxLimits;

use super::health::wait_for_port;
use super::logs::tail;
use super::process::SupervisedProcess;

pub struct ServiceHandle {
    pub process: SupervisedProcess,
    pub port: u16,
    pub startup: Duration,
}

/// Spawn `start_cmd` in `dir` and wait until `port` accepts connections.
/// An early exit or a readiness timeout returns the log tail as evidence.
pub async fn start_service(
    start_cmd: &str,
    dir: &Path,
    log_path: &Path,
    port: u16,
    ready_timeout: Duration,
    limits: Option<SandboxLimits>,
) -> Result<ServiceHandle> {
    let mut process = SupervisedProcess::spawn(start_cmd, dir, log_path, limits)?;

    let deadline = tokio::time::Instant::now() + ready_timeout;
    loop {
        if let Some(code) = process.try_exit_code()? {
            anyhow::bail!(
                "service exited with code {code} before becoming ready\n--- log tail ---\n{}",
                tail(log_path, 30)
            );
        }
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            let evidence = tail(log_path, 30);
            process.kill_tree().await.ok();
            anyhow::bail!(
                "service did not open port {port} within {}s\n--- log tail ---\n{evidence}",
                ready_timeout.as_secs()
            );
        }
        let slice = remaining.min(Duration::from_millis(500));
        if let Some(startup) = wait_for_port("127.0.0.1", port, slice).await {
            // startup here is only the tail of the wait; the caller measures
            // total startup from spawn. Recompute from the deadline instead.
            let elapsed = ready_timeout.saturating_sub(remaining) + startup;
            return Ok(ServiceHandle { process, port, startup: elapsed });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn a_crashing_service_reports_its_log_tail() {
        let tmp = tempfile::tempdir().unwrap();
        let log = tmp.path().join("svc.log");
        let result = start_service(
            "echo boot-failure && exit 3",
            tmp.path(),
            &log,
            crate::runner::ports::free_port().unwrap(),
            Duration::from_secs(5),
            None,
        )
        .await;
        let err = match result {
            Ok(_) => panic!("service unexpectedly became ready"),
            Err(err) => err,
        };
        let message = format!("{err:#}");
        assert!(message.contains("exited with code 3"), "{message}");
        assert!(message.contains("boot-failure"), "{message}");
    }
}
