//! The tools offered to a host agent, and their mapping onto engine stages.
//!
//! Descriptions are written for a model rather than for a manpage: they say
//! what the tool measures, what it needs, and which tool comes next. Results
//! are compact summaries plus the path of the full artefact the engine wrote
//! under `.navin/`, so an agent can read the details only when it needs them.

use anyhow::{Context, Result};
use serde::Deserialize;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::diagnose::{diagnose_project, Diagnosis};
use crate::evolve::{run_evolve, EvolveContext, EvolveReport};
use crate::fix::{
    run_fix, FixCandidate, FixContext, FixPatch, FixReport, GateConfig, ProvidedPatchGenerator,
};
use crate::optimize::{run_optimize, Objective, OptimizeContext, OptimizeReport};
use crate::policy::config::EvolveConfig;
use crate::progress::ProgressSink;
use crate::project::inspect_project;
use crate::promote;
use crate::proof::{run_proof_in_shadow, ProofPlan, ProofReport, Verdict};
use crate::target;

/// Advertised to the host at initialize time, so the agent knows the loop
/// before it calls anything.
pub const INSTRUCTIONS: &str = "\
The Navin engine measures a running app and verifies patches instead of \
guessing. It never edits the workspace: every candidate is applied in a \
throwaway git worktree, measured, tested, and kept only if the numbers \
improve.

You are the generator. The usual loop:
1. `inspect_project` to see how the engine will start and test the app.
2. `diagnose` to break the app under fault injection and get findings with \
stable ids.
3. Write patches yourself, then `fix` with the finding id and your \
candidates. The engine proves the before/after and gates on evidence.
4. For speed work, `optimize` with your variants: each one is benchmarked \
in isolation and only a statistically significant win survives.

When a candidate wins, apply it to the workspace yourself: the engine \
reports evidence, it does not edit your files. Every run also writes a \
markdown sibling (`report_md`) next to the JSON; open that file to read \
the numbers, the gate, and the winner's diff. The start command, test \
command and URL are auto-detected; pass them only to override.";

/// Tool names this server answers, in the order they are advertised.
const NAMES: [&str; 9] = [
    "inspect_project",
    "prove",
    "diagnose",
    "fix",
    "optimize",
    "evolve",
    "promotions",
    "open_pull_request",
    "verify_certificate",
];

pub fn is_known(name: &str) -> bool {
    NAMES.contains(&name)
}

/// A candidate as an agent writes it. The finding id is stamped by the tool:
/// which finding a run is about is the run's business, not the model's.
#[derive(Debug, Deserialize)]
struct IncomingCandidate {
    id: String,
    #[serde(default)]
    rationale: String,
    #[serde(default)]
    family: Option<String>,
    patch: FixPatch,
}

