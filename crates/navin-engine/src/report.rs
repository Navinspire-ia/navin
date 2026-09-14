//! A human-readable sibling of every measurement artefact.
//!
//! The JSON stays the source of truth. The markdown is what an agent cites
//! and what a person opens in the IDE: same numbers, no extra UI.

use std::fmt::Write as _;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};

use crate::fix::model::{Decision, FixReport};
use crate::optimize::model::OptimizeReport;
use crate::promote::model::PromotionRecord;
use crate::proof::Verdict;

/// Path of the markdown sitting next to a JSON artefact.
pub fn sidecar_path(json_path: &Path) -> PathBuf {
    json_path.with_extension("md")
}

/// Write `markdown` beside `json_path` (same stem, `.md`).
pub fn write_sidecar(json_path: &Path, markdown: &str) -> Result<PathBuf> {
    let path = sidecar_path(json_path);
    std::fs::write(&path, markdown)
        .with_context(|| format!("cannot write {}", path.display()))?;
    Ok(path)
}

/// Display path of the markdown that would sit next to this JSON file.
pub fn sidecar_display(json_path: &Path) -> String {
    sidecar_path(json_path).display().to_string()
}

pub fn optimize(report: &OptimizeReport) -> String {
    let mut out = String::new();
    let _ = writeln!(
        out,
        "# Optimize {}\n\ncommit `{}` · robustness {}/100",
        report.objective.slug(),
        short(&report.commit),
        report.baseline_score
    );
    let _ = writeln!(
        out,
        "\n**Baseline**  P95 {:.1}{} ms · {:.1}{} RPS · {} window(s)",
        report.baseline.p95_ms,
        std_suffix(report.baseline_p95_std_ms),
        report.baseline.rps,
        std_suffix(report.baseline_rps_std),
        report.bench_repeats.max(1)
    );

    match (&report.winner, report.winner_gain_percent) {
        (Some(id), Some(gain)) => {
            let _ = writeln!(out, "\n**Winner**  `{id}` · {:+.1}%", gain);
        }
        _ => {
            let _ = writeln!(out, "\n**Winner**  none (no variant cleared the gate)");
        }
    }

    if !report.variants.is_empty() {
        let _ = writeln!(out, "\n## Candidates\n");
        let _ = writeln!(
            out,
            "| candidate | gain | tests | behaviour | eligible | note |"
        );
        let _ = writeln!(
            out,
            "| --- | --- | --- | --- | --- | --- |"
        );
        for variant in &report.variants {
            let gain = variant
                .gain_percent
                .map(|g| format!("{g:+.1}%"))
                .unwrap_or_else(|| "-".to_owned());
            let _ = writeln!(
                out,
                "| `{}` | {} | {} | {} | {} | {} |",
                variant.candidate_id,
                gain,
                yn(variant.tests_passed),
                yn(variant.behavior_equivalent),
                if variant.eligible { "yes" } else { "no" },
                escape_cell(&variant.note),
            );
        }
    }

    if let Some(winner) = &report.winner {
        if let Some(variant) = report.variants.iter().find(|v| v.candidate_id == *winner) {
            if let Some(diff) = &variant.diff {
                let _ = writeln!(out, "\n## Diff (`{winner}`)\n\n```diff\n{diff}\n```");
            }
        }
    }

    if let Some(id) = &report.promotion_id {
        let _ = writeln!(
            out,
            "\n## Promotion\n\n`{id}` · {}\n\n```\nnavin-engine pr --id {id}\nnavin-engine merge --id {id}\n```",
            report.promotion_outcome.as_deref().unwrap_or("recorded")
        );
    }

    notes(&mut out, &report.notes);
    out
}

