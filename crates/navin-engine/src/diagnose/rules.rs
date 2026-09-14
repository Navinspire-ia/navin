//! Correlation rules: turn a symptom (plus any corroborating log signal)
//! into a root-cause finding. A matching log signal raises confidence and
//! sharpens the cause; without one, the finding is honest but tentative.

use super::log_scan::{self, SignalHit};
use super::model::{Confidence, Finding, Severity};
use super::symptoms::{Symptom, SymptomKind};

/// Apply every rule and return the findings for a single symptom.
pub fn diagnose_symptom(symptom: &Symptom, signals: &[SignalHit]) -> Finding {
    let fault = symptom.fault.clone();
    let fault_slug = fault.as_deref().unwrap_or("unknown");
    match symptom.kind {
        SymptomKind::CrashAfterFault => crash(symptom, signals, fault_slug),
        SymptomKind::NoRecovery => no_recovery(symptom, signals, fault_slug),
        SymptomKind::SlowRecovery => slow_recovery(symptom, fault_slug),
        SymptomKind::HighErrorRate => high_error_rate(symptom, signals, fault_slug),
        SymptomKind::MemoryOvershoot => memory_overshoot(symptom, signals, fault_slug),
        SymptomKind::TailLatencySpike => tail_latency(symptom, fault_slug),
    }
}

/// Log signals that never matched a symptom become low-severity findings:
/// worth surfacing, but the invariants still held.
pub fn incidental_findings(symptoms: &[Symptom], signals: &[SignalHit]) -> Vec<Finding> {
    let symptomatic_families: Vec<&str> = symptoms
        .iter()
        .flat_map(|s| related_signal_ids(s.kind))
        .collect();
    signals
        .iter()
        .filter(|hit| !symptomatic_families.contains(&hit.id.as_str()))
        .map(|hit| Finding {
            id: format!("log.{}", hit.id),
            title: format!("Log signal: {}", hit.cause),
            severity: Severity::Low,
            confidence: Confidence::Low,
            related_fault: None,
            symptom: "a known error signature appeared in the logs".to_owned(),
            root_cause: hit.cause.to_owned(),
            remediation: "investigate the logged error even though invariants held".to_owned(),
            family: hit.family.to_owned(),
            evidence: vec![hit.line.clone()],
        })
        .collect()
}

/// Which log-signal ids a symptom is expected to be explained by, so we do
/// not also report them as incidental.
fn related_signal_ids(kind: SymptomKind) -> Vec<&'static str> {
    match kind {
        SymptomKind::CrashAfterFault => vec!["rust_panic", "py_exception", "segfault", "uncaught"],
        SymptomKind::NoRecovery => vec!["port_in_use"],
        SymptomKind::HighErrorRate => vec!["fd_exhaustion", "conn_refused"],
        SymptomKind::MemoryOvershoot => vec!["oom"],
        SymptomKind::SlowRecovery | SymptomKind::TailLatencySpike => vec![],
    }
}

fn crash(symptom: &Symptom, signals: &[SignalHit], fault: &str) -> Finding {
    let hit = ["rust_panic", "py_exception", "segfault", "uncaught"]
        .iter()
        .find_map(|id| log_scan::find(signals, id));
    let (confidence, cause, family, mut evidence) = match hit {
        Some(h) => (
            Confidence::High,
            format!("under the `{fault}` fault, {}", h.cause),
            h.family.to_owned(),
            vec![h.line.clone()],
        ),
        None => (
            Confidence::Medium,
            format!("the process stopped answering under the `{fault}` fault; no crash signature was logged"),
            "reliability".to_owned(),
            vec![],
        ),
    };
    evidence.extend(symptom.evidence.clone());
    Finding {
        id: format!("crash.{fault}"),
        title: format!("Service crashes under `{fault}`"),
        severity: Severity::Critical,
        confidence,
        related_fault: Some(fault.to_owned()),
        symptom: symptom.detail.clone(),
        root_cause: cause,
        remediation: "harden the failing path: validate inputs and catch/handle errors instead of aborting".to_owned(),
        family,
        evidence,
    }
}

fn no_recovery(symptom: &Symptom, signals: &[SignalHit], fault: &str) -> Finding {
    let port = log_scan::find(signals, "port_in_use");
    let (confidence, cause, remediation, evidence) = match port {
        Some(h) => (
            Confidence::High,
            "the previous instance did not release its listen port before restart".to_owned(),
            "add a graceful shutdown that closes the listener, or bind with address reuse".to_owned(),
            vec![h.line.clone()],
        ),
        None => (
            Confidence::Medium,
            "the service did not become healthy again within the recovery bound".to_owned(),
            "ensure the process exits cleanly and restarts fast; check for stuck resources on shutdown".to_owned(),
            vec![],
        ),
    };
    Finding {
        id: format!("no_recovery.{fault}"),
        title: "Service does not recover after a kill".to_owned(),
        severity: Severity::Critical,
        confidence,
        related_fault: Some(fault.to_owned()),
        symptom: symptom.detail.clone(),
        root_cause: cause,
        remediation,
        family: "reliability".to_owned(),
        evidence,
    }
}