pub fn catalog() -> Vec<Value> {
    vec![
        json!({
            "name": "inspect_project",
            "description": "Discover how this project builds, tests and starts, per unit, \
                            including monorepos. Cheap and read-only: call it first to see \
                            what the engine will run.",
            "inputSchema": object_schema(json!({ "path": path_property() }), &[]),
        }),
        json!({
            "name": "prove",
            "description": "Boot the app in an isolated shadow worktree and attack it \
                            (load, restart, dependency loss, ...), then report a verdict \
                            and a robustness score out of 100. Read-only for your \
                            workspace. Takes minutes. Use `diagnose` instead when you \
                            want findings you can act on.",
            "inputSchema": object_schema(
                json!({
                    "path": path_property(),
                    "profile": profile_property(),
                    "start": start_property(),
                    "url": url_property(),
                }),
                &[],
            ),
        }),
        json!({
            "name": "diagnose",
            "description": "Prove the app, then explain every failure: symptom, root cause, \
                            remediation direction, and a stable finding id. This is the \
                            entry point when you intend to fix something: pass a finding id \
                            from here to `fix`.",
            "inputSchema": object_schema(
                json!({
                    "path": path_property(),
                    "profile": profile_property(),
                    "start": start_property(),
                    "url": url_property(),
                }),
                &[],
            ),
        }),
        json!({
            "name": "fix",
            "description": "Verify your patches against one finding. The engine proves the \
                            unmodified code, applies each candidate in its own shadow \
                            worktree, proves it again, runs the project test suite, and \
                            accepts a candidate only if it resolves the finding without \
                            regressing anything. Nothing touches your workspace: an \
                            accepted candidate becomes a promotion proposal. Get the \
                            finding id from `diagnose` first.",
            "inputSchema": object_schema(
                json!({
                    "path": path_property(),
                    "finding": {
                        "type": "string",
                        "description": "Finding id returned by `diagnose`, e.g. `crash.load`.",
                    },
                    "candidates": candidates_property(
                        "Patches you wrote for this finding. Independent alternatives, not \
                         steps of one change: each is measured on its own.",
                    ),
                    "profile": profile_property(),
                    "test": test_property(),
                    "start": start_property(),
                    "url": url_property(),
                }),
                &["finding", "candidates"],
            ),
        }),
        json!({
            "name": "optimize",
            "description": "Benchmark performance variants against the unmodified code. \
                            The engine measures a baseline, then each of your variants \
                            under identical load, repeated to estimate noise; a variant \
                            wins only if it beats the baseline by `min_gain` percent, the \
                            gain survives the noise, the tests stay green and the \
                            observable behaviour is unchanged. Requires an app that \
                            already passes its robustness proof.",
            "inputSchema": object_schema(
                json!({
                    "path": path_property(),
                    "objective": {
                        "type": "string",
                        "enum": ["p95", "throughput"],
                        "description": "What to improve: p95 latency (default) or requests per second.",
                    },
                    "candidates": candidates_property(
                        "Performance variants you wrote. Each must preserve routes, \
                         responses and CLI behaviour; the differential verifier replays \
                         traffic and rejects any variant that answers differently.",
                    ),
                    "duration": {
                        "type": "integer",
                        "description": "Seconds of load per benchmark window (default 10).",
                    },
                    "repeats": {
                        "type": "integer",
                        "description": "Benchmark windows per measurement, used to estimate noise (default 3).",
                    },
                    "concurrency": {
                        "type": "integer",
                        "description": "Concurrent connections during the benchmark (default 16).",
                    },
                    "max_variants": {
                        "type": "integer",
                        "description": "How many of your variants to measure (default 4).",
                    },
                    "min_gain": {
                        "type": "number",
                        "description": "Minimum improvement in percent for a variant to win (default 5).",
                    },
                    "test": test_property(),
                    "start": start_property(),
                    "url": url_property(),
                }),
                &[],
            ),
        }),
        json!({
            "name": "evolve",
            "description": "Autopilot: prove, diagnose, then fix the worst findings without \
                            further input. This needs a candidate generator configured in \
                            `.navin/evolve.toml` under `[evolve.generator]`; with no \
                            generator it will find issues and propose nothing. As the host \
                            model, prefer `diagnose` then `fix`, where you write the \
                            patches yourself.",
            "inputSchema": object_schema(
                json!({
                    "path": path_property(),
                    "profile": profile_property(),
                    "max_findings": {
                        "type": "integer",
                        "description": "How many findings to work through, worst first (default 3).",
                    },
                    "test": test_property(),
                    "start": start_property(),
                    "url": url_property(),
                }),
                &[],
            ),
        }),
        json!({
            "name": "promotions",
            "description": "List the promotions the engine recorded for this project. Each \
                            one is an accepted change on its own branch, with a signed \
                            certificate of the measurements that justified it.",
            "inputSchema": object_schema(json!({ "path": path_property() }), &[]),
        }),
        json!({
            "name": "open_pull_request",
            "description": "Push a promotion's branch to the git remote and open a pull \
                            request for it, using the GitHub CLI when it is installed. \
                            Without it, the branch is still pushed and a compare link is \
                            returned. The pull request body carries the measured evidence.",
            "inputSchema": object_schema(
                json!({
                    "path": path_property(),
                    "id": { "type": "string", "description": "Promotion id from `promotions`." },
                }),
                &["id"],
            ),
        }),
        json!({
            "name": "verify_certificate",
            "description": "Re-verify a promotion's certificate: gate validity, artefact \
                            checksum and Ed25519 signature. Use it to check that a change \
                            was really earned by measurement and has not been tampered with.",
            "inputSchema": object_schema(
                json!({
                    "path": path_property(),
                    "id": { "type": "string", "description": "Promotion id from `promotions`." },
                }),
                &["id"],
            ),
        }),
    ]
}

