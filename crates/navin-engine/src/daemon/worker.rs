//! Drains the scheduler queue. Sprint 1 knows one real job kind
//! (`project.inspect`); proof and evolve campaigns plug in here later.

use anyhow::{Context, Result};
use serde_json::json;
use std::path::{Path, PathBuf};
use tokio::sync::mpsc;
use tracing::{info, warn};

use crate::fix::{FixCandidate, FixReport};
use crate::ipc::events::EventBus;
use crate::ipc::protocol::Event;
use crate::policy::config::EvolveConfig;
use crate::progress::ProgressSink;
use crate::project::inspect_project;
use crate::promote::{self, PromotionRecord};

use super::scheduler::{Job, JobState, Scheduler};

/// Bridges engine progress onto the daemon event bus. Every event is tagged
/// with the job id so an IPC client can follow several runs at once.
struct BusSink {
    bus: EventBus,
    job: u64,
    kind: String,
}

impl ProgressSink for BusSink {
    fn emit(&self, stage: &str, event: &str, data: serde_json::Value) {
        self.bus.publish(Event::new(
            "run.progress",
            json!({
                "job": self.job,
                "kind": self.kind,
                "stage": stage,
                "event": event,
                "data": data,
            }),
        ));
    }
}

/// Start command and probe URL for a job. Both parameters are optional in
/// the protocol: what the caller omits, the engine works out from the
/// project itself, so a campaign can be launched with a path alone.
async fn job_target(
    root: &Path,
    params: &serde_json::Value,
    sink: &dyn ProgressSink,
) -> Result<crate::target::Target, String> {
    let field = |name: &str| params.get(name).and_then(|v| v.as_str()).map(String::from);
    crate::target::resolve(root, field("start"), field("url"), sink)
        .await
        .map_err(|e| format!("{e:#}"))
}

/// Build the candidate generator: the project policy names the bridge when
/// it wants a specific one, otherwise the caller's bridge is used. A desktop
/// app can therefore offer optimize and evolve out of the box, while
/// `.navin/evolve.toml` still has the last word on a project that pins one.
fn make_generator(
    config: &EvolveConfig,
    preset: Option<String>,
    offered: Option<String>,
    start_command: Option<String>,
) -> Box<dyn crate::fix::FixGenerator> {
    let configured = config.evolve.generator.command.trim();
    let command = if configured.is_empty() {
        offered.map(|c| c.trim().to_owned()).filter(|c| !c.is_empty())
    } else {
        Some(configured.to_owned())
    };
    match command {
        None => Box::new(crate::fix::ProvidedPatchGenerator::new(Vec::new())),
        Some(command) => Box::new(
            crate::fix::BridgeGenerator::new(
                command,
                std::time::Duration::from_secs(config.evolve.generator.timeout_secs),
            )
            .with_preset(preset)
            .about_app(start_command),
        ),
    }
}

/// The bridge a caller offers for this job, if any.
fn offered_generator(params: &serde_json::Value) -> Option<String> {
    params.get("generator").and_then(|v| v.as_str()).map(String::from)
}

/// Run the full evolve pipeline as a daemon job, with the generator the
/// policy pins or the one the caller offered.
// One job, one long parameter list: the alternative is a struct that exists
// only to be destructured at the single call site.
#[allow(clippy::too_many_arguments)]
async fn run_evolve_job(
    root: &Path,
    start: String,
    url: String,
    profile: String,
    max_findings: usize,
    test: Option<String>,
    preset: Option<String>,
    params: &serde_json::Value,
    sink: &dyn ProgressSink,
) -> Result<crate::evolve::EvolveReport> {
    let config = EvolveConfig::load(root)?;
    // Fall back to the manifest's test command, like the CLI does.
    let test_cmd = test.or_else(|| {
        inspect_project(root)
            .ok()
            .and_then(|m| m.units.first().and_then(|u| u.commands.test.clone()))
    });
    let generator =
        make_generator(&config, preset, offered_generator(params), Some(start.clone()));
    let ctx = crate::evolve::EvolveContext {
        start_cmd: start,
        url,
        profile,
        ready_timeout: std::time::Duration::from_secs(60),
        limits: None,
        max_findings,
        test_cmd,
    };
    crate::evolve::run_evolve(root, &ctx, generator.as_ref(), &config, sink).await
}

