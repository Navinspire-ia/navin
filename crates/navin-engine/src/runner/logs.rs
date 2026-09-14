//! Log capture for supervised processes.

use anyhow::{Context, Result};
use std::fs::File;
use std::path::Path;

pub fn open_log(path: &Path) -> Result<File> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("cannot create {}", parent.display()))?;
    }
    File::create(path).with_context(|| format!("cannot create log {}", path.display()))
}

/// Last `lines` lines of a log, for failure evidence and error messages.
pub fn tail(path: &Path, lines: usize) -> String {
    let Ok(text) = std::fs::read_to_string(path) else {
        return String::new();
    };
    let all: Vec<&str> = text.lines().collect();
    let start = all.len().saturating_sub(lines);
    all[start..].join("\n")
}
