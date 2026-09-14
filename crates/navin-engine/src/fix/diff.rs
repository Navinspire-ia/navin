//! Turn an applied patch into something a human can read. Candidates arrive
//! as whole-file writes, which are unreviewable; git, inside the shadow that
//! is about to be thrown away, computes the unified diff for free.

use std::path::Path;
use std::process::Command;

/// A diff is for reading. Past this size nobody reviews it anyway, and the
/// report would become a second copy of the repository.
const MAX_BYTES: usize = 60 * 1024;

/// The unified diff of everything the patch changed in `workdir`, created
/// files included. None when the directory is not a git worktree (copy-mode
/// shadow) or when the patch changed nothing.
pub fn capture(workdir: &Path) -> Option<String> {
    // Staging is what makes new files visible to `git diff`, and the index of
    // a throwaway worktree is ours to use.
    git(workdir, &["add", "-A"])?;
    let diff = git(workdir, &["diff", "--cached", "--no-color", "--find-renames"])?;
    let diff = diff.trim_end();
    if diff.is_empty() {
        return None;
    }
    Some(clamp(diff))
}

fn clamp(diff: &str) -> String {
    if diff.len() <= MAX_BYTES {
        return diff.to_owned();
    }
    let mut cut = MAX_BYTES;
    while cut > 0 && !diff.is_char_boundary(cut) {
        cut -= 1;
    }
    let head = &diff[..cut];
    // Cut on a line boundary: half a hunk header reads worse than nothing.
    let head = head.rsplit_once('\n').map(|(keep, _)| keep).unwrap_or(head);
    format!("{head}\n... diff truncated at {MAX_BYTES} bytes ...")
}

fn git(dir: &Path, args: &[&str]) -> Option<String> {
    let output = Command::new("git").arg("-C").arg(dir).args(args).output().ok()?;
    if !output.status.success() {
        return None;
    }
    String::from_utf8(output.stdout).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn repo() -> tempfile::TempDir {
        let tmp = tempfile::tempdir().unwrap();
        for args in [
            vec!["init", "-q"],
            vec!["config", "user.email", "engine@navin.test"],
            vec!["config", "user.name", "navin"],
        ] {
            Command::new("git").arg("-C").arg(tmp.path()).args(&args).output().unwrap();
        }
        std::fs::write(tmp.path().join("app.py"), "print(1)\n").unwrap();
        Command::new("git").arg("-C").arg(tmp.path()).args(["add", "-A"]).output().unwrap();
        Command::new("git")
            .arg("-C")
            .arg(tmp.path())
            .args(["commit", "-qm", "base"])
            .output()
            .unwrap();
        tmp
    }

    #[test]
    fn an_edit_and_a_new_file_both_show_up() {
        let tmp = repo();
        std::fs::write(tmp.path().join("app.py"), "print(2)\n").unwrap();
        std::fs::write(tmp.path().join("cache.py"), "CACHE = {}\n").unwrap();

        let diff = capture(tmp.path()).expect("a diff");
        assert!(diff.contains("app.py"), "{diff}");
        assert!(diff.contains("-print(1)") && diff.contains("+print(2)"), "{diff}");
        assert!(diff.contains("cache.py") && diff.contains("+CACHE = {}"), "{diff}");
    }

    #[test]
    fn an_untouched_worktree_has_nothing_to_show() {
        let tmp = repo();
        assert!(capture(tmp.path()).is_none());
    }

    #[test]
    fn a_non_git_directory_is_not_an_error() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::write(tmp.path().join("main.py"), "print(1)").unwrap();
        assert!(capture(tmp.path()).is_none());
    }

    #[test]
    fn a_huge_diff_is_clamped_on_a_line_boundary() {
        let line = format!("+{}\n", "x".repeat(80));
        let big = line.repeat(MAX_BYTES / line.len() + 100);
        let clamped = clamp(&big);

        assert!(clamped.len() < big.len());
        assert!(clamped.ends_with("bytes ..."), "{}", &clamped[clamped.len() - 40..]);
        // Every kept line is whole, so the tail is still readable as a diff.
        let body = clamped.rsplit_once('\n').unwrap().0;
        assert!(body.lines().all(|l| l.len() == line.len() - 1));
    }

    #[test]
    fn a_small_diff_is_returned_untouched() {
        let diff = "--- a/app.py\n+++ b/app.py\n+print(2)";
        assert_eq!(clamp(diff), diff);
    }
}
