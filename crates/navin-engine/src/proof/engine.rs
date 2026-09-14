//! Proof orchestration: start the service in a shadow, inject each fault
//! in the chosen profile, collect the checks, and build a robustness report.

use anyhow::Result;
use serde_json::json;
use std::path::{Path, PathBuf};
use std::time::Duration;
use tracing::info;

use crate::baseline::latency::specs_for;
use crate::policy::config::EvolveConfig;
use crate::progress::ProgressSink;
use crate::runner::ports::parse_http_url;
use crate::shadow::sandbox::SandboxLimits;
use crate::shadow::worktree;

use super::faults::netchaos::ChaosConfig;
use super::faults::{flood, kill, load, malformed, netchaos, FaultKind};
use super::model::ProofReport;
use super::service::ServiceManager;
use super::worker::WorkerManager;

/// Placeholder URL for `[target] kind = "worker"`: a worker owns no port,
/// so there is nothing to parse or discover.
pub const WORKER_URL: &str = "worker://local";

/// What to prove and how hard. Durations scale with the profile.
#[derive(Debug, Clone)]
pub struct ProofPlan {
    pub profile: String,
    pub faults: Vec<FaultKind>,
    pub load_duration: Duration,
    pub load_concurrency: usize,
    pub max_error_ratio: f64,
    pub recovery_bound: Duration,
    pub flood_connections: usize,
    pub flood_hold: Duration,
    pub rss_limit_mb: u64,
    /// Extra latency the chaos proxy adds to every connection.
    pub chaos_delay: Duration,
    /// Every Nth proxied connection is reset (0 disables resets).
    pub chaos_reset_every: u64,
}

impl ProofPlan {
    /// Build a plan from a profile name (quick | standard | deep).
    pub fn for_profile(profile: &str, rss_limit_mb: u64) -> Self {
        let (faults, load_duration, load_concurrency, flood_connections): (
            Vec<FaultKind>,
            u64,
            usize,
            usize,
        ) = match profile {
            "quick" => (vec![FaultKind::Load, FaultKind::KillRecovery], 5, 16, 64),
            "deep" => (
                vec![
                    FaultKind::Load,
                    FaultKind::Malformed,
                    FaultKind::ConnectionFlood,
                    FaultKind::NetworkChaos,
                    FaultKind::KillRecovery,
                ],
                30,
                128,
                512,
            ),
            // "standard" and anything unknown fall back to a sensible mix.
            _ => (
                vec![
                    FaultKind::Load,
                    FaultKind::Malformed,
                    FaultKind::ConnectionFlood,
                    FaultKind::NetworkChaos,
                    FaultKind::KillRecovery,
                ],
                12,
                48,
                200,
            ),
        };
        ProofPlan {
            profile: profile.to_owned(),
            faults,
            load_duration: Duration::from_secs(load_duration),
            load_concurrency,
            max_error_ratio: 0.01,
            recovery_bound: Duration::from_secs(15),
            flood_connections,
            flood_hold: Duration::from_secs(3),
            rss_limit_mb,
            chaos_delay: Duration::from_millis(40),
            chaos_reset_every: 5,
        }
    }
}

/// Where the service lives during the proof (always inside a shadow).
#[derive(Debug, Clone)]
pub struct ProofTarget {
    pub start_cmd: String,
    pub url: String,
    pub work_dir: PathBuf,
    pub ready_timeout: Duration,
    pub limits: Option<SandboxLimits>,
}