pub fn fix(report: &FixReport) -> String {
    let mut out = String::new();
    let _ = writeln!(
        out,
        "# Fix `{}`\n\ncommit `{}` · before {}/100 ({})",
        report.target_finding,
        short(&report.commit),
        report.score_before,
        verdict(report.verdict_before)
    );
    match &report.accepted {
        Some(id) => {
            let _ = writeln!(out, "\n**Accepted**  `{id}`");
        }
        None => {
            let _ = writeln!(out, "\n**Accepted**  none");
        }
    }

    if !report.attempts.is_empty() {
        let _ = writeln!(out, "\n## Attempts\n");
        for attempt in &report.attempts {
            let decision = match attempt.gate.decision {
                Decision::Accept => "accept",
                Decision::Reject => "reject",
            };
            let _ = writeln!(out, "### `{}` · {decision}\n", attempt.candidate_id);
            if !attempt.rationale.is_empty() {
                let _ = writeln!(out, "{}\n", attempt.rationale);
            }
            let cmp = &attempt.comparison;
            let _ = writeln!(
                out,
                "- score {}/100 -> {}/100 ({})",
                cmp.score_before,
                cmp.score_after,
                verdict(cmp.verdict_after)
            );
            let _ = writeln!(
                out,
                "- target resolved: {} · tests: {} -> {} · new high findings: {}",
                if cmp.resolved_target { "yes" } else { "no" },
                yn(cmp.tests_before),
                yn(cmp.tests_after),
                if cmp.new_high_findings.is_empty() {
                    "none".to_owned()
                } else {
                    cmp.new_high_findings.join(", ")
                }
            );
            if !attempt.gate.reasons.is_empty() {
                let _ = writeln!(out, "- reasons: {}", attempt.gate.reasons.join("; "));
            }
            if let Some(err) = &attempt.apply_error {
                let _ = writeln!(out, "- apply error: {err}");
            }
            let _ = writeln!(out);
        }
    }

    if let Some(id) = &report.accepted {
        if let Some(attempt) = report.attempts.iter().find(|a| a.candidate_id == *id) {
            if let Some(diff) = &attempt.diff {
                let _ = writeln!(out, "## Diff (`{id}`)\n\n```diff\n{diff}\n```\n");
            }
        }
    }

    notes(&mut out, &report.notes);
    out
}

pub fn promotion(record: &PromotionRecord) -> String {
    let mut out = String::new();
    let outcome = match record.outcome {
        crate::promote::model::PromotionOutcome::Blocked => "blocked",
        crate::promote::model::PromotionOutcome::BranchOnly => "branch only",
        crate::promote::model::PromotionOutcome::Merged => "merged",
    };
    let _ = writeln!(
        out,
        "# Promotion `{}`\n\n`{}` for `{}` · {outcome} · mode `{}`",
        record.id, record.candidate_id, record.finding, record.mode
    );
    if let Some(branch) = &record.branch {
        let _ = writeln!(out, "\n- branch `{branch}`");
    }
    if let Some(sha) = &record.commit_sha {
        let _ = writeln!(out, "- commit `{sha}`");
    }
    if let Some(remote) = &record.pushed_to {
        let _ = writeln!(out, "- pushed to `{remote}`");
    }
    if let Some(pr) = &record.pull_request {
        let _ = writeln!(out, "- pull request: {pr}");
    }
    if let Some(cert) = &record.certificate {
        let _ = writeln!(
            out,
            "\n## Certificate\n\n- robustness {}/100 -> {}/100 ({})\n- target resolved: {}\n- checksum `{}`\n- `navin-engine verify-cert . --id {}`",
            cert.score_before,
            cert.score_after,
            verdict(cert.verdict_after),
            if cert.resolved_target { "yes" } else { "no" },
            cert.checksum,
            record.id
        );
    }
    if let Some(diff) = &record.diff {
        let _ = writeln!(out, "\n## Diff\n\n```diff\n{diff}\n```");
    }
    if !record.reasons.is_empty() {
        let _ = writeln!(out, "\n## Reasons\n");
        for reason in &record.reasons {
            let _ = writeln!(out, "- {reason}");
        }
    }
    if record.rolled_back_at.is_some() {
        let _ = writeln!(out, "\nThis promotion was rolled back.");
    }
    let _ = writeln!(
        out,
        "\n```\nnavin-engine pr --id {}\nnavin-engine merge --id {}\nnavin-engine rollback --id {}\n```",
        record.id, record.id, record.id
    );
    out
}