/// Run an optimization campaign as a daemon job.
async fn run_optimize_job(
    root: &Path,
    start: String,
    url: String,
    objective: String,
    test: Option<String>,
    params: &serde_json::Value,
    sink: &dyn ProgressSink,
) -> Result<crate::optimize::OptimizeReport> {
    let config = EvolveConfig::load(root)?;
    let preset = params
        .get("preset")
        .and_then(|v| v.as_str())
        .map(String::from);
    let generator =
        make_generator(&config, preset, offered_generator(params), Some(start.clone()));
    let test_cmd = test.or_else(|| {
        inspect_project(root)
            .ok()
            .and_then(|m| m.units.first().and_then(|u| u.commands.test.clone()))
    });
    let ctx = crate::optimize::OptimizeContext {
        start_cmd: start,
        url,
        ready_timeout: std::time::Duration::from_secs(60),
        limits: None,
        test_cmd,
        bench_duration: std::time::Duration::from_secs(
            params.get("duration").and_then(|v| v.as_u64()).unwrap_or(10),
        ),
        bench_concurrency: params
            .get("concurrency")
            .and_then(|v| v.as_u64())
            .unwrap_or(16) as usize,
        bench_repeats: params.get("repeats").and_then(|v| v.as_u64()).unwrap_or(3) as usize,
        max_variants: params.get("max_variants").and_then(|v| v.as_u64()).unwrap_or(4) as usize,
        min_gain_percent: params.get("min_gain").and_then(|v| v.as_f64()).unwrap_or(5.0),
        objective: crate::optimize::Objective::parse(&objective)?,
        diff_vectors: params.get("diff_vectors").and_then(|v| v.as_u64()).unwrap_or(24) as usize,
    };
    crate::optimize::run_optimize(root, &ctx, generator.as_ref(), &config, sink).await
}

/// Load the HEAD fix report and its accepted proposal, then promote it.
/// Without git the report is keyed "workdir" and the promotion lands in a
/// patch bundle instead of a branch.
fn promote_from_disk(root: &Path, finding: &str) -> Result<PromotionRecord> {
    let config = EvolveConfig::load(root)?;
    let commit = promote::git::head_sha(root).unwrap_or_else(|_| "workdir".to_owned());
    let report_path = root.join(".navin/fixes").join(format!("{commit}.json"));
    let report: FixReport = serde_json::from_str(
        &std::fs::read_to_string(&report_path)
            .with_context(|| format!("no fix report at {}", report_path.display()))?,
    )?;
    anyhow::ensure!(
        report.target_finding == finding,
        "fix report targets `{}`, not `{finding}`",
        report.target_finding
    );
    let accepted = report
        .accepted
        .clone()
        .context("fix report has no accepted candidate")?;
    let proposal_path = root
        .join(".navin/fixes/proposals")
        .join(format!("{accepted}.json"));
    let candidate: FixCandidate = serde_json::from_str(
        &std::fs::read_to_string(&proposal_path)
            .with_context(|| format!("no proposal at {}", proposal_path.display()))?,
    )?;
    promote::promote(root, &report, &candidate, &config)
}

pub async fn run_worker(
    mut rx: mpsc::Receiver<Job>,
    scheduler: Scheduler,
    events: EventBus,
    mut shutdown: tokio::sync::watch::Receiver<bool>,
) {
    loop {
        tokio::select! {
            _ = shutdown.changed() => {
                if *shutdown.borrow() {
                    return;
                }
            }
            job = rx.recv() => {
                let Some(job) = job else { return };
                execute(job, &scheduler, &events).await;
            }
        }
    }
}