pub async fn call(
    name: &str,
    args: &Value,
    default_root: &Path,
    sink: &dyn ProgressSink,
) -> Result<String> {
    let root = root_of(args, default_root)?;
    let summary = match name {
        "inspect_project" => serde_json::to_value(inspect_project(&root)?)?,
        "prove" => prove(&root, args, sink).await?,
        "diagnose" => diagnose(&root, args, sink).await?,
        "fix" => fix(&root, args, sink).await?,
        "optimize" => optimize(&root, args, sink).await?,
        "evolve" => evolve(&root, args, sink).await?,
        "promotions" => json!({ "promotions": promote::list(&root) }),
        "open_pull_request" => open_pull_request(&root, args)?,
        "verify_certificate" => verify_certificate(&root, args)?,
        other => anyhow::bail!("unknown tool `{other}`"),
    };
    Ok(serde_json::to_string_pretty(&summary)?)
}

async fn prove(root: &Path, args: &Value, sink: &dyn ProgressSink) -> Result<Value> {
    let profile = string_arg(args, "profile").unwrap_or_else(|| "standard".to_owned());
    let report = run_proof(root, args, &profile, "mcp-prove", sink).await?;
    let artefact = report.save(root)?;
    Ok(proof_summary(&report, Some(artefact)))
}

async fn diagnose(root: &Path, args: &Value, sink: &dyn ProgressSink) -> Result<Value> {
    let profile = string_arg(args, "profile").unwrap_or_else(|| "standard".to_owned());
    let report = run_proof(root, args, &profile, "mcp-diagnose", sink).await?;
    report.save(root)?;
    let diagnosis = diagnose_project(root, &report);
    let artefact = diagnosis.save(root)?;
    Ok(diagnosis_summary(&diagnosis, artefact))
}

async fn fix(root: &Path, args: &Value, sink: &dyn ProgressSink) -> Result<Value> {
    let finding = string_arg(args, "finding")
        .context("`finding` is required: run `diagnose` and pass one of its finding ids")?;
    let candidates = candidates_arg(args, &finding, "reliability")?;
    anyhow::ensure!(
        !candidates.is_empty(),
        "`candidates` is empty: write at least one patch for `{finding}`"
    );

    let manifest = inspect_project(root)?;
    let target = target::resolve(root, string_arg(args, "start"), string_arg(args, "url"), sink)
        .await?;
    let profile = string_arg(args, "profile").unwrap_or_else(|| "quick".to_owned());
    let ctx = FixContext {
        start_cmd: target.start_cmd,
        url: target.url,
        plan: ProofPlan::for_profile(&profile, 512),
        ready_timeout: Duration::from_secs(60),
        limits: None,
        test_cmd: string_arg(args, "test")
            .or_else(|| manifest.units.first().and_then(|u| u.commands.test.clone())),
        invariants: EvolveConfig::load(root)?.invariants,
    };
    let generator = ProvidedPatchGenerator::new(candidates);
    let report = run_fix(
        root,
        &ctx,
        &finding,
        &generator,
        &GateConfig::default(),
        sink,
    )
    .await?;
    let artefact = report.save(root)?;
    Ok(fix_summary(&report, artefact))
}

