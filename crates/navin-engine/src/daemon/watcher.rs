//! Commit watcher: when the operator opts in (autorun.json), every new
//! commit on the project's HEAD re-runs the configured operation, so the
//! engine keeps proving the code while the developer works.
//!
//! The check reads two small files under `.git/` every few seconds; no
//! subprocess is spawned and an unchanged HEAD costs two stat+reads.

use serde_json::Value;
use std::path::{Path, PathBuf};
use std::time::Duration;
use tokio::sync::watch;
use tracing::{info, warn};

use super::scheduler::{JobState, Scheduler};

const POLL_INTERVAL: Duration = Duration::from_secs(5);

struct Autorun {
    enabled: bool,
    kind: String,
    params: Value,
}

pub async fn run_watcher(
    root: PathBuf,
    scheduler: Scheduler,
    mut shutdown: watch::Receiver<bool>,
) {
    let mut last_head = read_head(&root);
    loop {
        tokio::select! {
            _ = tokio::time::sleep(POLL_INTERVAL) => {}
            _ = shutdown.changed() => {
                if *shutdown.borrow() {
                    return;
                }
            }
        }
        let head = match read_head(&root) {
            Some(head) => head,
            None => continue,
        };
        if last_head.as_deref() == Some(head.as_str()) {
            continue;
        }
        // The first observed HEAD is only a baseline, not a new commit.
        let had_baseline = last_head.is_some();
        last_head = Some(head.clone());
        if !had_baseline {
            continue;
        }

        let autorun = match read_autorun(&root) {
            Some(autorun) if autorun.enabled => autorun,
            _ => continue,
        };
        let busy = scheduler
            .snapshot()
            .iter()
            .any(|job| matches!(job.state, JobState::Queued | JobState::Running));
        if busy {
            info!("commit {head} detected but a job is already active, skipping auto-run");
            continue;
        }
        match scheduler.enqueue(&autorun.kind, autorun.params) {
            Ok(id) => info!("commit {head} detected, auto-enqueued {} as job {id}", autorun.kind),
            Err(err) => warn!("auto-run enqueue failed: {err}"),
        }
    }
}

fn read_autorun(root: &Path) -> Option<Autorun> {
    let path = crate::engine_dir(root).join("autorun.json");
    let text = std::fs::read_to_string(path).ok()?;
    let value: Value = serde_json::from_str(&text).ok()?;
    Some(Autorun {
        enabled: value.get("enabled").and_then(Value::as_bool).unwrap_or(false),
        kind: value.get("kind")?.as_str()?.to_owned(),
        params: value.get("params").cloned().unwrap_or(Value::Null),
    })
}

/// Resolve the commit the main worktree's HEAD points at, without spawning
/// git. Shadow worktrees commit on their own branches and never move this.
fn read_head(root: &Path) -> Option<String> {
    let git = root.join(".git");
    let git_dir = if git.is_file() {
        // `.git` file used by linked worktrees: "gitdir: <path>".
        let text = std::fs::read_to_string(&git).ok()?;
        let pointer = text.strip_prefix("gitdir:")?.trim();
        let path = PathBuf::from(pointer);
        if path.is_absolute() {
            path
        } else {
            root.join(path)
        }
    } else if git.is_dir() {
        git
    } else {
        return None;
    };

    let head = std::fs::read_to_string(git_dir.join("HEAD")).ok()?;
    let head = head.trim();
    let reference = match head.strip_prefix("ref: ") {
        Some(reference) => reference.trim(),
        None => return Some(head.to_owned()), // detached HEAD holds the sha
    };
    if let Ok(sha) = std::fs::read_to_string(git_dir.join(reference)) {
        let sha = sha.trim();
        if !sha.is_empty() {
            return Some(sha.to_owned());
        }
    }
    // Ref may live in packed-refs after `git gc`.
    let packed = std::fs::read_to_string(git_dir.join("packed-refs")).ok()?;
    for line in packed.lines() {
        if let Some(sha) = line.strip_suffix(reference) {
            let sha = sha.trim();
            if !sha.is_empty() && !sha.starts_with('#') {
                return Some(sha.to_owned());
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn read_head_resolves_a_loose_ref() {
        let dir = tempfile::tempdir().unwrap();
        let git = dir.path().join(".git");
        std::fs::create_dir_all(git.join("refs/heads")).unwrap();
        std::fs::write(git.join("HEAD"), "ref: refs/heads/master\n").unwrap();
        std::fs::write(git.join("refs/heads/master"), "abc123\n").unwrap();
        assert_eq!(read_head(dir.path()).as_deref(), Some("abc123"));
    }

    #[test]
    fn read_head_returns_none_without_git() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(read_head(dir.path()), None);
    }
}