fn slow_recovery(symptom: &Symptom, fault: &str) -> Finding {
    Finding {
        id: format!("slow_recovery.{fault}"),
        title: "Recovery is slow".to_owned(),
        severity: Severity::Medium,
        confidence: Confidence::Medium,
        related_fault: Some(fault.to_owned()),
        symptom: symptom.detail.clone(),
        root_cause: "the service takes a large fraction of the recovery budget to become healthy (slow startup or warmup)".to_owned(),
        remediation: "reduce startup work: lazy-init heavy components, or add a readiness gate".to_owned(),
        family: "performance".to_owned(),
        evidence: symptom.evidence.clone(),
    }
}

fn high_error_rate(symptom: &Symptom, signals: &[SignalHit], fault: &str) -> Finding {
    let (confidence, cause, family, remediation, mut evidence) =
        if let Some(h) = log_scan::find(signals, "fd_exhaustion") {
            (
                Confidence::High,
                "file-descriptor exhaustion under load".to_owned(),
                "reliability".to_owned(),
                "reuse connections / a bounded pool, and raise the fd limit for the service".to_owned(),
                vec![h.line.clone()],
            )
        } else if let Some(h) = log_scan::find(signals, "conn_refused") {
            (
                Confidence::High,
                "a downstream dependency was refusing connections under load".to_owned(),
                "reliability".to_owned(),
                "add timeouts, retries with backoff, and a circuit breaker around the dependency".to_owned(),
                vec![h.line.clone()],
            )
        } else {
            (
                Confidence::Medium,
                "requests failed above the accepted ratio under load".to_owned(),
                "reliability".to_owned(),
                "add backpressure / a concurrency limit so overload sheds gracefully instead of erroring".to_owned(),
                vec![],
            )
        };
    evidence.extend(symptom.evidence.clone());
    Finding {
        id: format!("error_rate.{fault}"),
        title: "Elevated error rate under load".to_owned(),
        severity: Severity::High,
        confidence,
        related_fault: Some(fault.to_owned()),
        symptom: symptom.detail.clone(),
        root_cause: cause,
        remediation,
        family,
        evidence,
    }
}

fn memory_overshoot(symptom: &Symptom, signals: &[SignalHit], fault: &str) -> Finding {
    let oom = log_scan::find(signals, "oom");
    let (confidence, cause, mut evidence) = match oom {
        Some(h) => (Confidence::High, h.cause.to_owned(), vec![h.line.clone()]),
        None => (
            Confidence::Medium,
            "resident memory grew past the ceiling while under stress".to_owned(),
            vec![],
        ),
    };
    evidence.extend(symptom.evidence.clone());
    Finding {
        id: format!("memory.{fault}"),
        title: "Memory grows past its ceiling under load".to_owned(),
        severity: Severity::High,
        confidence,
        related_fault: Some(fault.to_owned()),
        symptom: symptom.detail.clone(),
        root_cause: cause,
        remediation: "cap buffers/caches, stream large payloads, and check for per-request leaks".to_owned(),
        family: "memory".to_owned(),
        evidence,
    }
}

fn tail_latency(symptom: &Symptom, fault: &str) -> Finding {
    Finding {
        id: format!("tail_latency.{fault}"),
        title: "Heavy tail latency under load".to_owned(),
        severity: Severity::Medium,
        confidence: Confidence::Medium,
        related_fault: Some(fault.to_owned()),
        symptom: symptom.detail.clone(),
        root_cause: "P99 is far above P95, pointing at lock contention, GC pauses, or a single-threaded bottleneck".to_owned(),
        remediation: "profile the slow path; parallelize the blocking work or remove the shared lock on the hot path".to_owned(),
        family: "performance".to_owned(),
        evidence: symptom.evidence.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::diagnose::log_scan::scan;

    fn symptom(kind: SymptomKind, fault: &str) -> Symptom {
        Symptom { kind, fault: Some(fault.to_owned()), detail: "detail".to_owned(), evidence: vec![] }
    }

    #[test]
    fn crash_with_panic_log_is_high_confidence() {
        let signals = scan("thread 'main' panicked at src/lib.rs:5");
        let finding = diagnose_symptom(&symptom(SymptomKind::CrashAfterFault, "load"), &signals);
        assert_eq!(finding.severity, Severity::Critical);
        assert_eq!(finding.confidence, Confidence::High);
        assert!(finding.root_cause.contains("panic"));
    }

    #[test]
    fn crash_without_log_is_medium_confidence() {
        let finding = diagnose_symptom(&symptom(SymptomKind::CrashAfterFault, "malformed"), &[]);
        assert_eq!(finding.confidence, Confidence::Medium);
        assert_eq!(finding.family, "reliability");
    }

    #[test]
    fn no_recovery_blames_the_port_when_logged() {
        let signals = scan("Error: listen EADDRINUSE address already in use");
        let finding = diagnose_symptom(&symptom(SymptomKind::NoRecovery, "kill_recovery"), &signals);
        assert_eq!(finding.confidence, Confidence::High);
        assert!(finding.root_cause.contains("port"));
    }

    #[test]
    fn unrelated_log_signal_becomes_an_incidental_low_finding() {
        let signals = scan("warning: deadlock avoided in module x");
        let findings = incidental_findings(&[], &signals);
        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].severity, Severity::Low);
        assert_eq!(findings[0].family, "concurrency");
    }
}