async fn optimize(root: &Path, args: &Value, sink: &dyn ProgressSink) -> Result<Value> {
    let objective = Objective::parse(
        &string_arg(args, "objective").unwrap_or_else(|| "p95".to_owned()),
    )?;
    // The optimize stage asks its generator about a synthetic finding; the
    // agent does not have to know that slug, so stamp it here.
    let candidates =
        candidates_arg(args, &format!("optimize.{}", objective.slug()), "performance")?;

    let config = EvolveConfig::load(root)?;
    let manifest = inspect_project(root)?;
    let target = target::resolve(root, string_arg(args, "start"), string_arg(args, "url"), sink)
        .await?;
    let ctx = OptimizeContext {
        start_cmd: target.start_cmd,
        url: target.url,
        ready_timeout: Duration::from_secs(60),
        limits: None,
        test_cmd: string_arg(args, "test")
            .or_else(|| manifest.units.first().and_then(|u| u.commands.test.clone())),
        bench_duration: Duration::from_secs(u64_arg(args, "duration", 10)),
        bench_concurrency: u64_arg(args, "concurrency", 16) as usize,
        bench_repeats: u64_arg(args, "repeats", 3) as usize,
        max_variants: u64_arg(args, "max_variants", 4) as usize,
        min_gain_percent: f64_arg(args, "min_gain", 5.0),
        objective,
        diff_vectors: u64_arg(args, "diff_vectors", 24) as usize,
    };
    let generator = ProvidedPatchGenerator::new(candidates);
    let report = run_optimize(root, &ctx, &generator, &config, sink).await?;
    Ok(optimize_summary(&report, root))
}

async fn evolve(root: &Path, args: &Value, sink: &dyn ProgressSink) -> Result<Value> {
    let config = EvolveConfig::load(root)?;
    let manifest = inspect_project(root)?;
    let target = target::resolve(root, string_arg(args, "start"), string_arg(args, "url"), sink)
        .await?;
    let ctx = EvolveContext {
        start_cmd: target.start_cmd.clone(),
        url: target.url,
        profile: string_arg(args, "profile").unwrap_or_else(|| "quick".to_owned()),
        ready_timeout: Duration::from_secs(60),
        limits: None,
        max_findings: u64_arg(args, "max_findings", 3) as usize,
        test_cmd: string_arg(args, "test")
            .or_else(|| manifest.units.first().and_then(|u| u.commands.test.clone())),
    };

    // The configured bridge if there is one; otherwise the run still proves
    // and diagnoses, and says plainly that nothing proposed a patch.
    let generator: Box<dyn crate::fix::FixGenerator> =
        if config.evolve.generator.command.is_empty() {
            Box::new(ProvidedPatchGenerator::new(Vec::new()))
        } else {
            Box::new(
                crate::fix::BridgeGenerator::new(
                    config.evolve.generator.command.clone(),
                    Duration::from_secs(config.evolve.generator.timeout_secs),
                )
                .about_app(Some(target.start_cmd)),
            )
        };
    let report = run_evolve(root, &ctx, generator.as_ref(), &config, sink).await?;
    Ok(evolve_summary(&report, root))
}

fn open_pull_request(root: &Path, args: &Value) -> Result<Value> {
    let id = string_arg(args, "id").context("`id` is required: pick one from `promotions`")?;
    let record = promote::publish(root, &id)?;
    let json_path = crate::promote::model::promotions_dir(root).join(format!("{}.json", record.id));
    Ok(json!({
        "promotion": record.id,
        "branch": record.branch,
        "pushed_to": record.pushed_to,
        "pull_request": record.pull_request,
        "merged": record.merged,
        "note": record.reasons.last(),
        "report_md": crate::report::sidecar_display(&json_path),
    }))
}

