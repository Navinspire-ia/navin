//! Lend the workspace's installed dependencies to a shadow.
//!
//! A worktree shadow holds the tracked files only, and the copy fallback
//! deliberately skips dependency folders. Either way `npm run dev` inside a
//! fresh shadow dies with "command not found" because `node_modules` is not
//! there. Installing from scratch would cost minutes per run, so the shadow
//! borrows what the workspace already installed: one symlink per package
//! directory, pointing back at the original tree.
//!
//! The link is a loan, not a copy: dependencies are inputs the experiment
//! reads, while everything it writes (build output, caches keyed on the
//! project directory) stays inside the shadow.

use std::path::{Path, PathBuf};
use tracing::debug;

/// Dependency folders worth lending, keyed by the manifest that proves the
/// package manager is in use. Global caches (Go modules, Cargo registry,
/// Maven repository) need no help: they already live outside the project.
const LENT: &[(&str, &[&str])] = &[
    ("package.json", &["node_modules"]),
    ("pyproject.toml", &[".venv", "venv", "env"]),
    ("requirements.txt", &[".venv", "venv", "env"]),
    ("setup.py", &[".venv", "venv", "env"]),
    ("composer.json", &["vendor"]),
    ("Gemfile", &["vendor/bundle"]),
];

/// Directories never worth descending into when looking for packages.
const SKIP: &[&str] = &[
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

/// How deep a package may sit below the root. Deep enough for the usual
/// monorepo (`packages/api`, `site/front`), shallow enough to stay cheap.
const MAX_DEPTH: usize = 3;

/// Link every dependency folder the workspace installed into the matching
/// place in the shadow. Returns how many were lent.
///
/// Best effort by design: a missing or unlinkable folder only means the
/// experiment will have to install it, never that the shadow is unusable.
pub fn lend_installed(origin: &Path, shadow: &Path) -> usize {
    let mut lent = 0;
    for package in packages(shadow, MAX_DEPTH) {
        let Ok(relative) = package.strip_prefix(shadow) else { continue };
        let source_dir = origin.join(relative);
        for name in folders_for(&package) {
            let source = source_dir.join(name);
            let destination = package.join(name);
            if !source.is_dir() || destination.exists() {
                continue;
            }
            if let Some(parent) = destination.parent() {
                if std::fs::create_dir_all(parent).is_err() {
                    continue;
                }
            }
            match symlink_dir(&source, &destination) {
                Ok(()) => {
                    debug!("lent {} to the shadow", relative.join(name).display());
                    lent += 1;
                }
                Err(err) => debug!("cannot lend {}: {err}", source.display()),
            }
        }
    }
    lent
}

/// The dependency folder names a package directory could use, deduplicated
/// so a Python project declaring both a pyproject and requirements does not
/// get its virtualenv considered twice.
fn folders_for(dir: &Path) -> Vec<&'static str> {
    let mut names: Vec<&'static str> = Vec::new();
    for (manifest, folders) in LENT {
        if !dir.join(manifest).is_file() {
            continue;
        }
        for folder in *folders {
            if !names.contains(folder) {
                names.push(folder);
            }
        }
    }
    names
}

/// Directories holding a package manifest, root first.
fn packages(root: &Path, depth: usize) -> Vec<PathBuf> {
    let mut found = Vec::new();
    if LENT.iter().any(|(manifest, _)| root.join(manifest).is_file()) {
        found.push(root.to_path_buf());
    }
    if depth == 0 {
        return found;
    }
    let Ok(entries) = std::fs::read_dir(root) else { return found };
    let mut children: Vec<PathBuf> = entries
        .flatten()
        .filter(|entry| entry.file_type().map(|kind| kind.is_dir()).unwrap_or(false))
        .map(|entry| entry.path())
        .filter(|path| {
            let name = path.file_name().unwrap_or_default().to_string_lossy().into_owned();
            !SKIP.contains(&name.as_str()) && !name.starts_with('.')
        })
        .collect();
    children.sort();
    for child in children {
        found.extend(packages(&child, depth - 1));
    }
    found
}

#[cfg(unix)]
fn symlink_dir(source: &Path, destination: &Path) -> std::io::Result<()> {
    std::os::unix::fs::symlink(source, destination)
}

/// Windows needs either developer mode or elevation for directory symlinks;
/// when it refuses, the caller simply gets one less loan.
#[cfg(windows)]
fn symlink_dir(source: &Path, destination: &Path) -> std::io::Result<()> {
    std::os::windows::fs::symlink_dir(source, destination)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn touch(path: &Path) {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(path, "{}").unwrap();
    }

    #[test]
    fn node_and_python_dependencies_are_lent_to_the_shadow() {
        let tmp = tempfile::tempdir().unwrap();
        let origin = tmp.path().join("origin");
        let shadow = tmp.path().join("shadow");

        touch(&origin.join("pyproject.toml"));
        touch(&origin.join(".venv").join("bin").join("python"));
        touch(&origin.join("site").join("front").join("package.json"));
        touch(&origin.join("site").join("front").join("node_modules").join(".bin").join("next"));
        touch(&shadow.join("pyproject.toml"));
        touch(&shadow.join("site").join("front").join("package.json"));

        assert_eq!(lend_installed(&origin, &shadow), 2);
        assert!(shadow.join(".venv").join("bin").join("python").is_file());
        assert!(shadow
            .join("site")
            .join("front")
            .join("node_modules")
            .join(".bin")
            .join("next")
            .is_file());
    }

    #[test]
    fn nothing_is_lent_when_the_workspace_installed_nothing() {
        let tmp = tempfile::tempdir().unwrap();
        let origin = tmp.path().join("origin");
        let shadow = tmp.path().join("shadow");
        touch(&origin.join("package.json"));
        touch(&shadow.join("package.json"));

        assert_eq!(lend_installed(&origin, &shadow), 0);
        assert!(!shadow.join("node_modules").exists());
    }

    #[test]
    fn an_existing_folder_in_the_shadow_is_left_alone() {
        let tmp = tempfile::tempdir().unwrap();
        let origin = tmp.path().join("origin");
        let shadow = tmp.path().join("shadow");
        touch(&origin.join("package.json"));
        touch(&origin.join("node_modules").join("lent"));
        touch(&shadow.join("package.json"));
        touch(&shadow.join("node_modules").join("own"));

        assert_eq!(lend_installed(&origin, &shadow), 0);
        assert!(shadow.join("node_modules").join("own").is_file());
        assert!(!shadow.join("node_modules").join("lent").exists());
    }
}