async fn execute(job: Job, scheduler: &Scheduler, events: &EventBus) {
    // Called off while it waited its turn: nothing was started, nothing to
    // undo.
    if scheduler.is_cancelled(job.id) {
        info!("job {} ({}) cancelled before it started", job.id, job.kind);
        events.publish(Event::new(
            "run.cancelled",
            json!({ "job": job.id, "kind": job.kind, "phase": "queued" }),
        ));
        scheduler.forget_stop(job.id);
        return;
    }

    scheduler.set_state(job.id, JobState::Running, None);
    events.publish(Event::new("run.started", json!({ "job": job.id, "kind": job.kind })));
    let sink = BusSink { bus: events.clone(), job: job.id, kind: job.kind.clone() };

    // Dropping the campaign future is what stops it: supervised processes
    // kill their group on drop and shadow guards destroy their worktree, so
    // an operator's stop leaves no server running and no shadow behind.
    let mut stop = scheduler.stop_signal(job.id);
    let outcome = tokio::select! {
        _ = wait_for_stop(&mut stop) => {
            warn!("job {} ({}) cancelled while running", job.id, job.kind);
            scheduler.set_state(job.id, JobState::Cancelled, Some("stopped on request".into()));
            events.publish(Event::new(
                "run.cancelled",
                json!({ "job": job.id, "kind": job.kind, "phase": "running" }),
            ));
            scheduler.forget_stop(job.id);
            return;
        }
        outcome = run_job(&job, &sink) => outcome,
    };
    scheduler.forget_stop(job.id);

    match outcome {
        Ok(result) => {
            info!("job {} ({}) completed", job.id, job.kind);
            scheduler.set_state(job.id, JobState::Completed, None);
            events.publish(Event::new(
                "run.completed",
                json!({ "job": job.id, "kind": job.kind, "result": result }),
            ));
        }
        Err(message) => {
            warn!("job {} ({}) failed: {message}", job.id, job.kind);
            scheduler.set_state(job.id, JobState::Failed, Some(message.clone()));
            events.publish(Event::new(
                "run.failed",
                json!({ "job": job.id, "kind": job.kind, "error": message }),
            ));
        }
    }
}

/// Resolve once the stop switch is flipped, and never if the sender is gone.
async fn wait_for_stop(stop: &mut tokio::sync::watch::Receiver<bool>) {
    if *stop.borrow() {
        return;
    }
    while stop.changed().await.is_ok() {
        if *stop.borrow() {
            return;
        }
    }
    std::future::pending::<()>().await
}

