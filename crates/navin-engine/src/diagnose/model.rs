//! Data model for diagnosis: findings with a severity, a confidence, the
//! symptom observed, the root-cause hypothesis, and a remediation family
//! that later feeds the Fix/Evolve stage.

use serde::{Deserialize, Serialize};
use std::path::Path;

use crate::proof::Verdict;

pub const DIAGNOSIS_SCHEMA: &str = "navin-diagnosis/v1";

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Severity {
    // Ordered worst-first so `sort` puts the scariest findings on top.
    Critical,
    High,
    Medium,
    Low,
    Info,
}

impl Severity {
    pub fn label(self) -> &'static str {
        match self {
            Severity::Critical => "critical",
            Severity::High => "high",
            Severity::Medium => "medium",
            Severity::Low => "low",
            Severity::Info => "info",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Confidence {
    /// A symptom plus a matching log signature agree on the cause.
    High,
    /// A symptom with no corroborating log evidence.
    Medium,
    /// A weak or incidental signal.
    Low,
}

/// One diagnosed issue: what was seen, why it likely happened, and the
/// direction a fix would take.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Finding {
    /// Stable slug, e.g. `crash.load`, so findings can be tracked over time.
    pub id: String,
    pub title: String,
    pub severity: Severity,
    pub confidence: Confidence,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub related_fault: Option<String>,
    /// What was observed.
    pub symptom: String,
    /// The most likely cause.
    pub root_cause: String,
    /// Suggested direction for a fix (not applied here).
    pub remediation: String,
    /// Policy family a fix would belong to (reliability, performance, ...).
    pub family: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence: Vec<String>,
}

/// The full diagnosis, persisted under `.navin/diagnoses/<commit>.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Diagnosis {
    pub schema: String,
    pub commit: String,
    pub collected_at: String,
    /// Verdict carried over from the proof this diagnosis is based on.
    pub source_verdict: Verdict,
    pub robustness_score: u8,
    pub findings: Vec<Finding>,
    pub summary: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub notes: Vec<String>,
}

impl Diagnosis {
    pub fn build(
        commit: &str,
        source_verdict: Verdict,
        robustness_score: u8,
        mut findings: Vec<Finding>,
        notes: Vec<String>,
    ) -> Self {
        // Worst severity first; stable so the order within a severity is
        // the order rules produced them.
        findings.sort_by_key(|finding| finding.severity);
        let summary = summarize(&findings, robustness_score);
        Diagnosis {
            schema: DIAGNOSIS_SCHEMA.to_owned(),
            commit: commit.to_owned(),
            collected_at: crate::proof::now_epoch(),
            source_verdict,
            robustness_score,
            findings,
            summary,
            notes,
        }
    }

    pub fn save(&self, project_root: &Path) -> anyhow::Result<std::path::PathBuf> {
        use anyhow::Context;
        let dir = project_root.join(crate::NAVIN_DIR).join("diagnoses");
        std::fs::create_dir_all(&dir)
            .with_context(|| format!("cannot create {}", dir.display()))?;
        let path = dir.join(format!("{}.json", self.commit));
        std::fs::write(&path, serde_json::to_string_pretty(self)?)
            .with_context(|| format!("cannot write {}", path.display()))?;
        Ok(path)
    }
}

fn summarize(findings: &[Finding], score: u8) -> String {
    if findings.is_empty() {
        return format!("No robustness issues diagnosed. Robustness {score}/100.");
    }
    let mut counts = [0u32; 5];
    for f in findings {
        counts[f.severity as usize] += 1;
    }
    let parts: Vec<String> = [
        (Severity::Critical, counts[0]),
        (Severity::High, counts[1]),
        (Severity::Medium, counts[2]),
        (Severity::Low, counts[3]),
        (Severity::Info, counts[4]),
    ]
    .into_iter()
    .filter(|(_, n)| *n > 0)
    .map(|(sev, n)| format!("{n} {}", sev.label()))
    .collect();
    format!(
        "{} finding(s): {}. Robustness {score}/100.",
        findings.len(),
        parts.join(", ")
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn finding(id: &str, severity: Severity) -> Finding {
        Finding {
            id: id.to_owned(),
            title: id.to_owned(),
            severity,
            confidence: Confidence::Medium,
            related_fault: None,
            symptom: String::new(),
            root_cause: String::new(),
            remediation: String::new(),
            family: "reliability".to_owned(),
            evidence: vec![],
        }
    }

    #[test]
    fn findings_are_sorted_worst_first() {
        let d = Diagnosis::build(
            "abc",
            Verdict::Fail,
            40,
            vec![
                finding("medium", Severity::Medium),
                finding("critical", Severity::Critical),
                finding("high", Severity::High),
            ],
            vec![],
        );
        let ids: Vec<&str> = d.findings.iter().map(|f| f.id.as_str()).collect();
        assert_eq!(ids, vec!["critical", "high", "medium"]);
        assert!(d.summary.contains("1 critical"));
    }

    #[test]
    fn clean_run_summarizes_as_no_issues() {
        let d = Diagnosis::build("abc", Verdict::Pass, 100, vec![], vec![]);
        assert!(d.summary.contains("No robustness issues"));
    }
}
