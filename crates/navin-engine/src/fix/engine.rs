//! Fix orchestration: prove the current code, then for each candidate apply
//! it in a fresh shadow, prove again, and let the gate decide. Accepted
//! candidates are written as promotion proposals under `.navin/fixes/` -
//! the engine never touches the real workspace.

use anyhow::{Context, Result};
use serde_json::json;
use std::path::{Path, PathBuf};
use std::time::Duration;
use tracing::info;

use crate::diagnose::{diagnose, Diagnosis, Severity};
use crate::policy::config::InvariantSpec;
use crate::progress::{NoopSink, ProgressSink};
use crate::verify::invariants::run_invariants;
use crate::proof::model::ProofReport;
use crate::proof::{run_proof, ProofPlan, ProofTarget};
use crate::shadow::cleanup::CleanupGuard;
use crate::shadow::sandbox::SandboxLimits;
use crate::shadow::{worktree, ShadowManager};

use super::gate::{evaluate, GateConfig};
use super::generator::FixGenerator;
use super::model::{
    Comparison, FixAttempt, FixCandidate, FixReport, FixPatch, FIX_SCHEMA,
};
use super::patch;

/// Everything the fix loop needs to reproduce the proof.
#[derive(Debug, Clone)]
pub struct FixContext {
    pub start_cmd: String,
    pub url: String,
    pub plan: ProofPlan,
    pub ready_timeout: Duration,
    pub limits: Option<SandboxLimits>,
    /// Project test command (from the manifest or the caller). When set,
    /// the suite runs in every shadow and the gate rejects candidates that
    /// turn a green suite red.
    pub test_cmd: Option<String>,
    /// Business invariants from evolve.toml; the gate rejects candidates
    /// that break a previously green invariant.
    pub invariants: Vec<InvariantSpec>,
}

/// Upper bound for one test-suite run inside a shadow.
const TEST_DEADLINE: Duration = Duration::from_secs(600);

struct Evidence {
    report: ProofReport,
    diagnosis: Diagnosis,
    tests_passed: Option<bool>,
    /// None when no invariant is declared.
    invariants_ok: Option<bool>,
    /// Unified diff of the candidate as applied, None for the baseline.
    code_diff: Option<String>,
}

/// Run a fix campaign for `target_finding` using candidates from `generator`.
pub async fn run_fix(
    project_root: &Path,
    ctx: &FixContext,
    target_finding: &str,
    generator: &dyn FixGenerator,
    gate_cfg: &GateConfig,
    sink: &dyn ProgressSink,
) -> Result<FixReport> {
    let commit = if worktree::is_git_repo(project_root) {
        worktree::head_sha(project_root)?
    } else {
        "workdir".to_owned()
    };

    // 1. Establish the "before" picture at HEAD.
    info!("fix: proving current code (before)");
    sink.emit("fix", "started", json!({ "finding": target_finding }));
    let before = prove_and_diagnose(project_root, "fix-before", ctx, None).await?;
    sink.emit(
        "fix",
        "baseline_proved",
        json!({
            "score": before.report.robustness_score,
            "verdict": before.report.verdict,
            "tests": before.tests_passed,
            "invariants": before.invariants_ok,
        }),
    );
    let target = before
        .diagnosis
        .findings
        .iter()
        .find(|f| f.id == target_finding)
        .cloned();

    let mut notes = Vec::new();
    if target.is_none() {
        notes.push(format!(
            "target finding `{target_finding}` was not present before the fix; proceeding but resolution cannot be confirmed"
        ));
    }

    // 2. Ask the generator for candidates.
    let candidates: Vec<FixCandidate> = match &target {
        Some(finding) => generator.propose(finding, project_root)?,
        None => Vec::new(),
    };
    if candidates.is_empty() {
        notes.push(format!("generator `{}` proposed no candidates", generator.name()));
    }
    sink.emit(
        "fix",
        "candidates",
        json!({ "generator": generator.name(), "count": candidates.len() }),
    );

    let p95_before = load_p95(&before.report);

    // 3. Evaluate each candidate in isolation.
    let mut attempts = Vec::new();
    let mut accepted: Option<(String, FixCandidate)> = None;
    for (index, candidate) in candidates.into_iter().enumerate() {
        info!("fix: evaluating candidate {} ({})", candidate.id, candidate.rationale);
        sink.emit(
            "fix",
            "candidate_started",
            json!({ "candidate": candidate.id, "rationale": candidate.rationale }),
        );
        let run_id = format!("fix-cand-{index}");
        let attempt = evaluate_candidate(
            project_root,
            ctx,
            &run_id,
            target_finding,
            &candidate,
            before.report.robustness_score,
            before.report.verdict,
            p95_before,
            before.tests_passed,
            before.invariants_ok,
            gate_cfg,
        )
        .await;

        let ok = attempt.gate.accepted();
        sink.emit(
            "fix",
            "candidate_evaluated",
            json!({
                "candidate": attempt.candidate_id,
                "decision": attempt.gate.decision,
                "score_after": attempt.comparison.score_after,
            }),
        );
        attempts.push(attempt);
        if ok && accepted.is_none() {
            accepted = Some((candidate.id.clone(), candidate));
        }
    }

    // 4. Persist an accepted candidate as a proposal (never applied here).
    let (accepted_id, proposal_path) = match accepted {
        Some((id, candidate)) => {
            let path = write_proposal(project_root, &candidate)?;
            (Some(id), Some(path.display().to_string()))
        }
        None => (None, None),
    };

    sink.emit("fix", "completed", json!({ "accepted": &accepted_id }));
    Ok(FixReport {
        schema: FIX_SCHEMA.to_owned(),
        commit,
        collected_at: crate::proof::now_epoch(),
        target_finding: target_finding.to_owned(),
        score_before: before.report.robustness_score,
        verdict_before: before.report.verdict,
        attempts,
        accepted: accepted_id,
        proposal_path,
        notes,
    })
}