fn verify_certificate(root: &Path, args: &Value) -> Result<Value> {
    let id = string_arg(args, "id").context("`id` is required: pick one from `promotions`")?;
    let record = promote::PromotionRecord::load(root, &id)?;
    let Some(cert) = record.certificate else {
        anyhow::bail!("promotion {id} carries no certificate");
    };
    Ok(json!({
        "promotion": id,
        "finding": cert.finding,
        "candidate": cert.candidate_id,
        "score": { "before": cert.score_before, "after": cert.score_after },
        "gate_valid": cert.is_valid(),
        "checksum_ok": cert.checksum_matches(),
        "signature_ok": cert.signature_valid(),
        "authentic": cert.is_authentic(),
        "public_key": cert.public_key,
    }))
}

async fn run_proof(
    root: &Path,
    args: &Value,
    profile: &str,
    run_id: &str,
    sink: &dyn ProgressSink,
) -> Result<ProofReport> {
    let target =
        target::resolve(root, string_arg(args, "start"), string_arg(args, "url"), sink).await?;
    let plan = ProofPlan::for_profile(profile, 512);
    run_proof_in_shadow(
        root,
        &format!("{run_id}-{}", std::process::id()),
        &target.start_cmd,
        &target.url,
        &plan,
        Duration::from_secs(60),
        None,
        false,
        sink,
    )
    .await
}

fn proof_summary(report: &ProofReport, artefact: Option<PathBuf>) -> Value {
    let faults: Vec<Value> = report
        .faults
        .iter()
        .map(|fault| {
            let failed: Vec<Value> = fault
                .checks
                .iter()
                .filter(|check| check.verdict != Verdict::Pass)
                .map(|check| {
                    json!({
                        "check": check.name,
                        "verdict": check.verdict,
                        "detail": check.detail,
                    })
                })
                .collect();
            json!({
                "fault": fault.fault,
                "verdict": fault.verdict,
                "failed_checks": failed,
            })
        })
        .collect();
    json!({
        "verdict": report.verdict,
        "robustness_score": report.robustness_score,
        "profile": report.profile,
        "faults": faults,
        "notes": report.notes,
        "report_file": artefact.map(|path| path.display().to_string()),
    })
}

fn diagnosis_summary(diagnosis: &Diagnosis, artefact: PathBuf) -> Value {
    let findings: Vec<Value> = diagnosis
        .findings
        .iter()
        .map(|finding| {
            json!({
                "id": finding.id,
                "title": finding.title,
                "severity": finding.severity,
                "confidence": finding.confidence,
                "family": finding.family,
                "symptom": finding.symptom,
                "root_cause": finding.root_cause,
                "remediation": finding.remediation,
                "evidence": finding.evidence,
            })
        })
        .collect();
    json!({
        "summary": diagnosis.summary,
        "robustness_score": diagnosis.robustness_score,
        "verdict": diagnosis.source_verdict,
        "findings": findings,
        "notes": diagnosis.notes,
        "report_file": artefact.display().to_string(),
        "next": if diagnosis.findings.is_empty() {
            "nothing to repair; `optimize` hunts for measurable speed instead"
        } else {
            "write patches for one finding id and call `fix` with them"
        },
    })
}

fn fix_summary(report: &FixReport, artefact: PathBuf) -> Value {
    let attempts: Vec<Value> = report
        .attempts
        .iter()
        .map(|attempt| {
            json!({
                "candidate": attempt.candidate_id,
                "decision": attempt.gate.decision,
                "reasons": attempt.gate.reasons,
                "score": {
                    "before": attempt.comparison.score_before,
                    "after": attempt.comparison.score_after,
                },
                "resolved_target": attempt.comparison.resolved_target,
                "new_high_findings": attempt.comparison.new_high_findings,
                "p95_ms": {
                    "before": attempt.comparison.p95_before_ms,
                    "after": attempt.comparison.p95_after_ms,
                },
                "tests": {
                    "before": attempt.comparison.tests_before,
                    "after": attempt.comparison.tests_after,
                },
                "apply_error": attempt.apply_error,
                // The accepted candidate is the one whose code matters here.
                "diff": if report.accepted.as_deref() == Some(attempt.candidate_id.as_str()) {
                    attempt.diff.clone()
                } else {
                    None
                },
            })
        })
        .collect();
    json!({
        "finding": report.target_finding,
        "score_before": report.score_before,
        "verdict_before": report.verdict_before,
        "accepted": report.accepted,
        "attempts": attempts,
        "proposal_file": report.proposal_path,
        "notes": report.notes,
        "report_file": artefact.display().to_string(),
        "report_md": crate::report::sidecar_display(&artefact),
    })
}