/// Run the plan against the target and produce a report. `project_root`
/// is only used to resolve the commit SHA and (by the caller) to save.
pub async fn run_proof(
    project_root: &Path,
    target: &ProofTarget,
    plan: &ProofPlan,
    sink: &dyn ProgressSink,
) -> Result<ProofReport> {
    let commit = if worktree::is_git_repo(project_root) {
        worktree::head_sha(project_root)?
    } else {
        "workdir".to_owned()
    };
    let config = EvolveConfig::load(project_root).unwrap_or_default();
    if config.target.is_worker() || target.url == WORKER_URL {
        return run_worker_proof(project_root, target, plan, &config, &commit, sink).await;
    }
    let (host, port, path) = parse_http_url(&target.url)?;
    anyhow::ensure!(
        matches!(host.as_str(), "127.0.0.1" | "localhost" | "::1"),
        "proof probes are localhost-only, refusing {host}"
    );
    let specs = specs_for(&config.target, &path);

    let log_path = crate::engine_dir(project_root).join("logs").join("proof-service.log");
    let mut svc = ServiceManager::new(
        target.start_cmd.clone(),
        target.work_dir.clone(),
        log_path,
        host,
        port,
        specs,
        target.ready_timeout,
        target.limits,
    );

    info!("proof: starting service ({})", target.start_cmd);
    sink.emit("proof", "started", json!({ "profile": plan.profile, "faults": plan.faults.len() }));
    svc.start().await?;
    anyhow::ensure!(svc.is_healthy().await, "service did not answer an initial health probe");
    sink.emit("proof", "ready", json!({ "url": target.url }));

    let mut outcomes = Vec::new();
    for fault in &plan.faults {
        info!("proof: injecting {}", fault.as_str());
        sink.emit("proof", "fault_started", json!({ "fault": fault.as_str() }));
        let outcome = match fault {
            FaultKind::Load => {
                load::run(
                    &svc,
                    plan.load_duration,
                    plan.load_concurrency,
                    plan.max_error_ratio,
                    plan.rss_limit_mb,
                )
                .await
            }
            FaultKind::Malformed => malformed::run(&svc).await,
            FaultKind::ConnectionFlood => {
                flood::run(&svc, plan.flood_connections, plan.flood_hold).await
            }
            FaultKind::NetworkChaos => {
                let config = ChaosConfig {
                    delay: plan.chaos_delay,
                    reset_every: plan.chaos_reset_every,
                };
                netchaos::run(
                    &svc,
                    config,
                    // Half the load window: chaos is about degradation, not endurance.
                    plan.load_duration.div_f64(2.0).max(Duration::from_secs(3)),
                    plan.load_concurrency,
                    plan.max_error_ratio,
                )
                .await
            }
            FaultKind::KillRecovery => kill::run(&mut svc, plan.recovery_bound).await,
        };
        sink.emit(
            "proof",
            "fault_done",
            json!({ "fault": &outcome.fault, "verdict": outcome.verdict }),
        );
        outcomes.push(outcome);
    }

    svc.shutdown().await;
    let report = ProofReport::build(&commit, &plan.profile, &target.work_dir, outcomes, Vec::new());
    sink.emit(
        "proof",
        "completed",
        json!({ "verdict": report.verdict, "score": report.robustness_score }),
    );
    Ok(report)
}

