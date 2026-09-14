//! Guaranteed cleanup: a guard that destroys the shadow when dropped,
//! so an early return or a panic in a campaign cannot leak a worktree.

use tracing::warn;

use super::manager::Shadow;

pub struct CleanupGuard {
    shadow: Option<Shadow>,
}

impl CleanupGuard {
    pub fn new(shadow: Shadow) -> Self {
        CleanupGuard { shadow: Some(shadow) }
    }

    pub fn path(&self) -> &std::path::Path {
        &self.shadow.as_ref().expect("guard not disarmed").path
    }

    pub fn shadow(&self) -> &Shadow {
        self.shadow.as_ref().expect("guard not disarmed")
    }

    /// Destroy now and report the outcome (preferred over relying on Drop).
    pub fn destroy(mut self) -> anyhow::Result<()> {
        match self.shadow.take() {
            Some(shadow) => shadow.destroy(),
            None => Ok(()),
        }
    }
}

impl Drop for CleanupGuard {
    fn drop(&mut self) {
        if let Some(shadow) = self.shadow.take() {
            let run_id = shadow.run_id.clone();
            if let Err(err) = shadow.destroy() {
                warn!("shadow {run_id} cleanup on drop failed: {err:#}");
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::shadow::manager::ShadowManager;
    use crate::shadow::worktree::testutil::init_repo;

    #[test]
    fn dropping_the_guard_destroys_the_shadow() {
        let tmp = tempfile::tempdir().unwrap();
        init_repo(tmp.path());
        let manager = ShadowManager::new(tmp.path());
        {
            let _guard = CleanupGuard::new(manager.create("guarded").unwrap());
            assert_eq!(manager.list().len(), 1);
        }
        assert!(manager.list().is_empty());
    }
}