/// The work itself. Held as a future so a cancellation can drop it.
async fn run_job(job: &Job, sink: &BusSink) -> Result<serde_json::Value, String> {
    match job.kind.as_str() {
        "project.inspect" => {
            let root = job
                .params
                .get("path")
                .and_then(|v| v.as_str())
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("."));
            // Discovery reads many small files; keep the runtime responsive.
            tokio::task::spawn_blocking(move || inspect_project(&root))
                .await
                .map_err(|e| e.to_string())
                .and_then(|res| res.map_err(|e| format!("{e:#}")))
                .map(|manifest| serde_json::to_value(manifest).unwrap_or_default())
        }
        "baseline.run" => {
            let root = job
                .params
                .get("path")
                .and_then(|v| v.as_str())
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("."));
            let mut opts = crate::baseline::BaselineOptions::defaults();
            opts.build_cmd = job.params.get("build").and_then(|v| v.as_str()).map(String::from);
            // A baseline still means something without a running app, so a
            // target we cannot resolve degrades instead of failing.
            if let Ok(target) = job_target(&root, &job.params, sink).await {
                opts.start_cmd = Some(target.start_cmd);
                opts.url = Some(target.url);
            }
            if let Some(secs) = job.params.get("duration").and_then(|v| v.as_u64()) {
                opts.probe_duration = std::time::Duration::from_secs(secs);
            }
            let run_id = format!("baseline-{}", job.id);
            crate::baseline::collector::collect_in_shadow(&root, &run_id, &opts)
                .await
                .map_err(|e| format!("{e:#}"))
                .map(|report| serde_json::to_value(report).unwrap_or_default())
        }
        "proof.run" => {
            let root = job
                .params
                .get("path")
                .and_then(|v| v.as_str())
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("."));
            let profile = job
                .params
                .get("profile")
                .and_then(|v| v.as_str())
                .unwrap_or("standard")
                .to_owned();
            // `dirty` proves the working tree as it stands (pending fixes
            // included) instead of the last commit: "prove this change".
            let dirty = job.params.get("dirty").and_then(|v| v.as_bool()).unwrap_or(false);
            match job_target(&root, &job.params, sink).await {
                Ok(target) => {
                    let plan = crate::proof::ProofPlan::for_profile(&profile, 512);
                    let run_id = format!("proof-{}", job.id);
                    crate::proof::run_proof_in_shadow(
                        &root,
                        &run_id,
                        &target.start_cmd,
                        &target.url,
                        &plan,
                        std::time::Duration::from_secs(60),
                        None,
                        dirty,
                        sink,
                    )
                    .await
                    .and_then(|report| {
                        report.save(&root)?;
                        Ok(report)
                    })
                    .map_err(|e| format!("{e:#}"))
                    .map(|report| serde_json::to_value(report).unwrap_or_default())
                }
                Err(message) => Err(message),
            }
        }
        "diagnose.run" => {
            let root = job
                .params
                .get("path")
                .and_then(|v| v.as_str())
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("."));
            let profile = job
                .params
                .get("profile")
                .and_then(|v| v.as_str())
                .unwrap_or("standard")
                .to_owned();
            match job_target(&root, &job.params, sink).await {
                Ok(target) => {
                    let plan = crate::proof::ProofPlan::for_profile(&profile, 512);
                    let run_id = format!("diagnose-{}", job.id);
                    crate::proof::run_proof_in_shadow(
                        &root,
                        &run_id,
                        &target.start_cmd,
                        &target.url,
                        &plan,
                        std::time::Duration::from_secs(60),
                        None,
                        false,
                        sink,
                    )
                    .await
                    .and_then(|report| {
                        report.save(&root)?;
                        let diagnosis = crate::diagnose::diagnose_project(&root, &report);
                        diagnosis.save(&root)?;
                        Ok(diagnosis)
                    })
                    .map_err(|e| format!("{e:#}"))
                    .map(|diagnosis| serde_json::to_value(diagnosis).unwrap_or_default())
                }
                Err(message) => Err(message),
            }
        }
        "fix.run" => {
            let root = job
                .params
                .get("path")
                .and_then(|v| v.as_str())
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("."));
            let finding = job.params.get("finding").and_then(|v| v.as_str()).map(String::from);
            let profile = job
                .params
                .get("profile")
                .and_then(|v| v.as_str())
                .unwrap_or("quick")
                .to_owned();
            let candidates: Result<Vec<crate::fix::FixCandidate>, String> = job
                .params
                .get("candidates")
                .cloned()
                .map(|v| serde_json::from_value(v).map_err(|e| format!("invalid candidates: {e}")))
                .unwrap_or_else(|| Ok(Vec::new()));
            let target = match finding.is_some() {
                true => Some(job_target(&root, &job.params, sink).await),
                false => None,
            };
            match (target, finding, candidates) {
                (Some(Ok(target)), Some(finding), Ok(candidates)) => {
                    let test_cmd =
                        job.params.get("test").and_then(|v| v.as_str()).map(String::from);
                    let ctx = crate::fix::FixContext {
                        start_cmd: target.start_cmd,
                        url: target.url,
                        plan: crate::proof::ProofPlan::for_profile(&profile, 512),
                        ready_timeout: std::time::Duration::from_secs(60),
                        limits: None,
                        test_cmd,
                        invariants: EvolveConfig::load(&root)
                            .map(|c| c.invariants)
                            .unwrap_or_default(),
                    };
                    let generator = crate::fix::ProvidedPatchGenerator::new(candidates);
                    crate::fix::run_fix(
                        &root,
                        &ctx,
                        &finding,
                        &generator,
                        &crate::fix::GateConfig::default(),
                        sink,
                    )
                    .await
                        .and_then(|report| {
                            report.save(&root)?;
                            Ok(report)
                        })
                        .map_err(|e| format!("{e:#}"))
                        .map(|report| serde_json::to_value(report).unwrap_or_default())
                }
                (_, _, Err(message)) | (Some(Err(message)), _, _) => Err(message),
                _ => Err("fix.run requires a `finding`".to_owned()),
            }
        }
        "evolve.run" => {
            let root = job
                .params
                .get("path")
                .and_then(|v| v.as_str())
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("."));
            let profile = job
                .params
                .get("profile")
                .and_then(|v| v.as_str())
                .unwrap_or("quick")
                .to_owned();
            let max_findings = job
                .params
                .get("max_findings")
                .and_then(|v| v.as_u64())
                .unwrap_or(3) as usize;
            let test = job.params.get("test").and_then(|v| v.as_str()).map(String::from);
            let preset = job.params.get("preset").and_then(|v| v.as_str()).map(String::from);
            match job_target(&root, &job.params, sink).await {
                Ok(target) => run_evolve_job(
                    &root,
                    target.start_cmd,
                    target.url,
                    profile,
                    max_findings,
                    test,
                    preset,
                    &job.params,
                    sink,
                )
                .await
                .map_err(|e| format!("{e:#}"))
                .map(|report| serde_json::to_value(report).unwrap_or_default()),
                Err(message) => Err(message),
            }
        }
        "optimize.run" => {
            let root = job
                .params
                .get("path")
                .and_then(|v| v.as_str())
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("."));
            let objective = job
                .params
                .get("objective")
                .and_then(|v| v.as_str())
                .unwrap_or("p95")
                .to_owned();
            let test = job.params.get("test").and_then(|v| v.as_str()).map(String::from);
            match job_target(&root, &job.params, sink).await {
                Ok(target) => run_optimize_job(
                    &root,
                    target.start_cmd,
                    target.url,
                    objective,
                    test,
                    &job.params,
                    sink,
                )
                .await
                .map_err(|e| format!("{e:#}"))
                .map(|report| serde_json::to_value(report).unwrap_or_default()),
                Err(message) => Err(message),
            }
        }
        "promote.run" => {
            let root = job
                .params
                .get("path")
                .and_then(|v| v.as_str())
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("."));
            let finding = job.params.get("finding").and_then(|v| v.as_str()).map(String::from);
            match finding {
                Some(finding) => promote_from_disk(&root, &finding)
                    .map_err(|e| format!("{e:#}"))
                    .map(|record| serde_json::to_value(record).unwrap_or_default()),
                None => Err("promote.run requires `finding`".to_owned()),
            }
        }
        "promote.merge" => {
            let root = job
                .params
                .get("path")
                .and_then(|v| v.as_str())
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("."));
            match job.params.get("id").and_then(|v| v.as_str()) {
                Some(id) => crate::promote::merge(&root, id)
                    .map_err(|e| format!("{e:#}"))
                    .map(|record| serde_json::to_value(record).unwrap_or_default()),
                None => Err("promote.merge requires `id`".to_owned()),
            }
        }
        "rollback.run" => {
            let root = job
                .params
                .get("path")
                .and_then(|v| v.as_str())
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("."));
            match job.params.get("id").and_then(|v| v.as_str()) {
                Some(id) => crate::promote::rollback(&root, id)
                    .map_err(|e| format!("{e:#}"))
                    .map(|record| serde_json::to_value(record).unwrap_or_default()),
                None => Err("rollback.run requires `id`".to_owned()),
            }
        }
        other => Err(format!("unknown job kind: {other}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bus_sink_tags_progress_events_with_the_job() {
        let bus = EventBus::new();
        let mut rx = bus.subscribe();
        let sink = BusSink { bus: bus.clone(), job: 7, kind: "proof.run".to_owned() };
        sink.emit("proof", "started", json!({ "profile": "quick" }));

        let event = rx.try_recv().expect("one event on the bus");
        assert_eq!(event.event, "run.progress");
        assert_eq!(event.payload["job"], 7);
        assert_eq!(event.payload["kind"], "proof.run");
        assert_eq!(event.payload["stage"], "proof");
        assert_eq!(event.payload["event"], "started");
        assert_eq!(event.payload["data"]["profile"], "quick");
    }

    #[test]
    fn a_caller_can_offer_the_bridge_a_project_never_configured() {
        let config = EvolveConfig::default();
        let params = json!({ "generator": "navin python -m navin.evolve.bridge" });
        let generator = make_generator(&config, None, offered_generator(&params), None);
        assert_eq!(generator.name(), "bridge");
    }

    #[test]
    fn what_the_project_pins_wins_over_what_the_caller_offers() {
        use crate::diagnose::{Confidence, Finding, Severity};

        let mut config = EvolveConfig::default();
        // The pinned bridge answers politely; the offered one would blow up.
        config.evolve.generator.command = "cat >/dev/null; echo '[]'".to_owned();
        let params = json!({ "generator": "exit 7" });
        let generator = make_generator(&config, None, offered_generator(&params), None);
        let finding = Finding {
            id: "crash.load".to_owned(),
            title: "t".to_owned(),
            severity: Severity::Critical,
            confidence: Confidence::High,
            related_fault: None,
            symptom: "s".to_owned(),
            root_cause: "c".to_owned(),
            remediation: "r".to_owned(),
            family: "reliability".to_owned(),
            evidence: vec![],
        };
        let proposed = generator.propose(&finding, Path::new("/tmp")).expect("pinned bridge ran");
        assert!(proposed.is_empty());
    }

    #[test]
    fn with_nothing_offered_and_nothing_configured_no_model_is_called() {
        let generator = make_generator(&EvolveConfig::default(), None, None, None);
        assert_eq!(generator.name(), "provided");
    }
}