fn optimize_summary(report: &OptimizeReport, root: &Path) -> Value {
    let variants: Vec<Value> = report
        .variants
        .iter()
        .map(|variant| {
            json!({
                "candidate": variant.candidate_id,
                "gain_percent": variant.gain_percent,
                "significant": variant.significant,
                "eligible": variant.eligible,
                "tests_passed": variant.tests_passed,
                "behavior_equivalent": variant.behavior_equivalent,
                "p95_ms": variant.stats.as_ref().map(|stats| stats.p95_ms),
                "rps": variant.stats.as_ref().map(|stats| stats.rps),
                "note": variant.note,
                // Only the winner's code travels: the host asked for a
                // measurement, not for N copies of its own patches.
                "diff": if report.winner.as_deref() == Some(variant.candidate_id.as_str()) {
                    variant.diff.clone()
                } else {
                    None
                },
            })
        })
        .collect();
    let json_file = artefact_path(root, "optimize", &report.commit);
    json!({
        "objective": report.objective,
        "baseline": {
            "p95_ms": report.baseline.p95_ms,
            "p50_ms": report.baseline.p50_ms,
            "rps": report.baseline.rps,
            "robustness_score": report.baseline_score,
        },
        "bench_repeats": report.bench_repeats,
        "variants": variants,
        "winner": report.winner,
        "winner_gain_percent": report.winner_gain_percent,
        "promotion": report.promotion_id,
        "promotion_outcome": report.promotion_outcome,
        "notes": report.notes,
        "report_file": json_file.clone(),
        "report_md": crate::report::sidecar_display(Path::new(&json_file)),
    })
}

fn evolve_summary(report: &EvolveReport, root: &Path) -> Value {
    let outcomes: Vec<Value> = report
        .outcomes
        .iter()
        .map(|outcome| {
            json!({
                "finding": outcome.finding_id,
                "severity": outcome.severity,
                "candidates_generated": outcome.candidates_generated,
                "fix_accepted": outcome.fix_accepted,
                "promotion": outcome.promotion_id,
                "promotion_outcome": outcome.promotion_outcome,
                "note": outcome.note,
            })
        })
        .collect();
    json!({
        "generator": report.generator,
        "robustness_before": report.robustness_before,
        "verdict_before": report.verdict_before,
        "findings_total": report.findings_total,
        "findings_addressed": report.findings_addressed,
        "outcomes": outcomes,
        "notes": report.notes,
        "report_file": artefact_path(root, "evolve-runs", &report.commit),
    })
}

/// Where a stage that saves itself put its report.
fn artefact_path(root: &Path, kind: &str, commit: &str) -> String {
    root.join(crate::NAVIN_DIR)
        .join(kind)
        .join(format!("{commit}.json"))
        .display()
        .to_string()
}

fn root_of(args: &Value, default_root: &Path) -> Result<PathBuf> {
    let given = args.get("path").and_then(Value::as_str).unwrap_or("").trim();
    let base = if given.is_empty() {
        default_root.to_path_buf()
    } else {
        PathBuf::from(given)
    };
    base.canonicalize()
        .with_context(|| format!("no such project directory: {}", base.display()))
}

fn string_arg(args: &Value, key: &str) -> Option<String> {
    let text = args.get(key)?.as_str()?.trim();
    if text.is_empty() {
        return None;
    }
    Some(text.to_owned())
}

