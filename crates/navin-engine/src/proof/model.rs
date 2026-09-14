//! Data model for the Proof engine: what a fault verdict is, how checks
//! roll up into a fault outcome, and how outcomes roll up into a report.

use serde::{Deserialize, Serialize};
use std::path::Path;

pub const PROOF_SCHEMA: &str = "navin-proof/v1";

/// Outcome of a single check or a whole fault.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Verdict {
    /// Behaved well: survived and stayed within bounds.
    Pass,
    /// Survived but degraded past a soft threshold; worth a look.
    Weak,
    /// Crashed, never recovered, or blew a hard bound.
    Fail,
}

impl Verdict {
    /// The pessimistic combination: a report is only as strong as its
    /// weakest check.
    pub fn worst(self, other: Verdict) -> Verdict {
        use Verdict::*;
        match (self, other) {
            (Fail, _) | (_, Fail) => Fail,
            (Weak, _) | (_, Weak) => Weak,
            _ => Pass,
        }
    }

    fn score(self) -> u32 {
        match self {
            Verdict::Pass => 100,
            Verdict::Weak => 60,
            Verdict::Fail => 0,
        }
    }
}

/// One invariant that was checked against a fault (e.g. "process survived",
/// "recovered within 10s", "error rate <= 1%").
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CheckResult {
    pub name: String,
    pub verdict: Verdict,
    pub detail: String,
    /// The measured value, when the check is quantitative.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub measured: Option<f64>,
    /// The threshold the measurement was compared against.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub threshold: Option<f64>,
}

impl CheckResult {
    pub fn new(name: impl Into<String>, verdict: Verdict, detail: impl Into<String>) -> Self {
        CheckResult {
            name: name.into(),
            verdict,
            detail: detail.into(),
            measured: None,
            threshold: None,
        }
    }

    pub fn with_metric(mut self, measured: f64, threshold: f64) -> Self {
        self.measured = Some(measured);
        self.threshold = Some(threshold);
        self
    }
}

/// The result of injecting one fault and checking the invariants after it.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FaultOutcome {
    pub fault: String,
    pub description: String,
    pub checks: Vec<CheckResult>,
    pub verdict: Verdict,
    /// Free-form evidence (log tails, counts) for the report reader.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence: Vec<String>,
}

impl FaultOutcome {
    pub fn new(fault: impl Into<String>, description: impl Into<String>, checks: Vec<CheckResult>) -> Self {
        let verdict = checks
            .iter()
            .fold(Verdict::Pass, |acc, c| acc.worst(c.verdict));
        FaultOutcome {
            fault: fault.into(),
            description: description.into(),
            checks,
            verdict,
            evidence: Vec::new(),
        }
    }

    pub fn with_evidence(mut self, evidence: Vec<String>) -> Self {
        self.evidence = evidence;
        self
    }
}

/// The full robustness report, persisted under `.navin/proofs/<commit>.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProofReport {
    pub schema: String,
    pub commit: String,
    pub profile: String,
    pub measured_in: String,
    pub collected_at: String,
    pub faults: Vec<FaultOutcome>,
    /// Worst verdict across every fault.
    pub verdict: Verdict,
    /// 0-100, the mean of per-fault scores (100 pass / 60 weak / 0 fail).
    pub robustness_score: u8,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub notes: Vec<String>,
}

impl ProofReport {
    pub fn build(
        commit: &str,
        profile: &str,
        measured_in: &Path,
        faults: Vec<FaultOutcome>,
        notes: Vec<String>,
    ) -> Self {
        let verdict = faults
            .iter()
            .fold(Verdict::Pass, |acc, f| acc.worst(f.verdict));
        let robustness_score = if faults.is_empty() {
            0
        } else {
            let sum: u32 = faults.iter().map(|f| f.verdict.score()).sum();
            (sum / faults.len() as u32) as u8
        };
        ProofReport {
            schema: PROOF_SCHEMA.to_owned(),
            commit: commit.to_owned(),
            profile: profile.to_owned(),
            measured_in: measured_in.display().to_string(),
            collected_at: crate::proof::now_epoch(),
            faults,
            verdict,
            robustness_score,
            notes,
        }
    }

    pub fn save(&self, project_root: &Path) -> anyhow::Result<std::path::PathBuf> {
        use anyhow::Context;
        let dir = project_root.join(crate::NAVIN_DIR).join("proofs");
        std::fs::create_dir_all(&dir)
            .with_context(|| format!("cannot create {}", dir.display()))?;
        let path = dir.join(format!("{}.json", self.commit));
        std::fs::write(&path, serde_json::to_string_pretty(self)?)
            .with_context(|| format!("cannot write {}", path.display()))?;
        Ok(path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn worst_verdict_is_pessimistic() {
        assert_eq!(Verdict::Pass.worst(Verdict::Weak), Verdict::Weak);
        assert_eq!(Verdict::Weak.worst(Verdict::Fail), Verdict::Fail);
        assert_eq!(Verdict::Pass.worst(Verdict::Pass), Verdict::Pass);
    }

    #[test]
    fn fault_verdict_is_the_worst_check() {
        let outcome = FaultOutcome::new(
            "load",
            "hammered the service",
            vec![
                CheckResult::new("no_crash", Verdict::Pass, "alive"),
                CheckResult::new("error_rate", Verdict::Weak, "3% errors"),
            ],
        );
        assert_eq!(outcome.verdict, Verdict::Weak);
    }

    #[test]
    fn score_is_the_mean_of_fault_scores() {
        let report = ProofReport::build(
            "abc",
            "standard",
            Path::new("/tmp/shadow"),
            vec![
                FaultOutcome::new("a", "", vec![CheckResult::new("x", Verdict::Pass, "")]),
                FaultOutcome::new("b", "", vec![CheckResult::new("y", Verdict::Weak, "")]),
            ],
            vec![],
        );
        // (100 + 60) / 2 = 80, overall verdict weak.
        assert_eq!(report.robustness_score, 80);
        assert_eq!(report.verdict, Verdict::Weak);
    }
}
