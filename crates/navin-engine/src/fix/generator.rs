//! Candidate generation is pluggable. The Rust engine owns the safe
//! apply-verify-gate loop; where the candidates come from (an LLM on the
//! desktop side, a template, a human) is behind this trait.

use anyhow::{bail, Context, Result};
use serde::Serialize;
use std::io::{Read, Write};
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant};

use crate::diagnose::Finding;

use super::model::FixCandidate;

/// Schema of the JSON the engine writes to a bridge's stdin.
pub const FIX_REQUEST_SCHEMA: &str = "navin-fix-request/v1";

#[derive(Debug, Serialize)]
struct FixRequest<'a> {
    schema: &'a str,
    engine_version: &'a str,
    project_root: String,
    finding: &'a Finding,
    /// How the app under test starts. A monorepo holds many programs; without
    /// this a generator cannot tell which one the numbers are about.
    #[serde(skip_serializing_if = "Option::is_none")]
    start_command: Option<&'a str>,
}

pub trait FixGenerator: Send + Sync {
    fn name(&self) -> &str;
    /// Propose zero or more candidates for a finding. Returning none is
    /// valid: not every finding has an automatic fix.
    fn propose(&self, finding: &Finding, project_root: &Path) -> Result<Vec<FixCandidate>>;
}

/// A generator that simply carries candidates supplied from outside (CLI,
/// daemon RPC, the desktop LLM bridge). This is the integration seam.
pub struct ProvidedPatchGenerator {
    candidates: Vec<FixCandidate>,
}

impl ProvidedPatchGenerator {
    pub fn new(candidates: Vec<FixCandidate>) -> Self {
        ProvidedPatchGenerator { candidates }
    }
}

impl FixGenerator for ProvidedPatchGenerator {
    fn name(&self) -> &str {
        "provided"
    }

    fn propose(&self, finding: &Finding, _project_root: &Path) -> Result<Vec<FixCandidate>> {
        // Only hand back candidates aimed at this finding.
        Ok(self
            .candidates
            .iter()
            .filter(|c| c.target_finding == finding.id)
            .cloned()
            .collect())
    }
}

/// Generator that shells out to an external bridge program. The engine
/// writes a [`FixRequest`] as JSON to the bridge's stdin and expects a JSON
/// array of [`FixCandidate`] on stdout. This is how the desktop/LLM side
/// plugs in without the Rust daemon ever embedding a model.
pub struct BridgeGenerator {
    command: String,
    timeout: Duration,
    /// Model preset forwarded to the bridge as `NAVIN_BRIDGE_PRESET`, so an
    /// operator can pick the LLM per campaign without editing evolve.toml.
    preset: Option<String>,
    /// The command that starts the app being measured, passed on so the
    /// bridge can show the model that app rather than the whole repository.
    start_command: Option<String>,
}

impl BridgeGenerator {
    pub fn new(command: impl Into<String>, timeout: Duration) -> Self {
        BridgeGenerator { command: command.into(), timeout, preset: None, start_command: None }
    }

    pub fn with_preset(mut self, preset: Option<String>) -> Self {
        self.preset = preset.filter(|p| !p.trim().is_empty());
        self
    }

    pub fn about_app(mut self, start_command: Option<String>) -> Self {
        self.start_command = start_command.filter(|c| !c.trim().is_empty());
        self
    }
}

impl FixGenerator for BridgeGenerator {
    fn name(&self) -> &str {
        "bridge"
    }

    fn propose(&self, finding: &Finding, project_root: &Path) -> Result<Vec<FixCandidate>> {
        let request = FixRequest {
            schema: FIX_REQUEST_SCHEMA,
            engine_version: crate::ENGINE_VERSION,
            project_root: project_root.display().to_string(),
            finding,
            start_command: self.start_command.as_deref(),
        };
        let payload = serde_json::to_vec(&request)?;
        let stdout = run_bridge(&self.command, &payload, self.timeout, self.preset.as_deref())
            .with_context(|| format!("fix bridge `{}` failed", self.command))?;

        let candidates: Vec<FixCandidate> = serde_json::from_slice(&stdout)
            .context("bridge output is not a JSON array of fix candidates")?;
        // Trust boundary: only keep candidates that actually target this
        // finding, so a misbehaving bridge cannot smuggle unrelated patches.
        Ok(candidates
            .into_iter()
            .filter(|c| c.target_finding == finding.id)
            .collect())
    }
}

