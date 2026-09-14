//! The end-to-end evolve pipeline. One call proves the project, diagnoses
//! the failures, and for each serious finding asks the generator for
//! candidates, verifies them with the Fix engine, and promotes the winner
//! under policy. Everything runs in shadows; only promotion touches the
//! workspace, and only within `.navin/evolve.toml`'s bounds.

use anyhow::Result;
use serde_json::json;
use std::path::Path;
use std::time::Duration;
use tracing::{info, warn};

use crate::diagnose::{diagnose_project, Severity};
use crate::fix::{run_fix, FixCandidate, FixContext, FixGenerator, GateConfig};
use crate::policy::config::EvolveConfig;
use crate::progress::ProgressSink;
use crate::promote::promote;
use crate::proof::{run_proof_in_shadow, ProofPlan};
use crate::shadow::sandbox::SandboxLimits;

use super::model::{EvolveReport, FindingOutcome, EVOLVE_SCHEMA};

#[derive(Debug, Clone)]
pub struct EvolveContext {
    pub start_cmd: String,
    pub url: String,
    pub profile: String,
    pub ready_timeout: Duration,
    pub limits: Option<SandboxLimits>,
    /// Cap on how many findings to attempt in one run.
    pub max_findings: usize,
    /// Project test command; candidates that break a green suite are rejected.
    pub test_cmd: Option<String>,
}

/// Run the whole pipeline once.
pub async fn run_evolve(
    project_root: &Path,
    ctx: &EvolveContext,
    generator: &dyn FixGenerator,
    config: &EvolveConfig,
    sink: &dyn ProgressSink,
) -> Result<EvolveReport> {
    let rss_limit = config.evolve.resources.max_memory_mb;
    let plan = ProofPlan::for_profile(&ctx.profile, rss_limit);

    // 1. Prove + 2. diagnose the current code.
    info!("evolve: proving current code");
    sink.emit(
        "evolve",
        "started",
        json!({ "profile": ctx.profile, "generator": generator.name() }),
    );
    let proof = run_proof_in_shadow(
        project_root,
        "evolve-proof",
        &ctx.start_cmd,
        &ctx.url,
        &plan,
        ctx.ready_timeout,
        ctx.limits,
        false,
        sink,
    )
    .await?;
    proof.save(project_root)?;
    sink.emit(
        "evolve",
        "proved",
        json!({ "score": proof.robustness_score, "verdict": proof.verdict }),
    );
    let diagnosis = diagnose_project(project_root, &proof);
    diagnosis.save(project_root)?;

    // 3. Select the serious findings, worst-first, within the budget.
    let budget = config.evolve.budget.max_candidates as usize;
    let cap = ctx.max_findings.min(budget);
    let targets: Vec<_> = diagnosis
        .findings
        .iter()
        .filter(|f| matches!(f.severity, Severity::Critical | Severity::High))
        .take(cap)
        .cloned()
        .collect();

    let mut notes = Vec::new();
    if targets.is_empty() {
        notes.push("no critical/high findings to address".to_owned());
    }
    sink.emit(
        "evolve",
        "diagnosed",
        json!({ "findings": diagnosis.findings.len(), "targets": targets.len() }),
    );

    let fix_ctx = FixContext {
        start_cmd: ctx.start_cmd.clone(),
        url: ctx.url.clone(),
        plan: plan.clone(),
        ready_timeout: ctx.ready_timeout,
        limits: ctx.limits,
        test_cmd: ctx.test_cmd.clone(),
        invariants: config.invariants.clone(),
    };
    let gate_cfg = GateConfig::default();

    // 4. Fix + 5. promote each finding.
    let mut outcomes = Vec::new();
    let mut addressed = 0;
    for finding in &targets {
        info!("evolve: addressing {} ({})", finding.id, finding.severity.label());
        sink.emit(
            "evolve",
            "finding_started",
            json!({ "finding": finding.id, "severity": finding.severity.label() }),
        );
        let mut outcome = FindingOutcome {
            finding_id: finding.id.clone(),
            severity: finding.severity.label().to_owned(),
            family: finding.family.clone(),
            candidates_generated: 0,
            fix_accepted: false,
            promotion_id: None,
            promotion_outcome: None,
            note: String::new(),
        };

        let fix_report =
            match run_fix(project_root, &fix_ctx, &finding.id, generator, &gate_cfg, sink).await {
                Ok(report) => report,
                Err(err) => {
                    outcome.note = format!("fix failed: {err:#}");
                    sink.emit(
                        "evolve",
                        "finding_done",
                        json!({ "finding": finding.id, "accepted": false, "note": &outcome.note }),
                    );
                    outcomes.push(outcome);
                    continue;
                }
            };
        fix_report.save(project_root).ok();
        outcome.candidates_generated = fix_report.attempts.len();

        let Some(accepted_id) = fix_report.accepted.clone() else {
            outcome.note = if fix_report.attempts.is_empty() {
                "no candidates generated".to_owned()
            } else {
                "no candidate passed the gate".to_owned()
            };
            sink.emit(
                "evolve",
                "finding_done",
                json!({ "finding": finding.id, "accepted": false, "note": &outcome.note }),
            );
            outcomes.push(outcome);
            continue;
        };
        outcome.fix_accepted = true;
        addressed += 1;

        // Promote the accepted proposal under policy.
        match load_proposal(project_root, &accepted_id) {
            Ok(candidate) => match promote(project_root, &fix_report, &candidate, config) {
                Ok(record) => {
                    outcome.promotion_id = Some(record.id.clone());
                    outcome.promotion_outcome = Some(format!("{:?}", record.outcome));
                    outcome.note = record.reasons.join("; ");
                    sink.emit(
                        "promote",
                        "decided",
                        json!({
                            "finding": finding.id,
                            "promotion": record.id,
                            "outcome": format!("{:?}", record.outcome),
                            "merged": record.merged,
                        }),
                    );
                }
                Err(err) => {
                    warn!("evolve: promotion failed for {}: {err:#}", finding.id);
                    outcome.note = format!("fix accepted but promotion failed: {err:#}");
                }
            },
            Err(err) => {
                outcome.note = format!("fix accepted but proposal unreadable: {err:#}");
            }
        }
        sink.emit(
            "evolve",
            "finding_done",
            json!({
                "finding": finding.id,
                "accepted": outcome.fix_accepted,
                "promotion": &outcome.promotion_outcome,
                "note": &outcome.note,
            }),
        );
        outcomes.push(outcome);
    }

    let report = EvolveReport {
        schema: EVOLVE_SCHEMA.to_owned(),
        commit: proof.commit.clone(),
        collected_at: crate::proof::now_epoch(),
        profile: ctx.profile.clone(),
        generator: generator.name().to_owned(),
        robustness_before: proof.robustness_score,
        verdict_before: proof.verdict,
        findings_total: diagnosis.findings.len(),
        findings_addressed: addressed,
        outcomes,
        notes,
    };
    report.save(project_root)?;
    sink.emit(
        "evolve",
        "completed",
        json!({
            "findings_total": report.findings_total,
            "findings_addressed": report.findings_addressed,
        }),
    );
    Ok(report)
}

fn load_proposal(project_root: &Path, id: &str) -> Result<FixCandidate> {
    let path = project_root
        .join(crate::NAVIN_DIR)
        .join("fixes")
        .join("proposals")
        .join(format!("{id}.json"));
    let text = std::fs::read_to_string(&path)?;
    Ok(serde_json::from_str(&text)?)
}