#[allow(clippy::too_many_arguments)]
async fn evaluate_candidate(
    project_root: &Path,
    ctx: &FixContext,
    run_id: &str,
    target_finding: &str,
    candidate: &FixCandidate,
    score_before: u8,
    verdict_before: crate::proof::Verdict,
    p95_before: Option<f64>,
    tests_before: Option<bool>,
    invariants_before: Option<bool>,
    gate_cfg: &GateConfig,
) -> FixAttempt {
    match prove_and_diagnose(project_root, run_id, ctx, Some(&candidate.patch)).await {
        Ok(after) => {
            let after_findings: Vec<String> =
                after.diagnosis.findings.iter().map(|f| f.id.clone()).collect();
            let resolved_target = !after_findings.iter().any(|id| id == target_finding);
            // "New serious findings" = critical/high present after but not before.
            let new_high_findings: Vec<String> = after
                .diagnosis
                .findings
                .iter()
                .filter(|f| matches!(f.severity, Severity::Critical | Severity::High))
                .map(|f| f.id.clone())
                .collect();
            let comparison = Comparison {
                score_before,
                score_after: after.report.robustness_score,
                verdict_before,
                verdict_after: after.report.verdict,
                resolved_target,
                new_high_findings,
                p95_before_ms: p95_before,
                p95_after_ms: load_p95(&after.report),
                tests_before,
                tests_after: after.tests_passed,
                invariants_before,
                invariants_after: after.invariants_ok,
            };
            let gate = evaluate(&comparison, gate_cfg);
            FixAttempt {
                candidate_id: candidate.id.clone(),
                target_finding: target_finding.to_owned(),
                rationale: candidate.rationale.clone(),
                comparison,
                gate,
                after_findings,
                apply_error: None,
                diff: after.code_diff,
            }
        }
        Err(err) => FixAttempt {
            candidate_id: candidate.id.clone(),
            target_finding: target_finding.to_owned(),
            rationale: candidate.rationale.clone(),
            comparison: Comparison {
                score_before,
                score_after: score_before,
                verdict_before,
                verdict_after: verdict_before,
                resolved_target: false,
                new_high_findings: vec![],
                p95_before_ms: p95_before,
                p95_after_ms: None,
                tests_before,
                tests_after: None,
                invariants_before,
                invariants_after: None,
            },
            gate: super::model::GateResult {
                decision: super::model::Decision::Reject,
                reasons: vec![format!("against: candidate failed to apply/prove: {err:#}")],
            },
            after_findings: vec![],
            apply_error: Some(format!("{err:#}")),
            diff: None,
        },
    }
}

