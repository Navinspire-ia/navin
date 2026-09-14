//! Orchestrate one baseline measurement: build, start, probe, sample,
//! stop, report. Runs either in place or inside a shadow workspace.

use anyhow::Result;
use std::path::Path;
use std::time::{Duration, Instant};
use tracing::info;

use crate::policy::config::EvolveConfig;
use crate::runner::logs::tail;
use crate::runner::ports::parse_http_url;
use crate::runner::process::SupervisedProcess;
use crate::runner::supervisor::start_service;
use crate::shadow::sandbox::SandboxLimits;
use crate::shadow::worktree;

use super::cpu::CpuSampler;
use super::latency::{probe_specs, specs_for};
use super::memory::MemorySampler;
use super::report::BaselineReport;

#[derive(Debug, Clone, Default)]
pub struct BaselineOptions {
    pub build_cmd: Option<String>,
    pub start_cmd: Option<String>,
    /// Local URL to probe once the service is up (e.g. http://127.0.0.1:3000/).
    pub url: Option<String>,
    pub probe_duration: Duration,
    pub probe_concurrency: usize,
    pub ready_timeout: Duration,
    pub limits: Option<SandboxLimits>,
}

impl BaselineOptions {
    pub fn defaults() -> Self {
        BaselineOptions {
            probe_duration: Duration::from_secs(10),
            probe_concurrency: 4,
            ready_timeout: Duration::from_secs(60),
            ..Default::default()
        }
    }
}

/// Measure in `work_dir` and persist the report under `project_root`.
pub async fn collect_baseline(
    project_root: &Path,
    work_dir: &Path,
    opts: &BaselineOptions,
) -> Result<BaselineReport> {
    let commit = if worktree::is_git_repo(project_root) {
        worktree::head_sha(project_root)?
    } else {
        "workdir".to_owned()
    };
    let mut report = BaselineReport::new(&commit, work_dir);
    let logs_dir = crate::engine_dir(project_root).join("logs");

    if let Some(build_cmd) = &opts.build_cmd {
        info!("baseline: build ({build_cmd})");
        let log = logs_dir.join("baseline-build.log");
        let started = Instant::now();
        let process = SupervisedProcess::spawn(build_cmd, work_dir, &log, opts.limits)?;
        let code = process
            .wait_with_deadline(Duration::from_secs(30 * 60))
            .await?;
        anyhow::ensure!(
            code == 0,
            "build failed with code {code}\n--- log tail ---\n{}",
            tail(&log, 30)
        );
        report.build_ms = Some(started.elapsed().as_millis() as u64);
    } else {
        report.notes.push("build not measured: no build command".to_owned());
    }

    let Some(start_cmd) = &opts.start_cmd else {
        report.notes.push("startup/latency not measured: no start command".to_owned());
        report.save(project_root)?;
        return Ok(report);
    };

    let config = EvolveConfig::load(project_root).unwrap_or_default();
    if config.target.is_worker() {
        return collect_worker_baseline(project_root, work_dir, opts, &config, report, start_cmd)
            .await;
    }

    let Some(url) = &opts.url else {
        report.notes.push("startup/latency not measured: no probe URL".to_owned());
        report.save(project_root)?;
        return Ok(report);
    };
    let (host, port, path) = parse_http_url(url)?;
    anyhow::ensure!(
        matches!(host.as_str(), "127.0.0.1" | "localhost" | "::1"),
        "baseline probes are localhost-only, refusing {host}"
    );
    let specs = specs_for(&config.target, &path);

    info!("baseline: start ({start_cmd}) then probe {url} ({} routes)", specs.len());
    let log = logs_dir.join("baseline-service.log");
    let spawn_started = Instant::now();
    let handle = start_service(start_cmd, work_dir, &log, port, opts.ready_timeout, opts.limits).await?;
    report.startup_ms = Some(spawn_started.elapsed().as_millis() as u64);

    // Sample CPU/RSS in parallel with the latency probe.
    let pid = handle.process.pid;
    let sampling = tokio::spawn(sample_resources(pid, opts.probe_duration));
    let latency = probe_specs(&host, port, &specs, opts.probe_duration, opts.probe_concurrency).await;
    report.latency = Some(latency);
    if let Ok((cpu_avg, rss_peak)) = sampling.await {
        report.cpu_percent_avg = cpu_avg;
        report.rss_mb_peak = rss_peak.map(|bytes| bytes / (1024 * 1024));
    }

    handle.process.kill_tree().await?;
    report.save(project_root)?;
    Ok(report)
}