fn notes(out: &mut String, items: &[String]) {
    if items.is_empty() {
        return;
    }
    let _ = writeln!(out, "\n## Notes\n");
    for note in items {
        let _ = writeln!(out, "- {note}");
    }
}

fn verdict(value: Verdict) -> &'static str {
    match value {
        Verdict::Pass => "pass",
        Verdict::Weak => "weak",
        Verdict::Fail => "fail",
    }
}

fn yn(value: Option<bool>) -> &'static str {
    match value {
        Some(true) => "pass",
        Some(false) => "fail",
        None => "-",
    }
}

fn std_suffix(std: Option<f64>) -> String {
    std.map(|v| format!(" ± {v:.1}")).unwrap_or_default()
}

fn short(commit: &str) -> &str {
    let end = commit.len().min(12);
    &commit[..end]
}

fn escape_cell(text: &str) -> String {
    text.replace('|', "\\|").replace('\n', " ")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::baseline::latency::LatencyStats;
    use crate::fix::model::{Comparison, FixAttempt, FixReport, GateResult, FIX_SCHEMA};
    use crate::optimize::model::{Objective, VariantOutcome, OPTIMIZE_SCHEMA};
    use crate::promote::model::{PromotionOutcome, PROMOTION_SCHEMA};
    use crate::proof::Verdict;

    fn stats(p95: f64, rps: f64) -> LatencyStats {
        LatencyStats {
            requests: 100,
            failures: 0,
            p50_ms: p95 / 2.0,
            p95_ms: p95,
            p99_ms: p95 * 1.1,
            rps,
        }
    }

    #[test]
    fn an_optimize_report_names_the_winner_and_the_rejection() {
        let text = optimize(&OptimizeReport {
            schema: OPTIMIZE_SCHEMA.to_owned(),
            commit: "abc123def456".to_owned(),
            collected_at: "epoch:1".to_owned(),
            objective: Objective::P95,
            baseline: stats(55.8, 277.0),
            baseline_p95_std_ms: Some(1.3),
            baseline_rps_std: Some(0.0),
            bench_repeats: 2,
            invariants_checked: 0,
            baseline_score: 100,
            variants: vec![
                VariantOutcome {
                    candidate_id: "cache-the-payload".to_owned(),
                    rationale: String::new(),
                    stats: Some(stats(8.0, 1502.0)),
                    p95_std_ms: Some(2.7),
                    rps_std: None,
                    tests_passed: Some(true),
                    invariants_ok: None,
                    gain_percent: Some(85.7),
                    significant: Some(true),
                    behavior_equivalent: Some(true),
                    eligible: true,
                    note: "P95 8.0 ms".to_owned(),
                    diff: Some("diff --git a/server.py b/server.py\n+CACHE".to_owned()),
                },
                VariantOutcome {
                    candidate_id: "drop-half".to_owned(),
                    rationale: String::new(),
                    stats: Some(stats(8.0, 1476.0)),
                    p95_std_ms: None,
                    rps_std: None,
                    tests_passed: Some(false),
                    invariants_ok: None,
                    gain_percent: Some(85.7),
                    significant: Some(true),
                    behavior_equivalent: None,
                    eligible: false,
                    note: "project test suite fails".to_owned(),
                    diff: None,
                },
            ],
            winner: Some("cache-the-payload".to_owned()),
            winner_gain_percent: Some(85.7),
            promotion_id: Some("promo-1".to_owned()),
            promotion_outcome: Some("BranchOnly".to_owned()),
            notes: vec![],
        });

        assert!(text.contains("# Optimize p95"), "{text}");
        assert!(text.contains("`cache-the-payload` · +85.7%"), "{text}");
        assert!(text.contains("| `drop-half` | +85.7% | fail |"), "{text}");
        assert!(text.contains("```diff"), "{text}");
        assert!(text.contains("navin-engine pr --id promo-1"), "{text}");
        assert!(!text.contains('\u{2014}') && !text.contains('\u{2013}'));
    }

    #[test]
    fn a_fix_report_shows_accept_and_reject() {
        let attempt = |id: &str, decision: Decision, resolved: bool, diff: Option<String>| {
            FixAttempt {
                candidate_id: id.to_owned(),
                target_finding: "crash.load".to_owned(),
                rationale: "bound the queue".to_owned(),
                comparison: Comparison {
                    score_before: 40,
                    score_after: if resolved { 100 } else { 40 },
                    verdict_before: Verdict::Fail,
                    verdict_after: if resolved { Verdict::Pass } else { Verdict::Fail },
                    resolved_target: resolved,
                    new_high_findings: vec![],
                    p95_before_ms: None,
                    p95_after_ms: None,
                    tests_before: Some(true),
                    tests_after: Some(true),
                    invariants_before: None,
                    invariants_after: None,
                },
                gate: GateResult { decision, reasons: vec!["measured".to_owned()] },
                after_findings: vec![],
                apply_error: None,
                diff,
            }
        };
        let text = fix(&FixReport {
            schema: FIX_SCHEMA.to_owned(),
            commit: "deadbeef".to_owned(),
            collected_at: "epoch:1".to_owned(),
            target_finding: "crash.load".to_owned(),
            score_before: 40,
            verdict_before: Verdict::Fail,
            attempts: vec![
                attempt("good", Decision::Accept, true, Some("+fn bound()".to_owned())),
                attempt("bad", Decision::Reject, false, None),
            ],
            accepted: Some("good".to_owned()),
            proposal_path: None,
            notes: vec![],
        });

        assert!(text.contains("# Fix `crash.load`"), "{text}");
        assert!(text.contains("**Accepted**  `good`"), "{text}");
        assert!(text.contains("### `bad` · reject"), "{text}");
        assert!(text.contains("+fn bound()"), "{text}");
    }

    #[test]
    fn a_promotion_carries_the_commands_to_ship_it() {
        let text = promotion(&PromotionRecord {
            schema: PROMOTION_SCHEMA.to_owned(),
            id: "promo-x".to_owned(),
            finding: "optimize.p95".to_owned(),
            candidate_id: "cache".to_owned(),
            mode: "safe".to_owned(),
            outcome: PromotionOutcome::BranchOnly,
            reasons: vec!["safe mode".to_owned()],
            branch: Some("navin/evolve/x".to_owned()),
            commit_sha: Some("abc".to_owned()),
            prev_head: None,
            merged: false,
            certificate: None,
            diff: Some("+x".to_owned()),
            pushed_to: None,
            pull_request: None,
            created_at: "epoch:1".to_owned(),
            rolled_back_at: None,
        });
        assert!(text.contains("# Promotion `promo-x`"), "{text}");
        assert!(text.contains("navin-engine pr --id promo-x"), "{text}");
        assert!(text.contains("```diff"), "{text}");
    }

    #[test]
    fn the_sidecar_sits_next_to_the_json() {
        let tmp = tempfile::tempdir().unwrap();
        let json = tmp.path().join("abc.json");
        std::fs::write(&json, "{}").unwrap();
        let md = write_sidecar(&json, "# hi\n").unwrap();
        assert_eq!(md.extension().unwrap(), "md");
        assert_eq!(std::fs::read_to_string(&md).unwrap(), "# hi\n");
        assert_eq!(sidecar_display(&json), md.display().to_string());
    }
}
