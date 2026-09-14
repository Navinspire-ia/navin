//! The optimization pipeline (ASSE): take healthy code, ask the generator
//! for N independent variants, benchmark every variant under the exact same
//! load in its own shadow, and keep only the measured winner - verified by
//! the project's tests and a fresh proof before promotion. The model's
//! judgement is never trusted: only numbers decide.

use anyhow::{Context, Result};
use serde_json::json;
use std::path::Path;
use std::time::Duration;
use tracing::info;

use crate::baseline::latency::{self, LatencyStats};
use crate::diagnose::model::{Confidence, Finding, Severity};
use crate::fix::model::{Comparison, Decision, FixAttempt, FixReport, GateResult, FIX_SCHEMA};
use crate::fix::{FixCandidate, FixGenerator, FixPatch};
use crate::policy::config::{EvolveConfig, InvariantSpec};
use crate::progress::{NoopSink, ProgressSink};
use crate::proof::service::ServiceManager;
use crate::proof::{run_proof, ProofPlan, ProofTarget, Verdict};
use crate::runner::ports::parse_http_url;
use crate::runner::SupervisedProcess;
use crate::shadow::cleanup::CleanupGuard;
use crate::shadow::sandbox::SandboxLimits;
use crate::shadow::{worktree, ShadowManager};
use crate::verify::differential::{self, Fingerprint};
use crate::verify::invariants::run_invariants;

use super::model::{
    error_ratio, gain_percent, select_winner, Objective, OptimizeReport, VariantOutcome,
    OPTIMIZE_SCHEMA,
};
use super::stats::{self, Sample};

/// Extra failure ratio tolerated versus the baseline benchmark (1%).
const ERROR_RATIO_TOLERANCE: f64 = 0.01;
/// Upper bound for one test-suite run inside a shadow.
const TEST_DEADLINE: Duration = Duration::from_secs(600);
/// Unmeasured warmup before the benchmark windows (cold-start bias).
const WARMUP_DURATION: Duration = Duration::from_secs(1);

#[derive(Debug, Clone)]
pub struct OptimizeContext {
    pub start_cmd: String,
    pub url: String,
    pub ready_timeout: Duration,
    pub limits: Option<SandboxLimits>,
    pub test_cmd: Option<String>,
    /// Benchmark length per window; identical for baseline and variants.
    pub bench_duration: Duration,
    pub bench_concurrency: usize,
    /// Repeated benchmark windows per measurement. A gain must exceed the
    /// combined noise of both distributions to count (Welch criterion).
    pub bench_repeats: usize,
    pub max_variants: usize,
    /// Minimum objective improvement (percent) required to win.
    pub min_gain_percent: f64,
    pub objective: Objective,
    /// How many request vectors the differential verifier replays against
    /// baseline and every variant (0 disables the check).
    pub diff_vectors: usize,
}

