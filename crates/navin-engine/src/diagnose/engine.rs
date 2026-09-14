//! Diagnose orchestration: proof report + service log -> Diagnosis.
//!
//! The core is a pure function ([`diagnose`]) so it is trivially testable;
//! the project-aware wrapper reads the log the proof left behind.

use std::path::Path;

use crate::policy::config::{EvolveConfig, SignatureSpec};
use crate::proof::model::ProofReport;

use super::model::Diagnosis;
use super::rules::{diagnose_symptom, incidental_findings};
use super::log_scan;
use super::symptoms::extract;

/// Pure diagnosis: correlate the proof with the log text. Passing an empty
/// log is valid; findings simply carry Medium confidence without it.
pub fn diagnose(report: &ProofReport, log_text: &str) -> Diagnosis {
    diagnose_with(report, log_text, &[])
}

/// Diagnosis with project-declared log signatures next to the built-ins.
pub fn diagnose_with(
    report: &ProofReport,
    log_text: &str,
    signatures: &[SignatureSpec],
) -> Diagnosis {
    let symptoms = extract(report);
    let signals = log_scan::scan_with(log_text, signatures);

    let mut findings: Vec<_> =
        symptoms.iter().map(|s| diagnose_symptom(s, &signals)).collect();
    findings.extend(incidental_findings(&symptoms, &signals));

    let mut notes = Vec::new();
    if symptoms.is_empty() && signals.is_empty() {
        notes.push("proof passed cleanly and no known error signatures were logged".to_owned());
    }
    if log_text.is_empty() {
        notes.push("no service log was available; findings rely on proof checks alone".to_owned());
    }

    Diagnosis::build(
        &report.commit,
        report.verdict,
        report.robustness_score,
        findings,
        notes,
    )
}

/// Diagnose using the log the proof engine wrote for this project, with
/// the `[[signatures]]` the project declared in `.navin/evolve.toml`.
pub fn diagnose_project(project_root: &Path, report: &ProofReport) -> Diagnosis {
    let log_path = crate::engine_dir(project_root)
        .join("logs")
        .join("proof-service.log");
    let log_text = std::fs::read_to_string(&log_path).unwrap_or_default();
    let signatures = EvolveConfig::load(project_root)
        .map(|config| config.signatures)
        .unwrap_or_default();
    diagnose_with(report, &log_text, &signatures)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::proof::model::{CheckResult, FaultOutcome, ProofReport, Verdict};
    use std::path::Path;

    fn report(faults: Vec<FaultOutcome>) -> ProofReport {
        ProofReport::build("deadbeef", "standard", Path::new("/tmp/s"), faults, vec![])
    }

    #[test]
    fn crash_symptom_plus_panic_log_yields_a_critical_high_confidence_finding() {
        let report = report(vec![FaultOutcome::new(
            "load",
            "storm",
            vec![CheckResult::new("no_crash", Verdict::Fail, "not serving")],
        )]);
        let log = "serving on 3000\nthread 'tokio-worker' panicked at 'index out of bounds'\n";
        let diag = diagnose(&report, log);

        assert_eq!(diag.findings.len(), 1);
        let f = &diag.findings[0];
        assert_eq!(f.id, "crash.load");
        assert_eq!(f.severity, super::super::model::Severity::Critical);
        assert_eq!(f.confidence, super::super::model::Confidence::High);
        assert!(diag.summary.contains("1 critical"));
    }

    #[test]
    fn a_project_signature_becomes_a_finding() {
        let report = report(vec![FaultOutcome::new(
            "load",
            "storm",
            vec![CheckResult::new("no_crash", Verdict::Pass, "alive")],
        )]);
        let signatures = vec![crate::policy::config::SignatureSpec {
            marker: "quota exceeded".to_owned(),
            id: "quota".to_owned(),
            family: "reliability".to_owned(),
            cause: "the tenant quota was exhausted".to_owned(),
        }];
        let diag = diagnose_with(&report, "ERROR quota exceeded for tenant 42\n", &signatures);
        assert_eq!(diag.findings.len(), 1);
        assert_eq!(diag.findings[0].id, "log.quota");
        assert!(diag.findings[0].root_cause.contains("tenant quota"));
    }

    #[test]
    fn a_clean_proof_produces_no_findings() {
        let report = report(vec![FaultOutcome::new(
            "load",
            "storm",
            vec![CheckResult::new("no_crash", Verdict::Pass, "alive")],
        )]);
        let diag = diagnose(&report, "all requests served\n");
        assert!(diag.findings.is_empty());
        assert!(diag.summary.contains("No robustness issues"));
    }
}
