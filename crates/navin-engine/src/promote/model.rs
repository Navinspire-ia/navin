//! Data model for promotion and certification. A certificate bundles the
//! proof evidence behind a change; a record captures exactly what was done
//! to the workspace, so any promotion can be explained and reversed.

use serde::{Deserialize, Serialize};

use crate::proof::Verdict;

pub const CERTIFICATE_SCHEMA: &str = "navin-certificate/v1";
pub const PROMOTION_SCHEMA: &str = "navin-promotion/v1";

/// Tamper-evident evidence bundle for a promoted change. The `checksum` is
/// a fast integrity digest; `signature` is an Ed25519 signature over every
/// payload field (see [`super::identity::signing_message`]), issued with
/// the workspace identity key so the evidence can be verified offline.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Certificate {
    pub schema: String,
    pub engine_version: String,
    pub finding: String,
    pub candidate_id: String,
    pub family: String,
    pub commit_before: String,
    pub score_before: u8,
    pub score_after: u8,
    pub verdict_after: Verdict,
    pub resolved_target: bool,
    pub issued_at: String,
    pub checksum: String,
    /// Hex Ed25519 signature; empty on certificates issued before signing.
    #[serde(default)]
    pub signature: String,
    /// Hex Ed25519 public key of the issuing engine.
    #[serde(default)]
    pub public_key: String,
}

impl Certificate {
    /// A certificate is valid only when the fix truly helped: the proof
    /// passed, the score did not drop, and the targeted finding is gone.
    pub fn is_valid(&self) -> bool {
        self.verdict_after == Verdict::Pass
            && self.score_after >= self.score_before
            && self.resolved_target
    }

    /// Recompute the checksum and confirm it matches (detects tampering).
    pub fn checksum_matches(&self) -> bool {
        self.checksum == compute_checksum(self)
    }

    /// Check the embedded Ed25519 signature against the payload.
    pub fn signature_valid(&self) -> bool {
        super::identity::verify(self)
    }

    /// Full authenticity check: gate outcome, integrity and signature.
    pub fn is_authentic(&self) -> bool {
        self.is_valid() && self.checksum_matches() && self.signature_valid()
    }
}

/// Deterministic integrity digest over everything but the checksum field.
pub fn compute_checksum(cert: &Certificate) -> String {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    cert.schema.hash(&mut hasher);
    cert.engine_version.hash(&mut hasher);
    cert.finding.hash(&mut hasher);
    cert.candidate_id.hash(&mut hasher);
    cert.family.hash(&mut hasher);
    cert.commit_before.hash(&mut hasher);
    cert.score_before.hash(&mut hasher);
    cert.score_after.hash(&mut hasher);
    format!("{:?}", cert.verdict_after).hash(&mut hasher);
    cert.resolved_target.hash(&mut hasher);
    cert.issued_at.hash(&mut hasher);
    format!("{:016x}", hasher.finish())
}

/// What the promotion actually did to the workspace.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PromotionOutcome {
    /// Policy refused; the workspace was not touched.
    Blocked,
    /// A branch was created with the commit, but not merged.
    BranchOnly,
    /// The change was merged into the active branch.
    Merged,
}

/// A durable record of one promotion, saved under `.navin/promotions/`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PromotionRecord {
    pub schema: String,
    pub id: String,
    pub finding: String,
    pub candidate_id: String,
    /// Policy mode in force (safe | trusted | autonomous).
    pub mode: String,
    pub outcome: PromotionOutcome,
    /// Why the gate decided this way.
    pub reasons: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub branch: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub commit_sha: Option<String>,
    /// Active branch HEAD before the promotion (rollback reference).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prev_head: Option<String>,
    pub merged: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub certificate: Option<Certificate>,
    /// Unified diff of the promoted commit, so accepting is never blind.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub diff: Option<String>,
    /// Remote branch, once the promotion has been pushed.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pushed_to: Option<String>,
    /// Pull request opened for the branch, or the compare link to open one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pull_request: Option<String>,
    pub created_at: String,
    /// Set once the change has been rolled back.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rolled_back_at: Option<String>,
}

impl PromotionRecord {
    pub fn save(&self, project_root: &std::path::Path) -> anyhow::Result<std::path::PathBuf> {
        use anyhow::Context;
        let dir = promotions_dir(project_root);
        std::fs::create_dir_all(&dir)
            .with_context(|| format!("cannot create {}", dir.display()))?;
        let path = dir.join(format!("{}.json", self.id));
        std::fs::write(&path, serde_json::to_string_pretty(self)?)
            .with_context(|| format!("cannot write {}", path.display()))?;
        crate::report::write_sidecar(&path, &crate::report::promotion(self))?;
        Ok(path)
    }

    pub fn load(project_root: &std::path::Path, id: &str) -> anyhow::Result<Self> {
        use anyhow::Context;
        let path = promotions_dir(project_root).join(format!("{id}.json"));
        let text = std::fs::read_to_string(&path)
            .with_context(|| format!("no promotion record at {}", path.display()))?;
        Ok(serde_json::from_str(&text)?)
    }
}

pub fn promotions_dir(project_root: &std::path::Path) -> std::path::PathBuf {
    project_root.join(crate::NAVIN_DIR).join("promotions")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cert() -> Certificate {
        let mut c = Certificate {
            schema: CERTIFICATE_SCHEMA.to_owned(),
            engine_version: "0.1.0".to_owned(),
            finding: "crash.load".to_owned(),
            candidate_id: "cand-1".to_owned(),
            family: "reliability".to_owned(),
            commit_before: "abc".to_owned(),
            score_before: 50,
            score_after: 100,
            verdict_after: Verdict::Pass,
            resolved_target: true,
            issued_at: "epoch:1".to_owned(),
            checksum: String::new(),
            signature: String::new(),
            public_key: String::new(),
        };
        c.checksum = compute_checksum(&c);
        c
    }

    #[test]
    fn a_passing_improving_cert_is_valid() {
        assert!(cert().is_valid());
    }

    #[test]
    fn a_failing_cert_is_invalid() {
        let mut c = cert();
        c.verdict_after = Verdict::Fail;
        assert!(!c.is_valid());
    }

    #[test]
    fn tampering_breaks_the_checksum() {
        let mut c = cert();
        assert!(c.checksum_matches());
        c.score_after = 10; // edited after issuance
        assert!(!c.checksum_matches());
    }
}