/// Run one optimization campaign.
pub async fn run_optimize(
    project_root: &Path,
    ctx: &OptimizeContext,
    generator: &dyn FixGenerator,
    config: &EvolveConfig,
    sink: &dyn ProgressSink,
) -> Result<OptimizeReport> {
    let commit = if worktree::is_git_repo(project_root) {
        worktree::head_sha(project_root)?
    } else {
        "workdir".to_owned()
    };
    let mut notes = Vec::new();
    sink.emit(
        "optimize",
        "started",
        json!({ "objective": ctx.objective.slug(), "generator": generator.name() }),
    );

    // 1. Optimize only healthy code: the unmodified project must pass a
    // quick proof first. Broken code goes through evolve, not optimize.
    info!("optimize: proving the unmodified code");
    let plan = ProofPlan::for_profile("quick", config.evolve.resources.max_memory_mb);
    let baseline_proof = prove_patched(project_root, "opt-proof-base", ctx, &plan, None).await?;
    if baseline_proof.verdict != Verdict::Pass {
        anyhow::bail!(
            "the unmodified code failed its proof (score {}); run `evolve` first, then optimize",
            baseline_proof.robustness_score
        );
    }

    // 2. Baseline benchmark (tests + invariants + behavioural fingerprints)
    // in a shadow, repeated over several windows to estimate the noise.
    info!("optimize: measuring the baseline ({} windows)", ctx.bench_repeats.max(1));
    let diff_mode = if ctx.diff_vectors > 0 {
        DiffMode::Discover(ctx.diff_vectors)
    } else {
        DiffMode::Off
    };
    let base_measured =
        measure_patched(project_root, "opt-base", ctx, None, diff_mode, &config.invariants)
            .await?;
    let baseline = stats::aggregate(&base_measured.runs);
    let base_p95 = stats::sample(&stats::p95_series(&base_measured.runs));
    let base_rps = stats::sample(&stats::rps_series(&base_measured.runs));
    let tests_before = base_measured.tests_passed;
    let invariants_before = base_measured.invariants_ok;
    let baseline_prints = base_measured.fingerprints;
    anyhow::ensure!(
        baseline.requests > 0,
        "baseline benchmark completed no requests; is the URL correct?"
    );
    if tests_before == Some(false) {
        notes.push("test suite already failing on the unmodified code".to_owned());
    }
    if invariants_before == Some(false) {
        notes.push(format!(
            "business invariants already failing on the unmodified code: {}",
            base_measured.invariant_failures.join(", ")
        ));
    }
    let diff_paths: Vec<String> = baseline_prints
        .as_deref()
        .unwrap_or(&[])
        .iter()
        .map(|print| print.path.clone())
        .collect();
    sink.emit(
        "optimize",
        "baseline",
        json!({
            "p95_ms": baseline.p95_ms,
            "p95_std_ms": base_p95.std_dev,
            "rps": baseline.rps,
            "rps_std": base_rps.std_dev,
            "repeats": base_measured.runs.len(),
            "score": baseline_proof.robustness_score,
            "tests": tests_before,
            "invariants": invariants_before,
            "diff_vectors": diff_paths.len(),
        }),
    );

    // 3. Ask the generator for variants of a synthetic "finding".
    let finding = optimization_finding(ctx, &baseline);
    let mut candidates = generator
        .propose(&finding, project_root)
        .context("generating optimization variants")?;
    candidates.truncate(ctx.max_variants);
    if candidates.is_empty() {
        notes.push(format!("generator `{}` proposed no variants", generator.name()));
    }
    sink.emit("optimize", "variants", json!({ "count": candidates.len() }));

    // 4. Benchmark every variant under the identical load.
    let mut variants: Vec<VariantOutcome> = Vec::new();
    let mut kept_candidates: Vec<FixCandidate> = Vec::new();
    for (index, candidate) in candidates.into_iter().enumerate() {
        info!("optimize: measuring variant {} ({})", candidate.id, candidate.rationale);
        sink.emit(
            "optimize",
            "variant_started",
            json!({ "variant": candidate.id, "rationale": candidate.rationale }),
        );
        let run_id = format!("opt-var-{index}");
        let outcome = measure_variant(MeasureVariantArgs {
            project_root,
            run_id: &run_id,
            ctx,
            candidate: &candidate,
            baseline: &baseline,
            base_p95: &base_p95,
            base_rps: &base_rps,
            tests_before,
            invariants_before,
            baseline_prints: baseline_prints.as_deref(),
            diff_paths: &diff_paths,
            invariants: &config.invariants,
        })
        .await;
        sink.emit(
            "optimize",
            "variant_measured",
            json!({
                "variant": outcome.candidate_id,
                "gain_percent": outcome.gain_percent,
                "significant": outcome.significant,
                "eligible": outcome.eligible,
                "note": &outcome.note,
            }),
        );
        variants.push(outcome);
        kept_candidates.push(candidate);
    }

    // 5. The measured winner, if any gain clears the floor.
    let winner_index = select_winner(&variants, ctx.min_gain_percent);
    let mut report = OptimizeReport {
        schema: OPTIMIZE_SCHEMA.to_owned(),
        commit: commit.clone(),
        collected_at: crate::proof::now_epoch(),
        objective: ctx.objective,
        baseline: baseline.clone(),
        baseline_p95_std_ms: Some(round1(base_p95.std_dev)),
        baseline_rps_std: Some(round1(base_rps.std_dev)),
        bench_repeats: base_measured.runs.len(),
        invariants_checked: config.invariants.len(),
        baseline_score: baseline_proof.robustness_score,
        variants,
        winner: None,
        winner_gain_percent: None,
        promotion_id: None,
        promotion_outcome: None,
        notes,
    };

    let Some(index) = winner_index else {
        report.notes.push(format!(
            "no variant improved {} by at least {:.1}% beyond measurement noise",
            ctx.objective.slug(),
            ctx.min_gain_percent
        ));
        sink.emit("optimize", "completed", json!({ "winner": null }));
        report.save(project_root)?;
        return Ok(report);
    };
    let winner = kept_candidates[index].clone();
    let gain = report.variants[index].gain_percent.unwrap_or(0.0);
    report.winner = Some(winner.id.clone());
    report.winner_gain_percent = Some(gain);
    sink.emit(
        "optimize",
        "winner",
        json!({ "variant": winner.id, "gain_percent": gain }),
    );

    // 6. Verify the winner with a full quick proof before touching git.
    info!("optimize: proving the winner {}", winner.id);
    let winner_proof =
        prove_patched(project_root, "opt-proof-win", ctx, &plan, Some(&winner.patch)).await?;
    sink.emit(
        "optimize",
        "verified",
        json!({ "score": winner_proof.robustness_score, "verdict": winner_proof.verdict }),
    );
    if winner_proof.verdict != Verdict::Pass
        || winner_proof.robustness_score < baseline_proof.robustness_score
    {
        report.notes.push(format!(
            "winner rejected at verification: proof {:?} score {} (baseline {})",
            winner_proof.verdict, winner_proof.robustness_score, baseline_proof.robustness_score
        ));
        report.winner = None;
        report.winner_gain_percent = None;
        sink.emit("optimize", "completed", json!({ "winner": null }));
        report.save(project_root)?;
        return Ok(report);
    }

    // 7. Promote under policy with the same signed-certificate path as fixes.
    let fix_report = synthesized_fix_report(
        &commit,
        &finding.id,
        &winner,
        &report,
        index,
        baseline_proof.robustness_score,
        winner_proof.robustness_score,
        tests_before,
        invariants_before,
    );
    fix_report.save(project_root).ok();
    match crate::promote::promote(project_root, &fix_report, &winner, config) {
        Ok(record) => {
            report.promotion_id = Some(record.id.clone());
            report.promotion_outcome = Some(format!("{:?}", record.outcome));
            report.notes.push(record.reasons.join("; "));
            sink.emit(
                "promote",
                "decided",
                json!({
                    "promotion": record.id,
                    "outcome": format!("{:?}", record.outcome),
                    "merged": record.merged,
                }),
            );
        }
        Err(err) => {
            report.notes.push(format!("winner accepted but promotion failed: {err:#}"));
        }
    }

    sink.emit(
        "optimize",
        "completed",
        json!({ "winner": &report.winner, "gain_percent": report.winner_gain_percent }),
    );
    report.save(project_root)?;
    Ok(report)
}

