//! Create and destroy shadow workspaces under `.navin/shadow/<run-id>`.

use anyhow::{Context, Result};
use serde::Serialize;
use std::path::{Path, PathBuf};
use tracing::{info, warn};

use super::deps;
use super::filesystem::copy_project;
use super::worktree;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ShadowMode {
    Worktree,
    Copy,
}

/// A live shadow workspace. Destroy it explicitly with [`Shadow::destroy`];
/// the daemon also sweeps leftovers at startup (crash recovery).
#[derive(Debug, Clone, Serialize)]
pub struct Shadow {
    pub run_id: String,
    pub path: PathBuf,
    /// Pinned commit for worktrees; None for copies.
    pub sha: Option<String>,
    pub mode: ShadowMode,
    #[serde(skip)]
    project_root: PathBuf,
}

pub struct ShadowManager {
    project_root: PathBuf,
}

impl ShadowManager {
    pub fn new(project_root: &Path) -> Self {
        ShadowManager { project_root: project_root.to_path_buf() }
    }

    fn shadow_dir(&self) -> PathBuf {
        self.project_root.join(crate::NAVIN_DIR).join("shadow")
    }

    pub fn create(&self, run_id: &str) -> Result<Shadow> {
        self.create_inner(run_id, false)
    }

    /// Like [`ShadowManager::create`], but the shadow also carries the
    /// project's uncommitted state (staged, unstaged and untracked files).
    /// This is how a pending fix gets proved under load before it is
    /// accepted or committed. Copy-mode shadows already are the live tree.
    pub fn create_with_uncommitted(&self, run_id: &str) -> Result<Shadow> {
        self.create_inner(run_id, true)
    }

    fn create_inner(&self, run_id: &str, include_uncommitted: bool) -> Result<Shadow> {
        anyhow::ensure!(
            !run_id.is_empty() && run_id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-'),
            "run id must be alphanumeric/dashes"
        );
        let dest = self.shadow_dir().join(run_id);
        // Stages name their shadows after the step, not the run, so a campaign
        // that was cancelled or crashed leaves one behind and would block every
        // later campaign. Anything under `.navin/shadow/` belongs to the engine,
        // never to the user, so reclaim it instead.
        if dest.exists() {
            warn!("shadow {run_id} left over from an earlier run, reclaiming it");
            destroy_path(&self.project_root, &dest)
                .with_context(|| format!("cannot reclaim stale shadow {run_id}"))?;
        }
        std::fs::create_dir_all(self.shadow_dir())
            .with_context(|| format!("cannot create {}", self.shadow_dir().display()))?;

        if worktree::is_git_repo(&self.project_root) {
            let sha = worktree::head_sha(&self.project_root)?;
            worktree::add_worktree(&self.project_root, &dest, &sha)?;
            if include_uncommitted {
                let carried = worktree::apply_uncommitted(&self.project_root, &dest)
                    .context("cannot carry uncommitted changes into the shadow")?;
                info!("shadow {run_id} carries {carried} uncommitted file(s)");
            }
            let lent = deps::lend_installed(&self.project_root, &dest);
            info!(
                "shadow {run_id} created (worktree @ {}, {lent} dependency folders lent)",
                &sha[..12.min(sha.len())]
            );
            Ok(Shadow {
                run_id: run_id.to_owned(),
                path: dest,
                sha: Some(sha),
                mode: ShadowMode::Worktree,
                project_root: self.project_root.clone(),
            })
        } else {
            copy_project(&self.project_root, &dest)?;
            let lent = deps::lend_installed(&self.project_root, &dest);
            info!("shadow {run_id} created (copy, {lent} dependency folders lent)");
            Ok(Shadow {
                run_id: run_id.to_owned(),
                path: dest,
                sha: None,
                mode: ShadowMode::Copy,
                project_root: self.project_root.clone(),
            })
        }
    }

    pub fn list(&self) -> Vec<String> {
        let Ok(entries) = std::fs::read_dir(self.shadow_dir()) else {
            return Vec::new();
        };
        let mut ids: Vec<String> = entries
            .flatten()
            .filter(|e| e.path().is_dir())
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .collect();
        ids.sort();
        ids
    }

    pub fn destroy(&self, run_id: &str) -> Result<()> {
        let dest = self.shadow_dir().join(run_id);
        destroy_path(&self.project_root, &dest)
    }

