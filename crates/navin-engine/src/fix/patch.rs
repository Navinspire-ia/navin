//! Apply a candidate patch to a shadow directory. Every write is confined
//! to the shadow: absolute paths and `..` traversal are rejected, so an
//! untrusted candidate can never escape into the real workspace.

use anyhow::{bail, Context, Result};
use std::io::Write;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};

use super::model::{FileEdit, FixPatch};

pub fn apply(patch: &FixPatch, shadow_dir: &Path) -> Result<()> {
    match patch {
        FixPatch::Files { edits } => {
            for edit in edits {
                apply_file_edit(edit, shadow_dir)?;
            }
            Ok(())
        }
        FixPatch::UnifiedDiff { diff } => apply_unified_diff(diff, shadow_dir),
    }
}

/// Resolve `rel` under `base`, rejecting anything that would escape it.
fn safe_join(base: &Path, rel: &str) -> Result<PathBuf> {
    let rel_path = Path::new(rel);
    if rel_path.is_absolute() {
        bail!("patch path must be relative: {rel}");
    }
    let mut out = base.to_path_buf();
    for component in rel_path.components() {
        match component {
            Component::Normal(part) => out.push(part),
            // Anything else (`..`, root, prefix) is an escape attempt.
            _ => bail!("unsafe path component in {rel}"),
        }
    }
    Ok(out)
}

fn apply_file_edit(edit: &FileEdit, shadow_dir: &Path) -> Result<()> {
    let target = safe_join(shadow_dir, &edit.path)?;
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("cannot create {}", parent.display()))?;
    }
    std::fs::write(&target, &edit.contents)
        .with_context(|| format!("cannot write {}", target.display()))?;
    Ok(())
}

fn apply_unified_diff(diff: &str, shadow_dir: &Path) -> Result<()> {
    let mut child = Command::new("git")
        .arg("-C")
        .arg(shadow_dir)
        .args(["apply", "--whitespace=nowarn", "-"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .context("git not found on PATH")?;
    child
        .stdin
        .take()
        .context("no stdin for git apply")?
        .write_all(diff.as_bytes())?;
    let output = child.wait_with_output()?;
    if !output.status.success() {
        bail!(
            "git apply failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn file_edits_are_written_inside_the_shadow() {
        let tmp = tempfile::tempdir().unwrap();
        let patch = FixPatch::Files {
            edits: vec![FileEdit {
                path: "src/handler.rs".to_owned(),
                contents: "fn ok() {}".to_owned(),
            }],
        };
        apply(&patch, tmp.path()).unwrap();
        assert_eq!(
            std::fs::read_to_string(tmp.path().join("src/handler.rs")).unwrap(),
            "fn ok() {}"
        );
    }

    #[test]
    fn traversal_is_rejected() {
        let tmp = tempfile::tempdir().unwrap();
        let patch = FixPatch::Files {
            edits: vec![FileEdit {
                path: "../escape.txt".to_owned(),
                contents: "nope".to_owned(),
            }],
        };
        let err = apply(&patch, tmp.path()).unwrap_err();
        assert!(format!("{err:#}").contains("unsafe path"));
        assert!(!tmp.path().parent().unwrap().join("escape.txt").exists());
    }

    #[test]
    fn absolute_paths_are_rejected() {
        let tmp = tempfile::tempdir().unwrap();
        let patch = FixPatch::Files {
            edits: vec![FileEdit {
                path: "/etc/passwd".to_owned(),
                contents: "nope".to_owned(),
            }],
        };
        assert!(apply(&patch, tmp.path()).is_err());
    }
}
