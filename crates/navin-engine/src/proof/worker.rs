//! Proof support for port-less targets: queue consumers, cron daemons,
//! CLIs wrapped in a loop - anything `[target] kind = "worker"` names.
//!
//! Health is process liveness plus an optional `health_cmd` (exit 0 means
//! healthy). Load is `exercise_cmd` run concurrently, each invocation
//! timed like an HTTP request, which is what makes a worker measurable.

use anyhow::Result;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use crate::baseline::latency::{percentile, LatencyStats};
use crate::baseline::memory::MemorySampler;
use crate::runner::logs::tail;
use crate::runner::process::SupervisedProcess;
use crate::shadow::sandbox::SandboxLimits;

use super::checks::{error_rate, no_crash, recovery, resource_bound};
use super::model::{CheckResult, FaultOutcome, Verdict};

/// How long one health command may run before it counts as unhealthy.
const HEALTH_DEADLINE: Duration = Duration::from_secs(30);
/// How long one exercise invocation may run before it counts as a failure.
const EXERCISE_DEADLINE: Duration = Duration::from_secs(60);
/// A worker is "ready" when it is still alive after this grace period
/// (capped by the caller's ready timeout): there is no port to observe.
const START_GRACE: Duration = Duration::from_secs(3);

/// Lifecycle wrapper around a supervised worker process.
pub struct WorkerManager {
    pub start_cmd: String,
    pub work_dir: PathBuf,
    pub log_path: PathBuf,
    /// Exit 0 means healthy; empty means liveness alone decides.
    pub health_cmd: String,
    pub ready_timeout: Duration,
    pub limits: Option<SandboxLimits>,
    process: Option<SupervisedProcess>,
}

impl WorkerManager {
    pub fn new(
        start_cmd: String,
        work_dir: PathBuf,
        log_path: PathBuf,
        health_cmd: String,
        ready_timeout: Duration,
        limits: Option<SandboxLimits>,
    ) -> Self {
        WorkerManager {
            start_cmd,
            work_dir,
            log_path,
            health_cmd,
            ready_timeout,
            limits,
            process: None,
        }
    }