/// Baseline for a port-less worker: startup is the boot grace, "latency"
/// is the timed `exercise_cmd`, resources are sampled the same way.
async fn collect_worker_baseline(
    project_root: &Path,
    work_dir: &Path,
    opts: &BaselineOptions,
    config: &EvolveConfig,
    mut report: BaselineReport,
    start_cmd: &str,
) -> Result<BaselineReport> {
    use crate::proof::worker::{exercise_stats, WorkerManager};

    let log = crate::engine_dir(project_root).join("logs").join("baseline-service.log");
    let mut worker = WorkerManager::new(
        start_cmd.to_owned(),
        work_dir.to_path_buf(),
        log.clone(),
        config.target.health_cmd.clone(),
        opts.ready_timeout,
        opts.limits,
    );
    info!("baseline(worker): start ({start_cmd})");
    let startup = worker.start().await?;
    report.startup_ms = Some(startup.as_millis() as u64);
    anyhow::ensure!(worker.is_healthy().await, "worker did not pass its initial health check");

    if config.target.exercise_cmd.trim().is_empty() {
        report
            .notes
            .push("latency not measured: no [target] exercise_cmd declared".to_owned());
    } else {
        let pid = worker.pid().unwrap_or(0);
        let sampling = tokio::spawn(sample_resources(pid, opts.probe_duration));
        let latency = exercise_stats(
            &config.target.exercise_cmd,
            work_dir,
            &log,
            opts.probe_duration,
            opts.probe_concurrency,
        )
        .await;
        report.latency = Some(latency);
        if let Ok((cpu_avg, rss_peak)) = sampling.await {
            report.cpu_percent_avg = cpu_avg;
            report.rss_mb_peak = rss_peak.map(|bytes| bytes / (1024 * 1024));
        }
    }

    worker.shutdown().await;
    report.save(project_root)?;
    Ok(report)
}

/// Full isolated measurement: create a shadow, measure inside it, destroy
/// it whatever happens (guard covers early errors and panics).
pub async fn collect_in_shadow(
    project_root: &Path,
    run_id: &str,
    opts: &BaselineOptions,
) -> Result<BaselineReport> {
    let manager = crate::shadow::ShadowManager::new(project_root);
    let guard = crate::shadow::cleanup::CleanupGuard::new(manager.create(run_id)?);
    let report = collect_baseline(project_root, guard.path(), opts).await;
    guard.destroy()?;
    report
}

async fn sample_resources(pid: u32, duration: Duration) -> (Option<f32>, Option<u64>) {
    let mut cpu = CpuSampler::new(pid);
    let mut memory = MemorySampler::new(pid);
    // Prime the CPU counter: the first reading is always zero.
    cpu.sample();
    let deadline = Instant::now() + duration;
    let mut cpu_samples = Vec::new();
    let mut rss_peak: Option<u64> = None;
    while Instant::now() < deadline {
        tokio::time::sleep(Duration::from_millis(500)).await;
        if let Some(usage) = cpu.sample() {
            cpu_samples.push(usage);
        }
        if let Some(rss) = memory.sample() {
            rss_peak = Some(rss_peak.map_or(rss, |peak| peak.max(rss)));
        }
    }
    let cpu_avg = if cpu_samples.is_empty() {
        None
    } else {
        Some(cpu_samples.iter().sum::<f32>() / cpu_samples.len() as f32)
    };
    (cpu_avg, rss_peak)
}