    /// Crash recovery: remove every leftover shadow. Called at daemon start,
    /// so a killed campaign can never leak disk space or git metadata.
    pub fn cleanup_stale(&self) -> usize {
        let mut removed = 0;
        for run_id in self.list() {
            match self.destroy(&run_id) {
                Ok(()) => {
                    warn!("removed stale shadow {run_id}");
                    removed += 1;
                }
                Err(err) => warn!("could not remove stale shadow {run_id}: {err:#}"),
            }
        }
        removed
    }
}

impl Shadow {
    pub fn destroy(self) -> Result<()> {
        destroy_path(&self.project_root, &self.path)
    }
}

fn destroy_path(project_root: &Path, dest: &Path) -> Result<()> {
    if !dest.exists() {
        return Ok(());
    }
    if worktree::is_git_repo(project_root) {
        // remove_worktree already force-deletes the directory on failure.
        worktree::remove_worktree(project_root, dest).ok();
    }
    if dest.exists() {
        std::fs::remove_dir_all(dest)
            .with_context(|| format!("cannot remove {}", dest.display()))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::shadow::worktree::testutil::init_repo;

    #[test]
    fn git_shadow_created_and_destroyed() {
        let tmp = tempfile::tempdir().unwrap();
        init_repo(tmp.path());
        let manager = ShadowManager::new(tmp.path());

        let shadow = manager.create("run-1").unwrap();
        assert_eq!(shadow.mode, ShadowMode::Worktree);
        assert!(shadow.path.join("app.txt").is_file());
        assert_eq!(manager.list(), vec!["run-1".to_owned()]);

        shadow.destroy().unwrap();
        assert!(manager.list().is_empty());
    }

    #[test]
    fn a_dirty_shadow_carries_the_pending_change() {
        let tmp = tempfile::tempdir().unwrap();
        init_repo(tmp.path());
        std::fs::write(tmp.path().join("app.txt"), "uncommitted fix").unwrap();
        let manager = ShadowManager::new(tmp.path());

        // A plain shadow pins HEAD, so it must NOT see the pending edit.
        let clean = manager.create("clean-run").unwrap();
        assert_eq!(std::fs::read_to_string(clean.path.join("app.txt")).unwrap(), "v1");
        clean.destroy().unwrap();

        // The dirty shadow is what "prove this change" runs against.
        let dirty = manager.create_with_uncommitted("dirty-run").unwrap();
        assert_eq!(
            std::fs::read_to_string(dirty.path.join("app.txt")).unwrap(),
            "uncommitted fix"
        );
        dirty.destroy().unwrap();
    }

    #[test]
    fn non_git_project_falls_back_to_copy() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::write(tmp.path().join("main.py"), "print(1)").unwrap();
        let manager = ShadowManager::new(tmp.path());

        let shadow = manager.create("run-2").unwrap();
        assert_eq!(shadow.mode, ShadowMode::Copy);
        assert!(shadow.path.join("main.py").is_file());
        shadow.destroy().unwrap();
    }

    #[test]
    fn a_leftover_shadow_is_reclaimed_not_refused() {
        let tmp = tempfile::tempdir().unwrap();
        init_repo(tmp.path());
        let manager = ShadowManager::new(tmp.path());

        // A cancelled campaign leaves its shadow behind, with a file in it.
        let first = manager.create("opt-base").unwrap();
        std::fs::write(first.path.join("leftover.txt"), "from the killed run").unwrap();

        // The next campaign must run anyway, and must start from clean code.
        let second = manager.create("opt-base").unwrap();
        assert!(second.path.join("app.txt").is_file());
        assert!(!second.path.join("leftover.txt").exists());
        assert_eq!(manager.list(), vec!["opt-base".to_owned()]);
        second.destroy().unwrap();
    }

    #[test]
    fn stale_shadows_are_swept() {
        let tmp = tempfile::tempdir().unwrap();
        init_repo(tmp.path());
        let manager = ShadowManager::new(tmp.path());
        manager.create("stale-1").unwrap();
        manager.create("stale-2").unwrap();
        // Simulate a crashed daemon: nothing destroyed the shadows.
        assert_eq!(manager.cleanup_stale(), 2);
        assert!(manager.list().is_empty());
    }
}