    /// Spawn the worker and wait out a short grace period; an early exit is
    /// a startup failure carrying the log tail as evidence.
    pub async fn start(&mut self) -> Result<Duration> {
        let started = Instant::now();
        let mut process =
            SupervisedProcess::spawn(&self.start_cmd, &self.work_dir, &self.log_path, self.limits)?;
        let grace = START_GRACE.min(self.ready_timeout);
        let deadline = Instant::now() + grace;
        while Instant::now() < deadline {
            if let Some(code) = process.try_exit_code()? {
                anyhow::bail!(
                    "worker exited with code {code} right after start\n--- log tail ---\n{}",
                    tail(&self.log_path, 30)
                );
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        self.process = Some(process);
        Ok(started.elapsed())
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

    pub async fn restart(&mut self) -> Result<Duration> {
        self.kill().await.ok();
        self.start().await
    }

    /// Alive, and the health command (when declared) exits 0.
    pub async fn is_healthy(&mut self) -> bool {
        let alive = match self.process.as_mut() {
            Some(process) => matches!(process.try_exit_code(), Ok(None)),
            None => false,
        };
        if !alive {
            return false;
        }
        if self.health_cmd.trim().is_empty() {
            return true;
        }
        run_ok(&self.health_cmd, &self.work_dir, &self.log_path, HEALTH_DEADLINE).await
    }

    /// Poll until healthy or the bound elapses; returns time taken.
    pub async fn wait_healthy(&mut self, bound: Duration) -> Option<Duration> {
        let started = Instant::now();
        while started.elapsed() < bound {
            if self.is_healthy().await {
                return Some(started.elapsed());
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        None
    }

    pub async fn shutdown(mut self) {
        self.kill().await.ok();
    }
}

/// Run one shell command to completion next to the worker's log; true when
/// it exits 0 within the deadline.
async fn run_ok(cmd: &str, dir: &Path, worker_log: &Path, deadline: Duration) -> bool {
    let log = worker_log.with_extension("cmd.log");
    match SupervisedProcess::spawn(cmd, dir, &log, None) {
        Ok(process) => matches!(process.wait_with_deadline(deadline).await, Ok(0)),
        Err(_) => false,
    }
}

/// Run `exercise_cmd` in `concurrency` parallel loops until `duration`
/// elapses, timing every invocation. The same shape as an HTTP benchmark,
/// so the optimize statistics apply unchanged.
pub async fn exercise_stats(
    exercise_cmd: &str,
    work_dir: &Path,
    log_path: &Path,
    duration: Duration,
    concurrency: usize,
) -> LatencyStats {
    let deadline = Instant::now() + duration;
    let mut tasks = Vec::new();
    // Each invocation is a whole process: cap the parallelism so the load
    // measures the worker, not the operating system's fork throughput.
    for _ in 0..concurrency.clamp(1, 8) {
        let cmd = exercise_cmd.to_owned();
        let dir = work_dir.to_path_buf();
        let log = log_path.with_extension("exercise.log");
        tasks.push(tokio::spawn(async move {
            let mut samples = Vec::new();
            let mut failures = 0u64;
            while Instant::now() < deadline {
                let started = Instant::now();
                if run_ok(&cmd, &dir, &log, EXERCISE_DEADLINE).await {
                    samples.push(started.elapsed().as_secs_f64() * 1000.0);
                } else {
                    failures += 1;
                }
            }
            (samples, failures)
        }));
    }

    let mut all = Vec::new();
    let mut failures = 0u64;
    for task in tasks {
        if let Ok((samples, failed)) = task.await {
            all.extend(samples);
            failures += failed;
        }
    }
    all.sort_by(|a, b| a.partial_cmp(b).expect("durations are finite"));
    let requests = all.len() as u64;
    LatencyStats {
        requests,
        failures,
        p50_ms: round1(percentile(&all, 0.50)),
        p95_ms: round1(percentile(&all, 0.95)),
        p99_ms: round1(percentile(&all, 0.99)),
        rps: round1(requests as f64 / duration.as_secs_f64().max(0.001)),
    }
}

/// Load fault for a worker: exercise it concurrently (when an exercise
/// command exists), watch its memory, and require it to stay healthy.
pub async fn run_load(
    manager: &mut WorkerManager,
    exercise_cmd: &str,
    duration: Duration,
    concurrency: usize,
    max_error_ratio: f64,
    rss_limit_mb: u64,
) -> FaultOutcome {
    let pid = manager.pid();
    let deadline = Instant::now() + duration;
    let sampler = pid.map(|pid| {
        tokio::spawn(async move {
            let mut memory = MemorySampler::new(pid);
            let mut peak = 0u64;
            while Instant::now() < deadline {
                if let Some(rss) = memory.sample() {
                    peak = peak.max(rss);
                }
                tokio::time::sleep(Duration::from_millis(250)).await;
            }
            peak / (1024 * 1024)
        })
    });

    let stats = if exercise_cmd.trim().is_empty() {
        tokio::time::sleep(duration).await;
        None
    } else {
        Some(
            exercise_stats(exercise_cmd, &manager.work_dir, &manager.log_path, duration, concurrency)
                .await,
        )
    };
    let rss_peak_mb = match sampler {
        Some(handle) => handle.await.unwrap_or(0),
        None => 0,
    };
    let alive = manager.is_healthy().await;

    let mut checks = vec![no_crash(alive)];
    let mut evidence = Vec::new();
    match &stats {
        Some(stats) => {
            let total = stats.requests + stats.failures;
            checks.push(error_rate(stats.failures, total, max_error_ratio));
            evidence.push(format!(
                "{} runs, p95 {} ms, p99 {} ms, {} runs/s",
                stats.requests, stats.p95_ms, stats.p99_ms, stats.rps
            ));
        }
        None => evidence.push(
            "no exercise_cmd configured: observed idle stability only ([target] exercise_cmd unlocks a measured load)"
                .to_owned(),
        ),
    }
    checks.push(resource_bound(rss_peak_mb, rss_limit_mb));

    let description = if exercise_cmd.trim().is_empty() {
        format!("watched for {}s without exercising", duration.as_secs())
    } else {
        format!(
            "{} concurrent exercise loops for {}s",
            concurrency.clamp(1, 8),
            duration.as_secs()
        )
    };
    FaultOutcome::new("load", description, checks).with_evidence(evidence)
}

/// Kill/recovery fault for a worker: hard-kill the tree, restart, and
/// require health again within the bound.
pub async fn run_kill_recovery(manager: &mut WorkerManager, bound: Duration) -> FaultOutcome {
    if let Err(err) = manager.kill().await {
        return FaultOutcome::new(
            "kill_recovery",
            "hard-kill then restart",
            vec![CheckResult::new(
                "kill",
                Verdict::Fail,
                format!("could not kill the worker: {err:#}"),
            )],
        );
    }
    let restart = match manager.restart().await {
        Ok(_) => manager.wait_healthy(bound).await,
        Err(err) => {
            return FaultOutcome::new(
                "kill_recovery",
                "hard-kill then restart",
                vec![CheckResult::new(
                    "recovery",
                    Verdict::Fail,
                    format!("restart failed: {err:#}"),
                )],
            );
        }
    };
    let (recovered, secs) = match restart {
        Some(elapsed) => (true, elapsed.as_secs_f64()),
        None => (false, bound.as_secs_f64()),
    };
    FaultOutcome::new(
        "kill_recovery",
        "hard-kill then restart",
        vec![recovery(recovered, secs, bound.as_secs_f64())],
    )
}

fn round1(v: f64) -> f64 {
    (v * 10.0).round() / 10.0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn manager(dir: &Path, start: &str, health: &str) -> WorkerManager {
        WorkerManager::new(
            start.to_owned(),
            dir.to_path_buf(),
            dir.join("worker.log"),
            health.to_owned(),
            Duration::from_secs(1),
            None,
        )
    }

    #[tokio::test]
    async fn a_living_worker_is_healthy_without_a_health_cmd() {
        let tmp = tempfile::tempdir().unwrap();
        let mut worker = manager(tmp.path(), "sleep 30", "");
        worker.start().await.unwrap();
        assert!(worker.is_healthy().await);
        worker.shutdown().await;
    }

    #[tokio::test]
    async fn a_worker_that_dies_at_boot_reports_its_log() {
        let tmp = tempfile::tempdir().unwrap();
        let mut worker = manager(tmp.path(), "echo boom && exit 7", "");
        let err = worker.start().await.unwrap_err();
        let message = format!("{err:#}");
        assert!(message.contains("exited with code 7"), "{message}");
        assert!(message.contains("boom"), "{message}");
    }

    #[tokio::test]
    async fn a_failing_health_cmd_makes_the_worker_unhealthy() {
        let tmp = tempfile::tempdir().unwrap();
        let mut worker = manager(tmp.path(), "sleep 30", "exit 1");
        worker.start().await.unwrap();
        assert!(!worker.is_healthy().await);
        worker.shutdown().await;
    }

    #[tokio::test]
    async fn exercise_stats_time_every_invocation() {
        let tmp = tempfile::tempdir().unwrap();
        let stats = exercise_stats(
            "true",
            tmp.path(),
            &tmp.path().join("worker.log"),
            Duration::from_millis(600),
            2,
        )
        .await;
        assert!(stats.requests > 0, "no completed runs");
        assert_eq!(stats.failures, 0);
        assert!(stats.p95_ms >= stats.p50_ms);
    }

    #[tokio::test]
    async fn kill_recovery_brings_a_worker_back() {
        let tmp = tempfile::tempdir().unwrap();
        let mut worker = manager(tmp.path(), "sleep 30", "");
        worker.start().await.unwrap();
        let outcome = run_kill_recovery(&mut worker, Duration::from_secs(10)).await;
        assert_eq!(outcome.verdict, Verdict::Pass, "{outcome:?}");
        worker.shutdown().await;
    }
}
