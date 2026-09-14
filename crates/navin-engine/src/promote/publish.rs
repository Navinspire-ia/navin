//! Publishing a promotion: push its branch to the remote, then open a pull
//! request with the `gh` CLI when it is installed and authenticated. Without
//! `gh` the branch is still pushed and a compare link is handed back, so the
//! last step is one click instead of a dead end. No token is ever stored.

use anyhow::{bail, Context, Result};
use std::path::Path;
use std::process::Command;
use tracing::info;

use super::git;
use super::model::PromotionRecord;

/// Push the promotion branch and open a pull request for it. Idempotent: an
/// already published promotion is re-reported, not duplicated.
pub fn publish(project_root: &Path, id: &str) -> Result<PromotionRecord> {
    let mut record = PromotionRecord::load(project_root, id)?;
    if record.rolled_back_at.is_some() {
        bail!("promotion {id} was rolled back; nothing to publish");
    }
    let Some(branch) = record.branch.clone() else {
        bail!("promotion {id} has no branch (it was blocked before any commit)");
    };
    if !git::is_repo(project_root) {
        bail!("not a git repository");
    }
    if !git::branch_exists(project_root, &branch) {
        bail!("branch {branch} no longer exists in this repository");
    }
    let Some(remote) = git::default_remote(project_root) else {
        bail!("no git remote configured; add one with `git remote add origin <url>`");
    };

    git::push_branch(project_root, &remote, &branch)
        .with_context(|| format!("pushing {branch} to {remote}"))?;
    record.pushed_to = Some(format!("{remote}/{branch}"));
    let base = git::base_branch(project_root, &remote)?;

    let (url, note) = match open_with_gh(project_root, &record, &branch, &base) {
        Ok(url) => (Some(url), format!("pull request opened against {base}")),
        Err(err) => {
            let link = git::remote_url(project_root, &remote)
                .as_deref()
                .and_then(|remote_url| compare_url(remote_url, &base, &branch));
            let reason = format!("{err:#}");
            match link {
                Some(link) => (Some(link), format!("branch pushed; open the PR here ({reason})")),
                None => (None, format!("branch pushed; {reason}")),
            }
        }
    };
    record.pull_request = url;
    record.reasons.push(note);
    record.save(project_root)?;
    info!("promotion {id}: pushed {branch} to {remote}");
    Ok(record)
}

/// Ask `gh` to open the pull request and return its URL. Errors carry a
/// reason the caller can show, because falling back is a normal outcome.
fn open_with_gh(
    project_root: &Path,
    record: &PromotionRecord,
    branch: &str,
    base: &str,
) -> Result<String> {
    if !gh_available() {
        bail!("the GitHub CLI (gh) is not installed");
    }
    let output = gh(
        project_root,
        &[
            "pr",
            "create",
            "--head",
            branch,
            "--base",
            base,
            "--title",
            &title(record),
            "--body",
            &body(record),
        ],
    )?;
    if let Some(url) = first_url(&output.text) {
        return Ok(url);
    }
    if output.ok {
        bail!("gh reported success without a pull request URL");
    }
    // The usual case on a second call: gh refuses because a PR is open.
    let existing = gh(project_root, &["pr", "view", branch, "--json", "url", "-q", ".url"])?;
    match first_url(&existing.text) {
        Some(url) => Ok(url),
        None => bail!("gh could not open a pull request: {}", short(&output.text)),
    }
}

struct GhOutput {
    ok: bool,
    text: String,
}

/// `gh` reads the repository from the working directory, and says useful
/// things on both streams, so both are kept.
fn gh(project_root: &Path, args: &[&str]) -> Result<GhOutput> {
    let output = Command::new("gh")
        .current_dir(project_root)
        .args(args)
        .output()
        .context("running the GitHub CLI")?;
    let mut text = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
    if !stderr.is_empty() {
        if !text.is_empty() {
            text.push('\n');
        }
        text.push_str(&stderr);
    }
    Ok(GhOutput { ok: output.status.success(), text })
}

fn gh_available() -> bool {
    Command::new("gh")
        .arg("--version")
        .output()
        .map(|output| output.status.success())
        .unwrap_or(false)
}

fn title(record: &PromotionRecord) -> String {
    format!("navin: {} ({})", record.candidate_id, record.finding)
}

/// The body is the evidence, not a description: whoever reviews this needs
/// the measured before/after and how to re-verify it locally.
fn body(record: &PromotionRecord) -> String {
    let mut lines = vec![
        format!("Candidate `{}` for finding `{}`.", record.candidate_id, record.finding),
        String::new(),
    ];
    if let Some(cert) = &record.certificate {
        lines.push(format!(
            "- robustness {} -> {} out of 100, proof verdict {:?}",
            cert.score_before, cert.score_after, cert.verdict_after
        ));
        lines.push(format!(
            "- targeted finding resolved: {}",
            if cert.resolved_target { "yes" } else { "no" }
        ));
        lines.push(format!("- family `{}`, engine {}", cert.family, cert.engine_version));
        lines.push(format!("- certificate `{}` (Ed25519 signed)", cert.checksum));
    }
    if let Some(sha) = &record.commit_sha {
        lines.push(format!("- commit `{sha}`"));
    }
    lines.push(String::new());
    lines.push(format!(
        "Prepared by navin-engine in `{}` mode. Verify the evidence with \
         `navin-engine verify-cert . --id {}`.",
        record.mode, record.id
    ));
    lines.join("\n")
}

