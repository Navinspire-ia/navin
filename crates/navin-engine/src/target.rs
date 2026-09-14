//! Work out what to run and where it answers.
//!
//! Every engine needs two facts: the command that starts the application
//! and the URL it serves. Both are questions about the project, not about
//! the operator, so the engine answers them itself and only asks when its
//! own investigation comes up empty.

use anyhow::Result;
use std::path::Path;
use std::time::Duration;

use crate::progress::ProgressSink;
use crate::project::{inspect_project, start_candidates, suggested_ports};
use crate::runner::discover::{discover_url, UrlCache};

/// Time given to an application to boot during discovery. Generous: a cold
/// bundler or a JVM is slow the first time.
const DISCOVERY_TIMEOUT: Duration = Duration::from_secs(90);

/// How to reach the application under test.
#[derive(Debug, Clone)]
pub struct Target {
    pub start_cmd: String,
    pub url: String,
    /// True when the engine had to boot the app to find its port.
    pub discovered: bool,
}

/// Fill in whatever the caller did not provide.
///
/// The start command comes from the project manifest. The URL, which no
/// static analysis can know for sure, is observed: the app is booted once
/// in a throwaway shadow, watched until it opens a port, then stopped.
pub async fn resolve(
    root: &Path,
    start: Option<String>,
    url: Option<String>,
    sink: &dyn ProgressSink,
) -> Result<Target> {
    let manifest = inspect_project(root)?;
    // An operator who names a command means it; without one, every way the
    // project could start is fair game.
    let candidates: Vec<String> = match start {
        Some(given) => vec![given],
        None => start_candidates(root, &manifest),
    };
    anyhow::ensure!(
        !candidates.is_empty(),
        "cannot tell how to start this project: nothing found in {}, give a start command",
        root.display()
    );

    // A worker owns no port: there is no URL to discover, health and load
    // go through the commands declared in `[target]`.
    let config = crate::policy::config::EvolveConfig::load(root).unwrap_or_default();
    if config.target.is_worker() {
        let url = crate::proof::WORKER_URL.to_owned();
        sink.emit("target", "resolved", serde_json::json!({ "url": url, "source": "worker" }));
        return Ok(Target { start_cmd: candidates[0].clone(), url, discovered: false });
    }

    if let Some(url) = url {
        return Ok(Target { start_cmd: candidates[0].clone(), url, discovered: false });
    }

    let cache = UrlCache::new(root);
    for candidate in &candidates {
        if let Some(url) = cache.get(candidate) {
            sink.emit("target", "resolved", serde_json::json!({ "url": url, "source": "cache" }));
            return Ok(Target { start_cmd: candidate.clone(), url, discovered: false });
        }
    }

    let manager = crate::shadow::ShadowManager::new(root);
    let guard = crate::shadow::cleanup::CleanupGuard::new(manager.create("discover")?);
    let log = crate::engine_dir(root).join("logs").join("discover.log");
    let hints = suggested_ports(&manifest);
    let mut refused: Vec<String> = Vec::new();

    for candidate in &candidates {
        sink.emit("target", "discovering", serde_json::json!({ "start": candidate }));
        let found =
            discover_url(candidate, guard.path(), &log, &hints, DISCOVERY_TIMEOUT, None).await;
        match found {
            Ok(url) => {
                guard.destroy()?;
                cache.put(candidate, &url).ok();
                sink.emit(
                    "target",
                    "resolved",
                    serde_json::json!({ "url": url, "source": "probe", "start": candidate }),
                );
                return Ok(Target { start_cmd: candidate.clone(), url, discovered: true });
            }
            Err(err) => {
                sink.emit(
                    "target",
                    "refused",
                    serde_json::json!({ "start": candidate, "reason": first_line(&err) }),
                );
                refused.push(format!("  {candidate}\n    {}", first_line(&err)));
            }
        }
    }
    guard.destroy()?;
    anyhow::bail!(
        "none of the ways this project can start served traffic:\n{}\n\
         last log tail:\n{}",
        refused.join("\n"),
        crate::runner::logs::tail(&log, 30)
    )
}

/// Errors carry a log tail for evidence; a summary only wants the verdict.
fn first_line(err: &anyhow::Error) -> String {
    let text = format!("{err:#}");
    text.lines().next().unwrap_or_default().to_owned()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::progress::RecordingSink;

    #[tokio::test]
    async fn a_given_target_is_left_alone() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::write(tmp.path().join("package.json"), "{}").unwrap();
        let target = resolve(
            tmp.path(),
            Some("node server.js".to_owned()),
            Some("http://127.0.0.1:9999/".to_owned()),
            &RecordingSink::default(),
        )
        .await
        .unwrap();
        assert_eq!(target.start_cmd, "node server.js");
        assert!(!target.discovered);
    }

    #[tokio::test]
    async fn a_project_with_no_entry_point_says_so() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::write(tmp.path().join("README.md"), "nothing runnable here").unwrap();
        let err = resolve(tmp.path(), None, None, &RecordingSink::default()).await.unwrap_err();
        assert!(format!("{err:#}").contains("cannot tell how to start"));
    }

    #[tokio::test]
    async fn a_command_that_cannot_run_hands_over_to_the_next_one() {
        let tmp = tempfile::tempdir().unwrap();
        let port = crate::runner::ports::free_port().unwrap();
        // The most explicit source names a binary nobody installed - exactly
        // what an uninstalled sub-project looks like from the outside.
        std::fs::write(tmp.path().join("Procfile"), "web: navin-not-installed --serve\n").unwrap();
        std::fs::write(
            tmp.path().join("package.json"),
            format!(r#"{{"scripts":{{"dev":"python3 -m http.server {port}"}}}}"#),
        )
        .unwrap();

        let sink = RecordingSink::default();
        let target = resolve(tmp.path(), None, None, &sink).await.unwrap();
        assert_eq!(target.start_cmd, "npm run dev");
        assert_eq!(target.url, format!("http://127.0.0.1:{port}/"));
    }

    #[tokio::test]
    async fn every_dead_end_is_reported_together() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::write(tmp.path().join("Procfile"), "web: navin-not-installed\n").unwrap();
        let err = resolve(tmp.path(), None, None, &RecordingSink::default()).await.unwrap_err();
        let message = format!("{err:#}");
        assert!(message.contains("none of the ways this project can start"), "{message}");
        assert!(message.contains("navin-not-installed"), "{message}");
    }

    #[tokio::test]
    async fn the_url_is_learnt_by_booting_the_app() {
        let tmp = tempfile::tempdir().unwrap();
        let port = crate::runner::ports::free_port().unwrap();
        std::fs::write(tmp.path().join("Procfile"), format!("web: python3 -m http.server {port}\n"))
            .unwrap();
        let target = resolve(tmp.path(), None, None, &RecordingSink::default()).await.unwrap();
        assert_eq!(target.url, format!("http://127.0.0.1:{port}/"));
        assert!(target.discovered);
        // The answer is remembered: the second call must not boot anything.
        let again = resolve(tmp.path(), None, None, &RecordingSink::default()).await.unwrap();
        assert_eq!(again.url, target.url);
        assert!(!again.discovered);
    }
}