fn u64_arg(args: &Value, key: &str, fallback: u64) -> u64 {
    args.get(key).and_then(Value::as_u64).unwrap_or(fallback)
}

fn f64_arg(args: &Value, key: &str, fallback: f64) -> f64 {
    args.get(key).and_then(Value::as_f64).unwrap_or(fallback)
}

fn candidates_arg(
    args: &Value,
    target_finding: &str,
    default_family: &str,
) -> Result<Vec<FixCandidate>> {
    let Some(raw) = args.get("candidates") else {
        return Ok(Vec::new());
    };
    if raw.is_null() {
        return Ok(Vec::new());
    }
    let incoming: Vec<IncomingCandidate> = serde_json::from_value(raw.clone()).context(
        "`candidates` must be an array of objects with id, rationale and patch \
         (patch is {kind:\"files\", edits:[{path, contents}]} or {kind:\"unified_diff\", diff})",
    )?;
    Ok(incoming
        .into_iter()
        .map(|candidate| FixCandidate {
            id: candidate.id,
            target_finding: target_finding.to_owned(),
            rationale: candidate.rationale,
            family: candidate.family.unwrap_or_else(|| default_family.to_owned()),
            patch: candidate.patch,
        })
        .collect())
}

fn object_schema(properties: Value, required: &[&str]) -> Value {
    json!({
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": false,
    })
}

fn path_property() -> Value {
    json!({
        "type": "string",
        "description": "Project root. Defaults to the directory the server was started in.",
    })
}

fn profile_property() -> Value {
    json!({
        "type": "string",
        "enum": ["quick", "standard", "deep"],
        "description": "How hard to attack the app. `quick` for iteration, `standard` (default) \
                        for a real verdict, `deep` before a release.",
    })
}

fn start_property() -> Value {
    json!({
        "type": "string",
        "description": "Override the auto-detected start command, e.g. `npm run dev`. Leave \
                        out unless detection picked the wrong program.",
    })
}

fn url_property() -> Value {
    json!({
        "type": "string",
        "description": "Override the auto-detected local URL, e.g. `http://127.0.0.1:3000/`. \
                        The engine finds it by booting the app and watching which port it \
                        opens, so this is rarely needed.",
    })
}

fn test_property() -> Value {
    json!({
        "type": "string",
        "description": "Override the auto-detected test command. A patch that breaks the \
                        test suite is rejected.",
    })
}