/// What the differential verifier should do during one measurement.
enum DiffMode<'a> {
    /// No behavioural check.
    Off,
    /// Crawl the app for up to N vectors and fingerprint them (baseline).
    Discover(usize),
    /// Replay this exact vector list and fingerprint it (variants).
    Replay(&'a [String]),
}

/// Everything one shadow measurement produced.
struct Measurement {
    /// One LatencyStats per benchmark window (>= 1).
    runs: Vec<LatencyStats>,
    tests_passed: Option<bool>,
    /// None when no invariant is declared in evolve.toml.
    invariants_ok: Option<bool>,
    invariant_failures: Vec<String>,
    fingerprints: Option<Vec<Fingerprint>>,
    /// Unified diff of the patch as applied, None for the baseline.
    code_diff: Option<String>,
}

/// Shadow, optional patch, tests, invariants, behavioural fingerprints and
/// repeated benchmark windows. The shadow is always destroyed; measurement
/// errors surface as a failed Result. Fingerprints are taken before the
/// benchmark so every variant runs the identical sequence.
async fn measure_patched(
    project_root: &Path,
    run_id: &str,
    ctx: &OptimizeContext,
    patch: Option<&FixPatch>,
    diff: DiffMode<'_>,
    invariants: &[InvariantSpec],
) -> Result<Measurement> {
    let manager = ShadowManager::new(project_root);
    let guard = CleanupGuard::new(manager.create(run_id)?);

    let code_diff = match patch {
        Some(patch) => {
            crate::fix::patch::apply(patch, guard.path())
                .context("applying the variant to the shadow")?;
            // Read it now: the shadow is destroyed at the end of this call.
            crate::fix::diff::capture(guard.path())
        }
        None => None,
    };

    let tests_passed = match &ctx.test_cmd {
        Some(cmd) => Some(run_test_suite(cmd, guard.path(), project_root).await),
        None => None,
    };

    let (invariants_ok, invariant_failures) = if invariants.is_empty() {
        (None, Vec::new())
    } else {
        let outcome = run_invariants(invariants, guard.path(), project_root).await;
        (Some(outcome.passed), outcome.failures)
    };

    let config = EvolveConfig::load(project_root).unwrap_or_default();
    if config.target.is_worker() {
        // A worker benchmark times `exercise_cmd` instead of HTTP windows;
        // behavioural fingerprints (an HTTP crawl) do not apply.
        anyhow::ensure!(
            !config.target.exercise_cmd.trim().is_empty(),
            "optimizing a worker needs [target] exercise_cmd in .navin/evolve.toml"
        );
        let log_path = crate::engine_dir(project_root).join("logs").join("optimize-service.log");
        let mut worker = crate::proof::worker::WorkerManager::new(
            ctx.start_cmd.clone(),
            guard.path().to_path_buf(),
            log_path.clone(),
            config.target.health_cmd.clone(),
            ctx.ready_timeout,
            ctx.limits,
        );
        worker.start().await?;
        anyhow::ensure!(worker.is_healthy().await, "worker did not pass its initial health check");
        let exercise = config.target.exercise_cmd.clone();
        crate::proof::worker::exercise_stats(
            &exercise,
            guard.path(),
            &log_path,
            WARMUP_DURATION,
            ctx.bench_concurrency,
        )
        .await;
        let mut runs = Vec::new();
        for _ in 0..ctx.bench_repeats.max(1) {
            runs.push(
                crate::proof::worker::exercise_stats(
                    &exercise,
                    guard.path(),
                    &log_path,
                    ctx.bench_duration,
                    ctx.bench_concurrency,
                )
                .await,
            );
        }
        worker.shutdown().await;
        guard.destroy()?;
        return Ok(Measurement {
            runs,
            tests_passed,
            invariants_ok,
            invariant_failures,
            fingerprints: None,
            code_diff,
        });
    }

    let (host, port, path) = parse_http_url(&ctx.url)?;
    anyhow::ensure!(
        matches!(host.as_str(), "127.0.0.1" | "localhost" | "::1"),
        "benchmarks are localhost-only, refusing {host}"
    );
    let specs = latency::specs_for(&config.target, &path);
    let log_path = crate::engine_dir(project_root).join("logs").join("optimize-service.log");
    let mut svc = ServiceManager::new(
        ctx.start_cmd.clone(),
        guard.path().to_path_buf(),
        log_path,
        host.clone(),
        port,
        specs.clone(),
        ctx.ready_timeout,
        ctx.limits,
    );
    svc.start().await?;
    anyhow::ensure!(svc.is_healthy().await, "service did not answer an initial probe");

    let fingerprints = match diff {
        DiffMode::Off => None,
        DiffMode::Discover(max) => {
            let vectors = differential::discover_vectors(&host, port, &path, max).await;
            Some(differential::capture(&host, port, &vectors).await)
        }
        DiffMode::Replay(vectors) => Some(differential::capture(&host, port, vectors).await),
    };

    // Short warmup so cold-start effects do not pollute the first window,
    // then the measured windows themselves.
    latency::probe_specs(&host, port, &specs, WARMUP_DURATION, ctx.bench_concurrency).await;
    let mut runs = Vec::new();
    for _ in 0..ctx.bench_repeats.max(1) {
        runs.push(
            latency::probe_specs(&host, port, &specs, ctx.bench_duration, ctx.bench_concurrency)
                .await,
        );
    }
    svc.shutdown().await;
    guard.destroy()?;
    Ok(Measurement {
        runs,
        tests_passed,
        invariants_ok,
        invariant_failures,
        fingerprints,
        code_diff,
    })
}

