// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

//! Content search.
//!
//! `grep_scan` mirrors the contract of `_rg_scan` in
//! `navin/agent/tools/search.py`: it returns matching line numbers and text
//! per absolute path, honours .gitignore (inside a git repo, like ripgrep),
//! skips binary files and files over `max_file_bytes`, and raises
//! `ValueError` for patterns the regex engine rejects (lookaround,
//! backreferences) so the Python caller can fall back to its `re` backend.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc;
use std::thread;

use grep_regex::RegexMatcherBuilder;
use grep_searcher::sinks::UTF8;
use grep_searcher::{BinaryDetection, SearcherBuilder};
use ignore::{WalkBuilder, WalkState};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

type Hits = HashMap<String, Vec<(u64, String)>>;
type FileHit = (String, Vec<(u64, String)>);

#[pyfunction]
#[pyo3(signature = (
    target,
    pattern,
    *,
    fixed_strings = false,
    case_insensitive = false,
    include_ignored = false,
    max_file_bytes = 2_000_000,
    skip_dirs = Vec::new(),
    max_total_matches = 20_000,
))]
#[allow(clippy::too_many_arguments)]
pub fn grep_scan(
    py: Python<'_>,
    target: String,
    pattern: String,
    fixed_strings: bool,
    case_insensitive: bool,
    include_ignored: bool,
    max_file_bytes: u64,
    skip_dirs: Vec<String>,
    max_total_matches: usize,
) -> PyResult<Hits> {
    let needle = if fixed_strings {
        regex::escape(&pattern)
    } else {
        pattern
    };
    let matcher = RegexMatcherBuilder::new()
        .case_insensitive(case_insensitive)
        .build(&needle)
        .map_err(|err| PyValueError::new_err(format!("pattern not supported: {err}")))?;

    // The walk and search are pure Rust work; release the GIL so the agent's
    // event loop keeps breathing while we scan.
    Ok(py.allow_threads(move || {
        let skip: HashSet<String> = skip_dirs.into_iter().collect();
        let total = AtomicUsize::new(0);
        let (tx, rx) = mpsc::channel::<FileHit>();

        let mut builder = WalkBuilder::new(&target);
        let threads = thread::available_parallelism()
            .map(|n| n.get())
            .unwrap_or(1)
            .max(1);
        builder
            // rg is invoked with --hidden --no-follow; gitignore handling
            // only applies inside a git work tree, exactly like ripgrep.
            .hidden(false)
            .follow_links(false)
            .max_filesize(Some(max_file_bytes))
            .git_ignore(!include_ignored)
            .git_global(!include_ignored)
            .git_exclude(!include_ignored)
            .require_git(true)
            .threads(threads)
            .filter_entry(move |entry| {
                if entry.file_type().is_some_and(|t| t.is_dir()) {
                    if let Some(name) = entry.file_name().to_str() {
                        return !skip.contains(name);
                    }
                }
                true
            });

        builder.build_parallel().run(|| {
            let matcher = matcher.clone();
            let tx = tx.clone();
            let total = &total;
            let mut searcher = SearcherBuilder::new()
                .binary_detection(BinaryDetection::quit(b'\x00'))
                .line_number(true)
                .build();
            Box::new(move |entry| {
                if total.load(Ordering::Relaxed) >= max_total_matches {
                    return WalkState::Quit;
                }
                let Ok(entry) = entry else {
                    return WalkState::Continue;
                };
                if !entry.file_type().is_some_and(|t| t.is_file()) {
                    return WalkState::Continue;
                }
                let path = entry.path();
                // Non-UTF-8 paths cannot round-trip through the tool output;
                // the ripgrep backend skips them too. Probe without allocating.
                let Some(path_str) = path.to_str() else {
                    return WalkState::Continue;
                };
                let mut file_matches: Vec<(u64, String)> = Vec::new();
                let _ = searcher.search_path(
                    &matcher,
                    path,
                    UTF8(|line_no, line| {
                        if total.fetch_add(1, Ordering::Relaxed) >= max_total_matches {
                            return Ok(false);
                        }
                        file_matches
                            .push((line_no, line.trim_end_matches(['\n', '\r']).to_owned()));
                        Ok(true)
                    }),
                );
                // Allocate the owned path only when the file actually matched.
                if !file_matches.is_empty() {
                    let _ = tx.send((path_str.to_owned(), file_matches));
                }
                WalkState::Continue
            })
        });
        // Drop the original sender so `rx` ends once every worker clone is gone.
        drop(tx);

        rx.into_iter().collect()
    }))
}