fn candidates_property(description: &str) -> Value {
    json!({
        "type": "array",
        "description": description,
        "items": {
            "type": "object",
            "required": ["id", "rationale", "patch"],
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Short slug, unique within this call, e.g. `cache-headers`.",
                },
                "rationale": {
                    "type": "string",
                    "description": "One sentence: why this should work. It is recorded in the certificate.",
                },
                "family": {
                    "type": "string",
                    "description": "reliability, performance, security or correctness. Policy \
                                    may restrict which families can be promoted.",
                },
                "patch": {
                    "type": "object",
                    "description": "Whole-file writes, or a unified diff applied with `git apply`.",
                    "required": ["kind"],
                    "properties": {
                        "kind": { "type": "string", "enum": ["files", "unified_diff"] },
                        "edits": {
                            "type": "array",
                            "description": "For kind `files`: the full new contents of each file.",
                            "items": {
                                "type": "object",
                                "required": ["path", "contents"],
                                "properties": {
                                    "path": {
                                        "type": "string",
                                        "description": "Relative to the project root. No absolute paths, no `..`.",
                                    },
                                    "contents": { "type": "string" },
                                },
                            },
                        },
                        "diff": {
                            "type": "string",
                            "description": "For kind `unified_diff`: the patch text.",
                        },
                    },
                },
            },
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_advertised_tool_is_known_and_described() {
        let catalog = catalog();
        assert_eq!(catalog.len(), NAMES.len());
        for tool in &catalog {
            let name = tool["name"].as_str().unwrap();
            assert!(is_known(name), "{name} is advertised but not routed");
            assert!(
                tool["description"].as_str().unwrap().len() > 40,
                "{name} needs a description a model can act on"
            );
            assert_eq!(tool["inputSchema"]["type"], json!("object"));
        }
        assert!(!is_known("rm_minus_rf"));
    }

    #[test]
    fn required_arguments_are_declared() {
        let catalog = catalog();
        let by_name = |name: &str| {
            catalog
                .iter()
                .find(|tool| tool["name"] == json!(name))
                .cloned()
                .unwrap()
        };
        assert_eq!(
            by_name("fix")["inputSchema"]["required"],
            json!(["finding", "candidates"])
        );
        assert_eq!(by_name("verify_certificate")["inputSchema"]["required"], json!(["id"]));
        // Everything else must run with no arguments at all: detection is
        // the engine's job, not the caller's.
        assert_eq!(by_name("prove")["inputSchema"]["required"], json!([]));
        assert_eq!(by_name("optimize")["inputSchema"]["required"], json!([]));
    }

    #[test]
    fn candidates_are_stamped_with_the_finding_under_test() {
        let args = json!({
            "candidates": [{
                "id": "add-timeout",
                "rationale": "bound the upstream call",
                "patch": { "kind": "files", "edits": [{ "path": "app.py", "contents": "x = 1\n" }] },
            }],
        });
        let candidates = candidates_arg(&args, "crash.load", "reliability").unwrap();
        assert_eq!(candidates.len(), 1);
        assert_eq!(candidates[0].target_finding, "crash.load");
        // The default family applies when the model does not pick one.
        assert_eq!(candidates[0].family, "reliability");
    }

    #[test]
    fn a_declared_family_is_kept() {
        let args = json!({
            "candidates": [{
                "id": "cache",
                "rationale": "reuse the parsed template",
                "family": "performance",
                "patch": { "kind": "unified_diff", "diff": "--- a\n+++ b\n" },
            }],
        });
        let candidates = candidates_arg(&args, "optimize.p95", "performance").unwrap();
        assert_eq!(candidates[0].family, "performance");
        assert!(matches!(candidates[0].patch, FixPatch::UnifiedDiff { .. }));
    }

    #[test]
    fn no_candidates_is_an_empty_list_not_an_error() {
        assert!(candidates_arg(&json!({}), "crash.load", "reliability")
            .unwrap()
            .is_empty());
        assert!(
            candidates_arg(&json!({ "candidates": null }), "crash.load", "reliability")
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn a_malformed_candidate_explains_the_shape() {
        let args = json!({ "candidates": [{ "id": "x" }] });
        let error = candidates_arg(&args, "crash.load", "reliability").unwrap_err();
        assert!(format!("{error:#}").contains("unified_diff"));
    }

    #[test]
    fn the_path_argument_falls_back_to_the_server_root() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().canonicalize().unwrap();
        assert_eq!(root_of(&json!({}), &root).unwrap(), root);
        assert_eq!(root_of(&json!({ "path": "  " }), &root).unwrap(), root);
        assert_eq!(
            root_of(&json!({ "path": root.display().to_string() }), Path::new("/nope")).unwrap(),
            root
        );
        assert!(root_of(&json!({ "path": "/definitely/not/here" }), &root).is_err());
    }

    #[test]
    fn scalar_arguments_fall_back_to_defaults() {
        let args = json!({ "duration": 30, "min_gain": 12.5, "profile": "deep", "start": "  " });
        assert_eq!(u64_arg(&args, "duration", 10), 30);
        assert_eq!(u64_arg(&args, "repeats", 3), 3);
        assert_eq!(f64_arg(&args, "min_gain", 5.0), 12.5);
        assert_eq!(string_arg(&args, "profile").as_deref(), Some("deep"));
        // Blank strings mean "you decide", like an omitted argument.
        assert_eq!(string_arg(&args, "start"), None);
    }
}