/// Inputs for one variant measurement (bundled: the list got too long).
struct MeasureVariantArgs<'a> {
    project_root: &'a Path,
    run_id: &'a str,
    ctx: &'a OptimizeContext,
    candidate: &'a FixCandidate,
    baseline: &'a LatencyStats,
    base_p95: &'a Sample,
    base_rps: &'a Sample,
    tests_before: Option<bool>,
    invariants_before: Option<bool>,
    baseline_prints: Option<&'a [Fingerprint]>,
    diff_paths: &'a [String],
    invariants: &'a [InvariantSpec],
}

/// Benchmark one variant and decide its eligibility. Never fails the whole
/// run: a broken variant is simply ineligible, with the reason recorded.
async fn measure_variant(args: MeasureVariantArgs<'_>) -> VariantOutcome {
    let MeasureVariantArgs {
        project_root,
        run_id,
        ctx,
        candidate,
        baseline,
        base_p95,
        base_rps,
        tests_before,
        invariants_before,
        baseline_prints,
        diff_paths,
        invariants,
    } = args;
    let mut outcome = VariantOutcome {
        candidate_id: candidate.id.clone(),
        rationale: candidate.rationale.clone(),
        stats: None,
        p95_std_ms: None,
        rps_std: None,
        tests_passed: None,
        invariants_ok: None,
        gain_percent: None,
        significant: None,
        behavior_equivalent: None,
        eligible: false,
        note: String::new(),
        diff: None,
    };

    let diff_mode = if baseline_prints.is_some() {
        DiffMode::Replay(diff_paths)
    } else {
        DiffMode::Off
    };
    let measured = match measure_patched(
        project_root,
        run_id,
        ctx,
        Some(&candidate.patch),
        diff_mode,
        invariants,
    )
    .await
    {
        Ok(measured) => measured,
        Err(err) => {
            outcome.note = format!("measurement failed: {err:#}");
            return outcome;
        }
    };
    let stats = stats::aggregate(&measured.runs);
    let cand_p95 = stats::sample(&stats::p95_series(&measured.runs));
    let cand_rps = stats::sample(&stats::rps_series(&measured.runs));
    outcome.tests_passed = measured.tests_passed;
    outcome.invariants_ok = measured.invariants_ok;
    // Set before the eligibility checks: a rejected variant is exactly the
    // one a human wants to read.
    outcome.diff = measured.code_diff;
    outcome.p95_std_ms = Some(round1(cand_p95.std_dev));
    outcome.rps_std = Some(round1(cand_rps.std_dev));
    outcome.gain_percent = Some(round1(gain_percent(ctx.objective, baseline, &stats)));
    if measured.runs.len() >= 2 {
        outcome.significant = Some(match ctx.objective {
            Objective::P95 => stats::significant(base_p95, &cand_p95),
            Objective::Throughput => stats::significant(base_rps, &cand_rps),
        });
    }

    // Breaking a green suite disqualifies; an already-red suite does not.
    if measured.tests_passed == Some(false) && tests_before != Some(false) {
        outcome.note = "project test suite fails with this variant".to_owned();
        outcome.stats = Some(stats);
        return outcome;
    }

    // Same rule for business invariants: breaking a green one disqualifies.
    if measured.invariants_ok == Some(false) && invariants_before != Some(false) {
        outcome.note = format!(
            "business invariants fail with this variant: {}",
            measured.invariant_failures.join(", ")
        );
        outcome.stats = Some(stats);
        return outcome;
    }

    // Behavioural equivalence: byte-identical answers on every vector.
    if let (Some(before), Some(after)) = (baseline_prints, measured.fingerprints.as_deref()) {
        let diff = differential::compare(before, after);
        outcome.behavior_equivalent = Some(diff.equivalent);
        if !diff.equivalent {
            outcome.note = format!(
                "behaviour diverged on {}/{} vectors: {}",
                diff.divergences.len(),
                diff.vectors,
                diff.divergences.join("; ")
            );
            outcome.stats = Some(stats);
            return outcome;
        }
    }
    if error_ratio(&stats) > error_ratio(baseline) + ERROR_RATIO_TOLERANCE {
        outcome.note = format!(
            "error ratio regressed ({:.2}% -> {:.2}%)",
            error_ratio(baseline) * 100.0,
            error_ratio(&stats) * 100.0
        );
        outcome.stats = Some(stats);
        return outcome;
    }

    outcome.note = format!(
        "P95 {:.1} ± {:.1} ms, {:.1} ± {:.1} RPS over {} windows{}",
        stats.p95_ms,
        cand_p95.std_dev,
        stats.rps,
        cand_rps.std_dev,
        measured.runs.len(),
        if outcome.significant == Some(false) { "; gain within measurement noise" } else { "" }
    );
    outcome.stats = Some(stats);
    outcome.eligible = true;
    outcome
}

