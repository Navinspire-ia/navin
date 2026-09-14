//! Build a signed certificate from a fix report and its accepted candidate.

use anyhow::{Context, Result};
use std::path::Path;

use crate::fix::model::FixReport;
use crate::fix::FixCandidate;

use super::identity;
use super::model::{compute_checksum, Certificate, CERTIFICATE_SCHEMA};

/// Issue a certificate for `candidate` using the matching accepted attempt
/// in `report`, signed with the workspace Ed25519 identity. Fails if the
/// candidate was not the accepted one.
pub fn issue(
    project_root: &Path,
    report: &FixReport,
    candidate: &FixCandidate,
) -> Result<Certificate> {
    let attempt = report
        .attempts
        .iter()
        .find(|a| a.candidate_id == candidate.id)
        .with_context(|| format!("no attempt for candidate `{}` in the fix report", candidate.id))?;

    let mut cert = Certificate {
        schema: CERTIFICATE_SCHEMA.to_owned(),
        engine_version: crate::ENGINE_VERSION.to_owned(),
        finding: report.target_finding.clone(),
        candidate_id: candidate.id.clone(),
        family: candidate.family.clone(),
        commit_before: report.commit.clone(),
        score_before: attempt.comparison.score_before,
        score_after: attempt.comparison.score_after,
        verdict_after: attempt.comparison.verdict_after,
        resolved_target: attempt.comparison.resolved_target,
        issued_at: crate::proof::now_epoch(),
        checksum: String::new(),
        signature: String::new(),
        public_key: String::new(),
    };
    cert.checksum = compute_checksum(&cert);
    identity::sign(project_root, &mut cert).context("signing the certificate")?;
    Ok(cert)
}