/// Proof for a port-less worker (`[target] kind = "worker"`). Only the
/// faults that mean something without a socket run - load (through
/// `exercise_cmd`) and kill/recovery - and the wire-level faults are
/// recorded as skipped rather than silently dropped.
async fn run_worker_proof(
    project_root: &Path,
    target: &ProofTarget,
    plan: &ProofPlan,
    config: &EvolveConfig,
    commit: &str,
    sink: &dyn ProgressSink,
) -> Result<ProofReport> {
    let log_path = crate::engine_dir(project_root).join("logs").join("proof-service.log");
    let mut worker = WorkerManager::new(
        target.start_cmd.clone(),
        target.work_dir.clone(),
        log_path,
        config.target.health_cmd.clone(),
        target.ready_timeout,
        target.limits,
    );

    let applicable: Vec<FaultKind> = plan
        .faults
        .iter()
        .copied()
        .filter(|fault| matches!(fault, FaultKind::Load | FaultKind::KillRecovery))
        .collect();
    let mut notes = vec!["worker target: health = process + health_cmd, load = exercise_cmd".to_owned()];
    for fault in &plan.faults {
        if !applicable.contains(fault) {
            notes.push(format!(
                "fault `{}` skipped: it needs a network socket, which a worker does not have",
                fault.as_str()
            ));
        }
    }

    info!("proof(worker): starting ({})", target.start_cmd);
    sink.emit(
        "proof",
        "started",
        json!({ "profile": plan.profile, "faults": applicable.len(), "target": "worker" }),
    );
    worker.start().await?;
    anyhow::ensure!(worker.is_healthy().await, "worker did not pass its initial health check");
    sink.emit("proof", "ready", json!({ "url": WORKER_URL }));

    let mut outcomes = Vec::new();
    for fault in &applicable {
        info!("proof(worker): injecting {}", fault.as_str());
        sink.emit("proof", "fault_started", json!({ "fault": fault.as_str() }));
        let outcome = match fault {
            FaultKind::Load => {
                super::worker::run_load(
                    &mut worker,
                    &config.target.exercise_cmd,
                    plan.load_duration,
                    plan.load_concurrency,
                    plan.max_error_ratio,
                    plan.rss_limit_mb,
                )
                .await
            }
            _ => super::worker::run_kill_recovery(&mut worker, plan.recovery_bound).await,
        };
        sink.emit(
            "proof",
            "fault_done",
            json!({ "fault": &outcome.fault, "verdict": outcome.verdict }),
        );
        outcomes.push(outcome);
    }

    worker.shutdown().await;
    let report = ProofReport::build(commit, &plan.profile, &target.work_dir, outcomes, notes);
    sink.emit(
        "proof",
        "completed",
        json!({ "verdict": report.verdict, "score": report.robustness_score }),
    );
    Ok(report)
}

/// Full isolated proof: create a shadow, prove inside it, destroy it.
/// With `include_uncommitted`, the shadow carries the project's pending
/// (uncommitted) state, so a proposed fix is proved before merge.
#[allow(clippy::too_many_arguments)]
pub async fn run_proof_in_shadow(
    project_root: &Path,
    run_id: &str,
    start_cmd: &str,
    url: &str,
    plan: &ProofPlan,
    ready_timeout: Duration,
    limits: Option<SandboxLimits>,
    include_uncommitted: bool,
    sink: &dyn ProgressSink,
) -> Result<ProofReport> {
    let manager = crate::shadow::ShadowManager::new(project_root);
    let shadow = if include_uncommitted {
        manager.create_with_uncommitted(run_id)?
    } else {
        manager.create(run_id)?
    };
    let guard = crate::shadow::cleanup::CleanupGuard::new(shadow);
    let target = ProofTarget {
        start_cmd: start_cmd.to_owned(),
        url: url.to_owned(),
        work_dir: guard.path().to_path_buf(),
        ready_timeout,
        limits,
    };
    let report = run_proof(project_root, &target, plan, sink).await;
    guard.destroy()?;
    report
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::progress::RecordingSink;

    /// Even a zero-fault proof walks the whole progress sequence, so stream
    /// consumers can rely on started -> ready -> completed framing.
    #[tokio::test]
    async fn run_proof_emits_started_ready_completed() {
        let dir = tempfile::tempdir().unwrap();
        let port = crate::runner::ports::free_port().unwrap();
        let target = ProofTarget {
            start_cmd: format!("python3 -m http.server {port}"),
            url: format!("http://127.0.0.1:{port}/"),
            work_dir: dir.path().to_path_buf(),
            ready_timeout: Duration::from_secs(15),
            limits: None,
        };
        let plan = ProofPlan {
            profile: "test".to_owned(),
            faults: vec![],
            load_duration: Duration::from_secs(1),
            load_concurrency: 2,
            max_error_ratio: 0.01,
            recovery_bound: Duration::from_secs(5),
            flood_connections: 4,
            flood_hold: Duration::from_secs(1),
            rss_limit_mb: 512,
            chaos_delay: Duration::from_millis(10),
            chaos_reset_every: 5,
        };
        let sink = RecordingSink::new();
        let report = run_proof(dir.path(), &target, &plan, &sink).await.unwrap();
        assert_eq!(sink.labels(), vec!["proof.started", "proof.ready", "proof.completed"]);
        assert!(report.faults.is_empty());
    }
}
