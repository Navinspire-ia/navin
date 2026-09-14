//! Data model for the Fix stage: a candidate is a patch plus its rationale;
//! a comparison holds the before/after evidence; a decision is the gate's
//! verdict with its reasons. Accepted candidates become promotion proposals,
//! never direct writes to the workspace.

use serde::{Deserialize, Serialize};

use crate::proof::Verdict;

pub const FIX_SCHEMA: &str = "navin-fix/v1";

/// Overwrite (or create) one file with exact contents. Whole-file edits
/// keep application deterministic and easy to audit.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileEdit {
    /// Path relative to the project root; `..` and absolute paths rejected.
    pub path: String,
    pub contents: String,
}

/// How a candidate mutates the code.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum FixPatch {
    /// A set of whole-file writes.
    Files { edits: Vec<FileEdit> },
    /// A unified diff applied with `git apply` inside the shadow.
    UnifiedDiff { diff: String },
}

/// A proposed fix for a specific diagnosed finding.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FixCandidate {
    pub id: String,
    /// Finding id this candidate is meant to resolve (e.g. `crash.load`).
    pub target_finding: String,
    pub rationale: String,
    /// Remediation family, carried from the finding for auditability.
    pub family: String,
    pub patch: FixPatch,
}

/// The measured difference a candidate made.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Comparison {
    pub score_before: u8,
    pub score_after: u8,
    pub verdict_before: Verdict,
    pub verdict_after: Verdict,
    /// The targeted finding is gone (or reduced) after the patch.
    pub resolved_target: bool,
    /// New critical/high findings the patch introduced (ids).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub new_high_findings: Vec<String>,
    /// Load P95 latency in ms, when the proof measured it.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub p95_before_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub p95_after_ms: Option<f64>,
    /// Project test suite outcome, when a test command is known.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tests_before: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tests_after: Option<bool>,
    /// Business invariants from evolve.toml, when any are declared.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub invariants_before: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub invariants_after: Option<bool>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Decision {
    Accept,
    Reject,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GateResult {
    pub decision: Decision,
    /// Human-readable reasons, both for and against.
    pub reasons: Vec<String>,
}

impl GateResult {
    pub fn accepted(&self) -> bool {
        self.decision == Decision::Accept
    }
}

/// One candidate's full outcome: what it changed and whether it passed.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FixAttempt {
    pub candidate_id: String,
    pub target_finding: String,
    pub rationale: String,
    pub comparison: Comparison,
    pub gate: GateResult,
    /// Finding ids present after the patch (for the report reader).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub after_findings: Vec<String>,
    /// Set when applying the patch itself failed (candidate dead on arrival).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub apply_error: Option<String>,
    /// Unified diff of what the candidate changed, captured in the shadow.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub diff: Option<String>,
}

/// The result of a fix campaign against one finding.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FixReport {
    pub schema: String,
    pub commit: String,
    pub collected_at: String,
    pub target_finding: String,
    pub score_before: u8,
    pub verdict_before: Verdict,
    pub attempts: Vec<FixAttempt>,
    /// Candidate id that was accepted, if any.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub accepted: Option<String>,
    /// Where the accepted proposal was written (never the workspace).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub proposal_path: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub notes: Vec<String>,
}

impl FixReport {
    pub fn save(&self, project_root: &std::path::Path) -> anyhow::Result<std::path::PathBuf> {
        use anyhow::Context;
        let dir = project_root.join(crate::NAVIN_DIR).join("fixes");
        std::fs::create_dir_all(&dir)
            .with_context(|| format!("cannot create {}", dir.display()))?;
        let path = dir.join(format!("{}.json", self.commit));
        std::fs::write(&path, serde_json::to_string_pretty(self)?)
            .with_context(|| format!("cannot write {}", path.display()))?;
        crate::report::write_sidecar(&path, &crate::report::fix(self))?;
        Ok(path)
    }
}