/// Shadow + optional patch + quick proof (silent: optimize reports its own
/// progress). Used to guard both ends of the campaign.
async fn prove_patched(
    project_root: &Path,
    run_id: &str,
    ctx: &OptimizeContext,
    plan: &ProofPlan,
    patch: Option<&FixPatch>,
) -> Result<crate::proof::ProofReport> {
    let manager = ShadowManager::new(project_root);
    let guard = CleanupGuard::new(manager.create(run_id)?);
    if let Some(patch) = patch {
        crate::fix::patch::apply(patch, guard.path())
            .context("applying the variant to the proof shadow")?;
    }
    let target = ProofTarget {
        start_cmd: ctx.start_cmd.clone(),
        url: ctx.url.clone(),
        work_dir: guard.path().to_path_buf(),
        ready_timeout: ctx.ready_timeout,
        limits: ctx.limits,
    };
    let report = run_proof(project_root, &target, plan, &NoopSink).await;
    guard.destroy()?;
    report
}

async fn run_test_suite(cmd: &str, work_dir: &Path, project_root: &Path) -> bool {
    let log = crate::engine_dir(project_root).join("logs").join("optimize-tests.log");
    match SupervisedProcess::spawn(cmd, work_dir, &log, None) {
        Ok(process) => matches!(process.wait_with_deadline(TEST_DEADLINE).await, Ok(0)),
        Err(_) => false,
    }
}