/// Create a shadow, optionally apply a patch, prove it, and diagnose using
/// the log the proof produced. The shadow is always destroyed afterwards.
async fn prove_and_diagnose(
    project_root: &Path,
    run_id: &str,
    ctx: &FixContext,
    patch: Option<&FixPatch>,
) -> Result<Evidence> {
    let manager = ShadowManager::new(project_root);
    let guard = CleanupGuard::new(manager.create(run_id)?);

    let code_diff = match patch {
        Some(patch) => {
            patch::apply(patch, guard.path())
                .context("applying candidate patch to the shadow")?;
            // Read it now: the shadow does not outlive this call.
            super::diff::capture(guard.path())
        }
        None => None,
    };

    // Run the project's own tests first: cheaper than a proof, and a broken
    // suite is decisive on its own.
    let tests_passed = match &ctx.test_cmd {
        Some(cmd) => Some(run_test_suite(cmd, guard.path(), project_root).await),
        None => None,
    };

    let invariants_ok = if ctx.invariants.is_empty() {
        None
    } else {
        Some(run_invariants(&ctx.invariants, guard.path(), project_root).await.passed)
    };

    let target = ProofTarget {
        start_cmd: ctx.start_cmd.clone(),
        url: ctx.url.clone(),
        work_dir: guard.path().to_path_buf(),
        ready_timeout: ctx.ready_timeout,
        limits: ctx.limits,
    };
    // Inner proofs are silent: the fix stage reports its own progress and
    // interleaving nested proof events would confuse stream consumers.
    let report = run_proof(project_root, &target, &ctx.plan, &NoopSink).await;
    // Read the log before it is overwritten by the next proof, then diagnose.
    let log_text = std::fs::read_to_string(proof_log_path(project_root)).unwrap_or_default();
    guard.destroy()?;

    let report = report?;
    let diagnosis = diagnose(&report, &log_text);
    Ok(Evidence { report, diagnosis, tests_passed, invariants_ok, code_diff })
}

/// Run the test command inside a shadow. Any non-zero exit, spawn failure
/// or deadline overrun counts as a failing suite.
async fn run_test_suite(cmd: &str, work_dir: &Path, project_root: &Path) -> bool {
    let log = crate::engine_dir(project_root).join("logs").join("fix-tests.log");
    match crate::runner::SupervisedProcess::spawn(cmd, work_dir, &log, None) {
        Ok(process) => matches!(process.wait_with_deadline(TEST_DEADLINE).await, Ok(0)),
        Err(err) => {
            info!("fix: test command failed to spawn: {err:#}");
            false
        }
    }
}

fn proof_log_path(project_root: &Path) -> PathBuf {
    crate::engine_dir(project_root).join("logs").join("proof-service.log")
}

/// Pull the load fault's P95 (ms) out of a proof report, if present.
fn load_p95(report: &ProofReport) -> Option<f64> {
    let load = report.faults.iter().find(|f| f.fault == "load")?;
    let text = load.evidence.join(" ").to_lowercase();
    let idx = text.find("p95")? + "p95".len();
    let number: String = text[idx..]
        .trim_start()
        .chars()
        .take_while(|c| c.is_ascii_digit() || *c == '.')
        .collect();
    number.parse().ok()
}

/// Write an accepted candidate as a proposal file. This is the hand-off to
/// the (later) promotion stage; it is not applied to the workspace.
fn write_proposal(project_root: &Path, candidate: &FixCandidate) -> Result<PathBuf> {
    let dir = project_root.join(crate::NAVIN_DIR).join("fixes").join("proposals");
    std::fs::create_dir_all(&dir)
        .with_context(|| format!("cannot create {}", dir.display()))?;
    let path = dir.join(format!("{}.json", candidate.id));
    std::fs::write(&path, serde_json::to_string_pretty(candidate)?)
        .with_context(|| format!("cannot write {}", path.display()))?;
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::proof::model::{CheckResult, FaultOutcome, ProofReport, Verdict};
    use std::path::Path;

    #[test]
    fn load_p95_is_parsed_from_evidence() {
        let report = ProofReport::build(
            "abc",
            "quick",
            Path::new("/tmp/s"),
            vec![FaultOutcome::new("load", "", vec![CheckResult::new("no_crash", Verdict::Pass, "")])
                .with_evidence(vec!["100 req, p95 12.5 ms, p99 30 ms, 900 rps".to_owned()])],
            vec![],
        );
        assert_eq!(load_p95(&report), Some(12.5));
    }

    #[test]
    fn missing_load_fault_yields_no_p95() {
        let report = ProofReport::build("abc", "quick", Path::new("/tmp/s"), vec![], vec![]);
        assert_eq!(load_p95(&report), None);
    }
}