/// Turn a remote URL into the web link that opens a pull request form.
/// Handles the shapes git actually stores: scp-like SSH, ssh://, https with
/// or without credentials, with or without a `.git` suffix.
pub fn compare_url(remote_url: &str, base: &str, branch: &str) -> Option<String> {
    let web = web_root(remote_url)?;
    Some(format!("{web}/compare/{base}...{branch}?expand=1"))
}

fn web_root(remote_url: &str) -> Option<String> {
    let url = remote_url.trim();
    let authority = if let Some(rest) = url.strip_prefix("git@") {
        // The scp-like shape: git@host:owner/repo
        rest.replacen(':', "/", 1)
    } else {
        // Anything without a scheme is a local path, which has no web page.
        let rest = url
            .strip_prefix("ssh://")
            .or_else(|| url.strip_prefix("https://"))
            .or_else(|| url.strip_prefix("http://"))?;
        // Credentials in the URL must not leak into the link we hand out.
        rest.split_once('@').map(|(_, tail)| tail.to_owned()).unwrap_or_else(|| rest.to_owned())
    };
    let root = authority.trim_end_matches('/').trim_end_matches(".git");
    root.contains('/').then(|| format!("https://{root}"))
}

fn first_url(text: &str) -> Option<String> {
    text.split_whitespace()
        .find(|word| word.starts_with("https://"))
        .map(|word| word.trim_end_matches(['.', ',']).to_owned())
}

fn short(text: &str) -> String {
    text.lines().last().unwrap_or("").trim().to_owned()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::promote::model::{PromotionOutcome, PROMOTION_SCHEMA};

    fn record() -> PromotionRecord {
        PromotionRecord {
            schema: PROMOTION_SCHEMA.to_owned(),
            id: "promo-crash-load-1".to_owned(),
            finding: "crash.load".to_owned(),
            candidate_id: "bound-the-queue".to_owned(),
            mode: "safe".to_owned(),
            outcome: PromotionOutcome::BranchOnly,
            reasons: vec!["safe mode".to_owned()],
            branch: Some("navin/evolve/crash-load-1".to_owned()),
            commit_sha: Some("abc123".to_owned()),
            prev_head: Some("def456".to_owned()),
            merged: false,
            certificate: None,
            diff: None,
            pushed_to: None,
            pull_request: None,
            created_at: "epoch:1".to_owned(),
            rolled_back_at: None,
        }
    }

    #[test]
    fn ssh_and_https_remotes_reach_the_same_page() {
        let expect = "https://github.com/acme/app/compare/main...feature?expand=1";
        for remote in [
            "git@github.com:acme/app.git",
            "ssh://git@github.com/acme/app.git",
            "https://github.com/acme/app.git",
            "https://github.com/acme/app",
            "https://token@github.com/acme/app.git",
        ] {
            assert_eq!(compare_url(remote, "main", "feature").as_deref(), Some(expect), "{remote}");
        }
    }

    #[test]
    fn self_hosted_hosts_work_too() {
        assert_eq!(
            compare_url("git@gitlab.internal:team/api.git", "trunk", "navin/x").as_deref(),
            Some("https://gitlab.internal/team/api/compare/trunk...navin/x?expand=1")
        );
    }

    #[test]
    fn a_local_remote_has_no_web_page() {
        assert!(compare_url("/srv/git/app.git", "main", "x").is_none());
        assert!(compare_url("../mirror", "main", "x").is_none());
    }

    #[test]
    fn the_url_is_picked_out_of_gh_chatter() {
        let text = "Warning: 3 uncommitted changes\nhttps://github.com/acme/app/pull/42\n";
        assert_eq!(first_url(text).as_deref(), Some("https://github.com/acme/app/pull/42"));
        assert!(first_url("could not create pull request").is_none());
    }

    #[test]
    fn the_pull_request_body_carries_the_evidence() {
        let text = body(&record());
        assert!(text.contains("bound-the-queue"));
        assert!(text.contains("crash.load"));
        assert!(text.contains("verify-cert"));
        assert!(text.contains("commit `abc123`"));
        assert_eq!(title(&record()), "navin: bound-the-queue (crash.load)");
    }

    #[test]
    fn a_rolled_back_promotion_is_refused() {
        let tmp = tempfile::tempdir().unwrap();
        let mut rec = record();
        rec.rolled_back_at = Some("epoch:2".to_owned());
        rec.save(tmp.path()).unwrap();
        let err = publish(tmp.path(), &rec.id).unwrap_err().to_string();
        assert!(err.contains("rolled back"), "{err}");
    }

    #[test]
    fn a_blocked_promotion_has_no_branch_to_publish() {
        let tmp = tempfile::tempdir().unwrap();
        let mut rec = record();
        rec.branch = None;
        rec.outcome = PromotionOutcome::Blocked;
        rec.save(tmp.path()).unwrap();
        let err = publish(tmp.path(), &rec.id).unwrap_err().to_string();
        assert!(err.contains("no branch"), "{err}");
    }
}
