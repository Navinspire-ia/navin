//! Turn a [`ProofReport`] into structured symptoms. This layer only reads
//! the proof: it decides *what went wrong*, the rules decide *why*.

use crate::proof::model::{ProofReport, Verdict};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SymptomKind {
    /// The service stopped answering after a fault.
    CrashAfterFault,
    /// After a kill, it never came back within the bound.
    NoRecovery,
    /// It came back, but slowly (soft warning).
    SlowRecovery,
    /// Failure ratio under load exceeded the threshold.
    HighErrorRate,
    /// Peak RSS under stress went past the ceiling.
    MemoryOvershoot,
    /// P99 dwarfs P95: a heavy tail.
    TailLatencySpike,
}

#[derive(Debug, Clone)]
pub struct Symptom {
    pub kind: SymptomKind,
    pub fault: Option<String>,
    pub detail: String,
    pub evidence: Vec<String>,
}

/// Extract every symptom implied by the proof's checks and evidence.
pub fn extract(report: &ProofReport) -> Vec<Symptom> {
    let mut symptoms = Vec::new();
    for fault in &report.faults {
        for check in &fault.checks {
            match (check.name.as_str(), check.verdict) {
                ("no_crash", Verdict::Fail) => symptoms.push(Symptom {
                    kind: SymptomKind::CrashAfterFault,
                    fault: Some(fault.fault.clone()),
                    detail: check.detail.clone(),
                    evidence: fault.evidence.clone(),
                }),
                ("recovery", Verdict::Fail) => symptoms.push(Symptom {
                    kind: SymptomKind::NoRecovery,
                    fault: Some(fault.fault.clone()),
                    detail: check.detail.clone(),
                    evidence: fault.evidence.clone(),
                }),
                ("recovery", Verdict::Weak) => symptoms.push(Symptom {
                    kind: SymptomKind::SlowRecovery,
                    fault: Some(fault.fault.clone()),
                    detail: check.detail.clone(),
                    evidence: fault.evidence.clone(),
                }),
                ("error_rate", Verdict::Fail | Verdict::Weak) => symptoms.push(Symptom {
                    kind: SymptomKind::HighErrorRate,
                    fault: Some(fault.fault.clone()),
                    detail: check.detail.clone(),
                    evidence: fault.evidence.clone(),
                }),
                ("resource_bound", Verdict::Fail | Verdict::Weak) => symptoms.push(Symptom {
                    kind: SymptomKind::MemoryOvershoot,
                    fault: Some(fault.fault.clone()),
                    detail: check.detail.clone(),
                    evidence: fault.evidence.clone(),
                }),
                _ => {}
            }
        }

        // Tail-latency spike is not a check; derive it from load evidence.
        if fault.fault == "load" {
            if let Some((p95, p99)) = parse_p95_p99(&fault.evidence) {
                // A heavy tail: P99 an order of magnitude past P95 and clearly
                // slow in absolute terms (avoids flagging noise on fast paths).
                if p99 >= p95 * 10.0 && p99 >= 250.0 {
                    symptoms.push(Symptom {
                        kind: SymptomKind::TailLatencySpike,
                        fault: Some(fault.fault.clone()),
                        detail: format!("P95 {p95} ms vs P99 {p99} ms"),
                        evidence: fault.evidence.clone(),
                    });
                }
            }
        }
    }
    symptoms
}

/// Pull `p95 <n> ms` and `p99 <n> ms` out of the load fault's evidence
/// strings, e.g. "11853 req, p95 13.4 ms, p99 1040 ms, 987.8 rps".
fn parse_p95_p99(evidence: &[String]) -> Option<(f64, f64)> {
    let text = evidence.join(" ").to_lowercase();
    let p95 = parse_metric(&text, "p95")?;
    let p99 = parse_metric(&text, "p99")?;
    Some((p95, p99))
}

fn parse_metric(text: &str, key: &str) -> Option<f64> {
    let idx = text.find(key)? + key.len();
    let rest = text[idx..].trim_start();
    let number: String = rest
        .chars()
        .take_while(|c| c.is_ascii_digit() || *c == '.')
        .collect();
    number.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::proof::model::{CheckResult, FaultOutcome, ProofReport};
    use std::path::Path;

    fn report_with(faults: Vec<FaultOutcome>) -> ProofReport {
        ProofReport::build("abc", "standard", Path::new("/tmp/s"), faults, vec![])
    }

    #[test]
    fn crash_and_no_recovery_are_extracted() {
        let report = report_with(vec![
            FaultOutcome::new(
                "malformed",
                "",
                vec![CheckResult::new("no_crash", Verdict::Fail, "dead")],
            ),
            FaultOutcome::new(
                "kill_recovery",
                "",
                vec![CheckResult::new("recovery", Verdict::Fail, "never came back")],
            ),
        ]);
        let symptoms = extract(&report);
        let kinds: Vec<SymptomKind> = symptoms.iter().map(|s| s.kind).collect();
        assert!(kinds.contains(&SymptomKind::CrashAfterFault));
        assert!(kinds.contains(&SymptomKind::NoRecovery));
    }

    #[test]
    fn tail_latency_spike_is_derived_from_evidence() {
        let load = FaultOutcome::new(
            "load",
            "",
            vec![CheckResult::new("no_crash", Verdict::Pass, "alive")],
        )
        .with_evidence(vec!["11853 req, p95 13.4 ms, p99 1040 ms, 987.8 rps".to_owned()]);
        let symptoms = extract(&report_with(vec![load]));
        assert!(symptoms.iter().any(|s| s.kind == SymptomKind::TailLatencySpike));
    }

    #[test]
    fn fast_and_flat_latency_is_not_flagged() {
        let load = FaultOutcome::new("load", "", vec![])
            .with_evidence(vec!["1000 req, p95 5.0 ms, p99 6.0 ms, 900 rps".to_owned()]);
        let symptoms = extract(&report_with(vec![load]));
        assert!(!symptoms.iter().any(|s| s.kind == SymptomKind::TailLatencySpike));
    }
}