/// Run the bridge with a wall-clock timeout, feeding `input` on stdin and
/// returning stdout bytes. Kills the child if it overruns.
fn run_bridge(
    command: &str,
    input: &[u8],
    timeout: Duration,
    preset: Option<&str>,
) -> Result<Vec<u8>> {
    let mut shell_cmd = shell(command);
    if let Some(preset) = preset {
        shell_cmd.env("NAVIN_BRIDGE_PRESET", preset);
    }
    let mut child = shell_cmd
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .context("cannot spawn bridge")?;

    // Feed the request, then close stdin so the bridge sees EOF.
    child
        .stdin
        .take()
        .context("bridge has no stdin")?
        .write_all(input)?;

    // Read stdout on a helper thread so a chatty bridge cannot deadlock us.
    let mut stdout = child.stdout.take().context("bridge has no stdout")?;
    let (tx, rx) = mpsc::channel();
    let reader = std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = stdout.read_to_end(&mut buf);
        let _ = tx.send(buf);
    });

    let deadline = Instant::now() + timeout;
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        if Instant::now() >= deadline {
            child.kill().ok();
            child.wait().ok();
            bail!("bridge exceeded its timeout of {}s", timeout.as_secs());
        }
        std::thread::sleep(Duration::from_millis(50));
    };

    let stdout = rx.recv_timeout(Duration::from_secs(2)).unwrap_or_default();
    reader.join().ok();

    if !status.success() {
        bail!("bridge exited with {}", status.code().unwrap_or(-1));
    }
    Ok(stdout)
}

fn shell(command: &str) -> Command {
    #[cfg(unix)]
    {
        let mut cmd = Command::new("sh");
        cmd.arg("-c").arg(command);
        cmd
    }
    #[cfg(not(unix))]
    {
        let mut cmd = Command::new("cmd");
        cmd.arg("/C").arg(command);
        cmd
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::diagnose::{Confidence, Finding, Severity};

    fn finding(id: &str) -> Finding {
        Finding {
            id: id.to_owned(),
            title: "t".to_owned(),
            severity: Severity::Critical,
            confidence: Confidence::High,
            related_fault: Some("load".to_owned()),
            symptom: "s".to_owned(),
            root_cause: "c".to_owned(),
            remediation: "r".to_owned(),
            family: "reliability".to_owned(),
            evidence: vec![],
        }
    }

    #[test]
    fn bridge_parses_candidates_and_filters_by_finding() {
        // A bridge that echoes two candidates: one on-target, one not.
        let script = r#"cat >/dev/null; cat <<'JSON'
[
  {"id":"good","target_finding":"crash.load","rationale":"fix","family":"reliability",
   "patch":{"kind":"files","edits":[{"path":"app.py","contents":"ok"}]}},
  {"id":"stray","target_finding":"other.finding","rationale":"nope","family":"reliability",
   "patch":{"kind":"files","edits":[{"path":"x","contents":"y"}]}}
]
JSON"#;
        let gen = BridgeGenerator::new(script, Duration::from_secs(10));
        let out = gen.propose(&finding("crash.load"), Path::new("/tmp")).unwrap();
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].id, "good");
    }

    #[test]
    fn the_bridge_is_told_which_app_is_under_test() {
        // The script refuses unless the request names the start command.
        let script = "grep -q 'cd site && npm run dev' && echo '[]' || exit 3";
        let gen = BridgeGenerator::new(script, Duration::from_secs(10))
            .about_app(Some("cd site && npm run dev".to_owned()));
        assert!(gen.propose(&finding("crash.load"), Path::new("/tmp")).is_ok());
    }

    #[test]
    fn bridge_timeout_is_enforced() {
        let gen = BridgeGenerator::new("sleep 5", Duration::from_millis(300));
        let err = gen.propose(&finding("crash.load"), Path::new("/tmp")).unwrap_err();
        assert!(format!("{err:#}").contains("timeout"));
    }

    #[test]
    fn bridge_failure_is_reported() {
        let gen = BridgeGenerator::new("exit 2", Duration::from_secs(5));
        assert!(gen.propose(&finding("crash.load"), Path::new("/tmp")).is_err());
    }
}
