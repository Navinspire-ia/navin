//! The acceptance gate. A candidate is promoted only when it actually
//! helped: the targeted finding is gone, robustness did not drop, no new
//! serious findings appeared, and latency did not regress. Every rejection
//! lists exactly why, so a near-miss is debuggable.

use super::model::{Comparison, Decision, GateResult};

#[derive(Debug, Clone)]
pub struct GateConfig {
    /// Require the targeted finding to be resolved (the whole point).
    pub require_target_resolved: bool,
    /// Fractional latency regression tolerated on P95 (0.5 = +50%).
    pub latency_regression_tolerance: f64,
}

impl Default for GateConfig {
    fn default() -> Self {
        GateConfig {
            require_target_resolved: true,
            latency_regression_tolerance: 0.5,
        }
    }
}

pub fn evaluate(cmp: &Comparison, cfg: &GateConfig) -> GateResult {
    let mut against: Vec<String> = Vec::new();
    let mut fores: Vec<String> = Vec::new();

    if cfg.require_target_resolved {
        if cmp.resolved_target {
            fores.push("targeted finding resolved".to_owned());
        } else {
            against.push("targeted finding not resolved".to_owned());
        }
    }

    if cmp.score_after < cmp.score_before {
        against.push(format!(
            "robustness dropped {} -> {}",
            cmp.score_before, cmp.score_after
        ));
    } else {
        fores.push(format!(
            "robustness {} -> {}",
            cmp.score_before, cmp.score_after
        ));
    }

    if !cmp.new_high_findings.is_empty() {
        against.push(format!(
            "introduced serious findings: {}",
            cmp.new_high_findings.join(", ")
        ));
    }

    match (cmp.tests_before, cmp.tests_after) {
        (_, Some(true)) => fores.push("project test suite passes".to_owned()),
        // A suite that was already red is a pre-existing condition, not a
        // regression introduced by this candidate.
        (Some(false), Some(false)) => {
            fores.push("test suite was already failing before the patch".to_owned())
        }
        (_, Some(false)) => against.push("project test suite fails after the patch".to_owned()),
        _ => {}
    }

    // Business invariants follow the same pre-existing-condition rule.
    match (cmp.invariants_before, cmp.invariants_after) {
        (_, Some(true)) => fores.push("business invariants hold".to_owned()),
        (Some(false), Some(false)) => {
            fores.push("business invariants were already failing before the patch".to_owned())
        }
        (_, Some(false)) => {
            against.push("business invariants fail after the patch".to_owned())
        }
        _ => {}
    }

    if let (Some(before), Some(after)) = (cmp.p95_before_ms, cmp.p95_after_ms) {
        let ceiling = before * (1.0 + cfg.latency_regression_tolerance);
        if before > 0.0 && after > ceiling {
            against.push(format!(
                "P95 latency regressed {before:.1} ms -> {after:.1} ms (> +{:.0}%)",
                cfg.latency_regression_tolerance * 100.0
            ));
        } else {
            fores.push(format!("P95 latency {before:.1} ms -> {after:.1} ms"));
        }
    }

    let decision = if against.is_empty() { Decision::Accept } else { Decision::Reject };
    let mut reasons = Vec::new();
    if !against.is_empty() {
        reasons.push(format!("against: {}", against.join("; ")));
    }
    if !fores.is_empty() {
        reasons.push(format!("for: {}", fores.join("; ")));
    }
    GateResult { decision, reasons }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::proof::Verdict;

    fn base() -> Comparison {
        Comparison {
            score_before: 0,
            score_after: 100,
            verdict_before: Verdict::Fail,
            verdict_after: Verdict::Pass,
            resolved_target: true,
            new_high_findings: vec![],
            p95_before_ms: Some(10.0),
            p95_after_ms: Some(11.0),
            tests_before: None,
            tests_after: None,
            invariants_before: None,
            invariants_after: None,
        }
    }

    #[test]
    fn a_clean_improvement_is_accepted() {
        let gate = evaluate(&base(), &GateConfig::default());
        assert_eq!(gate.decision, Decision::Accept);
    }

    #[test]
    fn unresolved_target_is_rejected() {
        let mut cmp = base();
        cmp.resolved_target = false;
        assert_eq!(evaluate(&cmp, &GateConfig::default()).decision, Decision::Reject);
    }

    #[test]
    fn new_serious_finding_is_rejected() {
        let mut cmp = base();
        cmp.new_high_findings = vec!["memory.load".to_owned()];
        let gate = evaluate(&cmp, &GateConfig::default());
        assert_eq!(gate.decision, Decision::Reject);
        assert!(gate.reasons.iter().any(|r| r.contains("memory.load")));
    }

    #[test]
    fn latency_regression_is_rejected() {
        let mut cmp = base();
        cmp.p95_before_ms = Some(10.0);
        cmp.p95_after_ms = Some(20.0); // +100% > +50%
        assert_eq!(evaluate(&cmp, &GateConfig::default()).decision, Decision::Reject);
    }

    #[test]
    fn score_drop_is_rejected() {
        let mut cmp = base();
        cmp.score_before = 80;
        cmp.score_after = 60;
        assert_eq!(evaluate(&cmp, &GateConfig::default()).decision, Decision::Reject);
    }

    #[test]
    fn breaking_the_test_suite_is_rejected() {
        let mut cmp = base();
        cmp.tests_before = Some(true);
        cmp.tests_after = Some(false);
        let gate = evaluate(&cmp, &GateConfig::default());
        assert_eq!(gate.decision, Decision::Reject);
        assert!(gate.reasons.iter().any(|r| r.contains("test suite")));
    }

    #[test]
    fn a_suite_already_red_is_not_held_against_the_candidate() {
        let mut cmp = base();
        cmp.tests_before = Some(false);
        cmp.tests_after = Some(false);
        assert_eq!(evaluate(&cmp, &GateConfig::default()).decision, Decision::Accept);
    }

    #[test]
    fn breaking_a_business_invariant_is_rejected() {
        let mut cmp = base();
        cmp.invariants_before = Some(true);
        cmp.invariants_after = Some(false);
        let gate = evaluate(&cmp, &GateConfig::default());
        assert_eq!(gate.decision, Decision::Reject);
        assert!(gate.reasons.iter().any(|r| r.contains("invariants fail")));
    }

    #[test]
    fn invariants_already_red_are_not_held_against_the_candidate() {
        let mut cmp = base();
        cmp.invariants_before = Some(false);
        cmp.invariants_after = Some(false);
        assert_eq!(evaluate(&cmp, &GateConfig::default()).decision, Decision::Accept);
    }

    #[test]
    fn passing_tests_support_acceptance() {
        let mut cmp = base();
        cmp.tests_before = Some(true);
        cmp.tests_after = Some(true);
        let gate = evaluate(&cmp, &GateConfig::default());
        assert_eq!(gate.decision, Decision::Accept);
        assert!(gate.reasons.iter().any(|r| r.contains("test suite passes")));
    }
}