/// The synthetic finding handed to the generator: optimization reuses the
/// fix-bridge protocol, so the same LLM bridge serves both campaigns.
fn optimization_finding(ctx: &OptimizeContext, baseline: &LatencyStats) -> Finding {
    Finding {
        id: format!("optimize.{}", ctx.objective.slug()),
        title: "Measured performance headroom search".to_owned(),
        severity: Severity::Info,
        confidence: Confidence::High,
        related_fault: None,
        symptom: format!(
            "baseline: P95 {:.1} ms, P50 {:.1} ms, {:.1} RPS over {}s at concurrency {}",
            baseline.p95_ms,
            baseline.p50_ms,
            baseline.rps,
            ctx.bench_duration.as_secs(),
            ctx.bench_concurrency
        ),
        root_cause: "not a failure: the engine is searching for measurable performance headroom"
            .to_owned(),
        remediation: format!(
            "propose independent variants that improve {} without changing externally \
             visible behaviour (routes, responses, CLI); the test suite must stay green; \
             each variant is benchmarked in isolation and only the measured winner is kept",
            ctx.objective.slug()
        ),
        family: "performance".to_owned(),
        evidence: vec![serde_json::to_string(baseline).unwrap_or_default()],
    }
}

/// Wrap the winner in a fix-shaped report so the existing promotion path
/// (policy, signed certificate, rollback) applies unchanged.
#[allow(clippy::too_many_arguments)]
fn synthesized_fix_report(
    commit: &str,
    finding_id: &str,
    winner: &FixCandidate,
    report: &OptimizeReport,
    winner_index: usize,
    score_before: u8,
    score_after: u8,
    tests_before: Option<bool>,
    invariants_before: Option<bool>,
) -> FixReport {
    let measured = &report.variants[winner_index];
    let comparison = Comparison {
        score_before,
        score_after,
        verdict_before: Verdict::Pass,
        verdict_after: Verdict::Pass,
        resolved_target: true,
        new_high_findings: vec![],
        p95_before_ms: Some(report.baseline.p95_ms),
        p95_after_ms: measured.stats.as_ref().map(|s| s.p95_ms),
        tests_before,
        tests_after: measured.tests_passed,
        invariants_before,
        invariants_after: measured.invariants_ok,
    };
    let gate = GateResult {
        decision: Decision::Accept,
        reasons: vec![format!(
            "optimize: measured winner, {} improved {:.1}% ({} variants compared)",
            report.objective.slug(),
            measured.gain_percent.unwrap_or(0.0),
            report.variants.len()
        )],
    };
    FixReport {
        schema: FIX_SCHEMA.to_owned(),
        commit: commit.to_owned(),
        collected_at: crate::proof::now_epoch(),
        target_finding: finding_id.to_owned(),
        score_before,
        verdict_before: Verdict::Pass,
        attempts: vec![FixAttempt {
            candidate_id: winner.id.clone(),
            target_finding: finding_id.to_owned(),
            rationale: winner.rationale.clone(),
            comparison,
            gate,
            after_findings: vec![],
            apply_error: None,
            diff: measured.diff.clone(),
        }],
        accepted: Some(winner.id.clone()),
        proposal_path: None,
        notes: vec!["synthesized by the optimize engine".to_owned()],
    }
}

fn round1(v: f64) -> f64 {
    (v * 10.0).round() / 10.0
}
