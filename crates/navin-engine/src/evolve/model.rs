//! Report for a full evolve run: one pass of prove -> diagnose -> (per
//! finding) fix -> promote. It links to the detailed artefacts each stage
//! saved, rather than duplicating them, and summarises what happened.

use serde::{Deserialize, Serialize};

use crate::proof::Verdict;

pub const EVOLVE_SCHEMA: &str = "navin-evolve-run/v1";

/// What the pipeline did about one diagnosed finding.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FindingOutcome {
    pub finding_id: String,
    pub severity: String,
    pub family: String,
    pub candidates_generated: usize,
    pub fix_accepted: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub promotion_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub promotion_outcome: Option<String>,
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvolveReport {
    pub schema: String,
    pub commit: String,
    pub collected_at: String,
    pub profile: String,
    pub generator: String,
    pub robustness_before: u8,
    pub verdict_before: Verdict,
    pub findings_total: usize,
    pub findings_addressed: usize,
    pub outcomes: Vec<FindingOutcome>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub notes: Vec<String>,
}

impl EvolveReport {
    pub fn save(&self, project_root: &std::path::Path) -> anyhow::Result<std::path::PathBuf> {
        use anyhow::Context;
        let dir = project_root.join(crate::NAVIN_DIR).join("evolve-runs");
        std::fs::create_dir_all(&dir)
            .with_context(|| format!("cannot create {}", dir.display()))?;
        let path = dir.join(format!("{}.json", self.commit));
        std::fs::write(&path, serde_json::to_string_pretty(self)?)
            .with_context(|| format!("cannot write {}", path.display()))?;
        Ok(path)
    }
}
