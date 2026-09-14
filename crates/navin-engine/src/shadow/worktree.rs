//! Git worktree operations via the git CLI (always present where a repo is).

use anyhow::{bail, Context, Result};
use std::path::Path;
use std::process::Command;

fn git(root: &Path, args: &[&str]) -> Result<String> {
    let stdout = git_stdout(root, args)?;
    Ok(String::from_utf8_lossy(&stdout).trim().to_owned())
}

/// Raw stdout variant, for output that is not text (binary diffs, NUL lists).
fn git_stdout(root: &Path, args: &[&str]) -> Result<Vec<u8>> {
    let output = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .output()
        .context("git not found on PATH")?;
    if !output.status.success() {
        bail!(
            "git {} failed: {}",
            args.join(" "),
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    Ok(output.stdout)
}

pub fn is_git_repo(root: &Path) -> bool {
    root.join(".git").exists()
}

pub fn head_sha(root: &Path) -> Result<String> {
    git(root, &["rev-parse", "HEAD"])
}

/// Create a detached worktree of `sha` at `dest`.
pub fn add_worktree(root: &Path, dest: &Path, sha: &str) -> Result<()> {
    let dest_str = dest.to_string_lossy();
    git(root, &["worktree", "add", "--detach", &dest_str, sha])?;
    Ok(())
}

/// Carry the uncommitted state of `root` onto the worktree at `dest`:
/// staged and unstaged tracked changes travel as one binary patch, and
/// untracked (non-ignored) files are copied as-is. Returns how many files
/// differ from HEAD, so callers can log what the proof actually covers.
pub fn apply_uncommitted(root: &Path, dest: &Path) -> Result<usize> {
    let mut carried = 0usize;

    let names = git(root, &["diff", "HEAD", "--name-only"])?;
    if !names.is_empty() {
        carried += names.lines().count();
        let patch = git_stdout(root, &["diff", "HEAD", "--binary"])?;
        git_apply(dest, &patch)?;
    }

    let untracked = git_stdout(root, &["ls-files", "--others", "--exclude-standard", "-z"])?;
    for raw in untracked.split(|byte| *byte == 0) {
        if raw.is_empty() {
            continue;
        }
        let rel = String::from_utf8_lossy(raw).into_owned();
        let target = dest.join(&rel);
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("cannot create {}", parent.display()))?;
        }
        std::fs::copy(root.join(&rel), &target)
            .with_context(|| format!("cannot copy untracked file {rel}"))?;
        carried += 1;
    }
    Ok(carried)
}

/// `git apply` with the patch on stdin: a binary diff cannot go through argv.
fn git_apply(dest: &Path, patch: &[u8]) -> Result<()> {
    use std::io::Write;

    let mut child = Command::new("git")
        .arg("-C")
        .arg(dest)
        .args(["apply", "--binary", "--whitespace=nowarn"])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .context("git not found on PATH")?;
    child
        .stdin
        .as_mut()
        .expect("stdin was requested piped")
        .write_all(patch)
        .context("cannot stream the patch to git apply")?;
    let output = child.wait_with_output().context("git apply did not finish")?;
    if !output.status.success() {
        bail!(
            "git apply failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    Ok(())
}

/// Remove a worktree even if it has local changes, then prune metadata.
pub fn remove_worktree(root: &Path, dest: &Path) -> Result<()> {
    let dest_str = dest.to_string_lossy();
    // --force twice also removes worktrees with dirty or locked state.
    let result = git(root, &["worktree", "remove", "--force", "--force", &dest_str]);
    // Whatever happened, make sure the directory and the metadata are gone:
    // a crashed run must never leave a zombie worktree behind.
    if dest.exists() {
        std::fs::remove_dir_all(dest).ok();
    }
    git(root, &["worktree", "prune"]).ok();
    result.map(|_| ())
}

#[cfg(test)]
pub mod testutil {
    use super::*;

    /// Init a repo with one commit; returns nothing, panics on failure.
    pub fn init_repo(root: &Path) {
        for args in [
            vec!["init", "-q"],
            vec!["config", "user.email", "test@navin.local"],
            vec!["config", "user.name", "navin-test"],
        ] {
            git(root, &args).unwrap();
        }
        std::fs::write(root.join("app.txt"), "v1").unwrap();
        git(root, &["add", "."]).unwrap();
        git(root, &["commit", "-q", "-m", "init"]).unwrap();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn worktree_lifecycle_pins_the_sha() {
        let tmp = tempfile::tempdir().unwrap();
        testutil::init_repo(tmp.path());
        let sha = head_sha(tmp.path()).unwrap();

        let dest = tmp.path().join(".navin").join("shadow").join("wt-1");
        std::fs::create_dir_all(dest.parent().unwrap()).unwrap();
        add_worktree(tmp.path(), &dest, &sha).unwrap();
        assert!(dest.join("app.txt").is_file());

        // Mutating the shadow must not touch the real project.
        std::fs::write(dest.join("app.txt"), "mutated").unwrap();
        assert_eq!(std::fs::read_to_string(tmp.path().join("app.txt")).unwrap(), "v1");

        remove_worktree(tmp.path(), &dest).unwrap();
        assert!(!dest.exists());
    }

    /// The whole point of a dirty shadow: a pending, uncommitted fix must be
    /// what the proof exercises, not the last commit.
    #[test]
    fn uncommitted_edits_and_new_files_reach_the_worktree() {
        let tmp = tempfile::tempdir().unwrap();
        testutil::init_repo(tmp.path());
        std::fs::write(tmp.path().join("app.txt"), "pending fix").unwrap();
        std::fs::create_dir_all(tmp.path().join("src")).unwrap();
        std::fs::write(tmp.path().join("src/new.txt"), "brand new").unwrap();
        std::fs::write(tmp.path().join(".gitignore"), "ignored.txt\n").unwrap();
        std::fs::write(tmp.path().join("ignored.txt"), "never copied").unwrap();

        let sha = head_sha(tmp.path()).unwrap();
        let dest = tmp.path().join(".navin").join("shadow").join("wt-dirty");
        std::fs::create_dir_all(dest.parent().unwrap()).unwrap();
        add_worktree(tmp.path(), &dest, &sha).unwrap();

        let carried = apply_uncommitted(tmp.path(), &dest).unwrap();
        assert!(carried >= 3, "edit + new file + .gitignore, got {carried}");
        assert_eq!(std::fs::read_to_string(dest.join("app.txt")).unwrap(), "pending fix");
        assert_eq!(std::fs::read_to_string(dest.join("src/new.txt")).unwrap(), "brand new");
        assert!(!dest.join("ignored.txt").exists());

        remove_worktree(tmp.path(), &dest).unwrap();
    }
}
