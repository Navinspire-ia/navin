//! Filesystem copy fallback for non-git projects.
//!
//! Copies source files while skipping dependency and build folders: the
//! shadow rebuilds its own artefacts, and copying node_modules would make
//! shadow creation slower than the experiment itself.

use anyhow::{Context, Result};
use std::fs;
use std::path::Path;

const SKIP_DIRS: &[&str] = &[
    "node_modules",
    "target",
    "dist",
    "build",
    ".git",
    ".navin",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    ".turbo",
    ".cache",
];

pub fn copy_project(src: &Path, dest: &Path) -> Result<()> {
    fs::create_dir_all(dest)
        .with_context(|| format!("cannot create {}", dest.display()))?;
    copy_dir(src, dest)
}

fn copy_dir(src: &Path, dest: &Path) -> Result<()> {
    for entry in fs::read_dir(src).with_context(|| format!("cannot read {}", src.display()))? {
        let entry = entry?;
        let name = entry.file_name();
        let name_str = name.to_string_lossy();
        let path = entry.path();
        let file_type = entry.file_type()?;
        if file_type.is_dir() {
            if SKIP_DIRS.contains(&name_str.as_ref()) {
                continue;
            }
            let target = dest.join(&name);
            fs::create_dir_all(&target)?;
            copy_dir(&path, &target)?;
        } else if file_type.is_file() {
            fs::copy(&path, dest.join(&name))
                .with_context(|| format!("cannot copy {}", path.display()))?;
        }
        // Symlinks are skipped: a link escaping the project must never be
        // replicated into the shadow.
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn copies_sources_and_skips_dependencies() {
        let tmp = tempfile::tempdir().unwrap();
        let src = tmp.path().join("proj");
        fs::create_dir_all(src.join("src")).unwrap();
        fs::create_dir_all(src.join("node_modules").join("x")).unwrap();
        fs::write(src.join("src").join("main.js"), "code").unwrap();
        fs::write(src.join("package.json"), "{}").unwrap();

        let dest = tmp.path().join("shadow");
        copy_project(&src, &dest).unwrap();
        assert!(dest.join("src").join("main.js").is_file());
        assert!(dest.join("package.json").is_file());
        assert!(!dest.join("node_modules").exists());
    }
}
