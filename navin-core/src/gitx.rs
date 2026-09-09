// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

//! Git hot paths as library calls (libgit2): status, log, diff, branch.
//!
//! A `git status` subprocess costs 30-80 ms of spawn before any work happens;
//! these calls answer in well under a millisecond on warm trees. Output is
//! kept low-level (tuples, unified-diff text) so the Python tool layer keeps
//! formatting exactly as before.

use git2::{DiffFormat, DiffOptions, Repository, StatusOptions};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

fn open_repo(path: &str) -> PyResult<Repository> {
    Repository::discover(path).map_err(|e| PyValueError::new_err(format!("not a git repo: {e}")))
}

fn status_code(status: git2::Status) -> String {
    // Two-letter porcelain-style code: index column then worktree column.
    let index = if status.is_index_new() {
        'A'
    } else if status.is_index_modified() {
        'M'
    } else if status.is_index_deleted() {
        'D'
    } else if status.is_index_renamed() {
        'R'
    } else if status.is_index_typechange() {
        'T'
    } else {
        ' '
    };
    let worktree = if status.is_wt_new() {
        '?'
    } else if status.is_wt_modified() {
        'M'
    } else if status.is_wt_deleted() {
        'D'
    } else if status.is_wt_renamed() {
        'R'
    } else if status.is_wt_typechange() {
        'T'
    } else {
        ' '
    };
    if status.is_wt_new() {
        "??".to_owned()
    } else {
        format!("{index}{worktree}")
    }
}

/// Porcelain-style status: `(code, path)` per changed file, e.g. `("M ", "a.py")`.
#[pyfunction]
#[pyo3(signature = (repo_path, *, include_untracked = true))]
pub fn git_status(
    py: Python<'_>,
    repo_path: String,
    include_untracked: bool,
) -> PyResult<Vec<(String, String)>> {
    py.allow_threads(move || {
        let repo = open_repo(&repo_path)?;
        let mut opts = StatusOptions::new();
        opts.include_untracked(include_untracked)
            .recurse_untracked_dirs(include_untracked)
            .renames_head_to_index(true);
        let statuses = repo
            .statuses(Some(&mut opts))
            .map_err(|e| PyValueError::new_err(format!("status failed: {e}")))?;
        Ok(statuses
            .iter()
            .filter_map(|entry| {
                let path = entry.path()?.to_owned();
                Some((status_code(entry.status()), path))
            })
            .collect())
    })
}

/// Commits reachable from `rev` (default HEAD), newest first:
/// `(sha, author_name, author_email, unix_timestamp, summary)`.
#[pyfunction]
#[pyo3(signature = (repo_path, *, max_count = 20, rev = None))]
pub fn git_log(
    py: Python<'_>,
    repo_path: String,
    max_count: usize,
    rev: Option<String>,
) -> PyResult<Vec<(String, String, String, i64, String)>> {
    py.allow_threads(move || {
        let repo = open_repo(&repo_path)?;
        let mut walk = repo
            .revwalk()
            .map_err(|e| PyValueError::new_err(format!("revwalk failed: {e}")))?;
        match rev {
            Some(spec) => {
                let object = repo
                    .revparse_single(&spec)
                    .map_err(|e| PyValueError::new_err(format!("bad rev {spec:?}: {e}")))?;
                walk.push(object.id())
            }
            None => walk.push_head(),
        }
        .map_err(|e| PyValueError::new_err(format!("log failed: {e}")))?;

        let mut out = Vec::new();
        for oid in walk.flatten().take(max_count) {
            let Ok(commit) = repo.find_commit(oid) else {
                continue;
            };
            let author = commit.author();
            out.push((
                oid.to_string(),
                author.name().unwrap_or("").to_owned(),
                author.email().unwrap_or("").to_owned(),
                commit.time().seconds(),
                commit.summary().unwrap_or("").to_owned(),
            ));
        }
        Ok(out)
    })
}

/// Unified diff text.
///
/// `cached=false`: worktree vs index (like `git diff`).
/// `cached=true`: index vs HEAD (like `git diff --cached`).
/// `rev`: that commit vs the worktree instead.
#[pyfunction]
#[pyo3(signature = (repo_path, *, cached = false, rev = None, path = None, context_lines = 3))]
pub fn git_diff(
    py: Python<'_>,
    repo_path: String,
    cached: bool,
    rev: Option<String>,
    path: Option<String>,
    context_lines: u32,
) -> PyResult<String> {
    py.allow_threads(move || {
        let repo = open_repo(&repo_path)?;
        let mut opts = DiffOptions::new();
        opts.context_lines(context_lines).include_untracked(false);
        if let Some(p) = path {
            opts.pathspec(p);
        }

        let diff = if let Some(spec) = rev {
            let tree = repo
                .revparse_single(&spec)
                .and_then(|obj| obj.peel_to_tree())
                .map_err(|e| PyValueError::new_err(format!("bad rev {spec:?}: {e}")))?;
            repo.diff_tree_to_workdir_with_index(Some(&tree), Some(&mut opts))
        } else if cached {
            let head = repo.head().ok().and_then(|h| h.peel_to_tree().ok());
            repo.diff_tree_to_index(head.as_ref(), None, Some(&mut opts))
        } else {
            repo.diff_index_to_workdir(None, Some(&mut opts))
        }
        .map_err(|e| PyValueError::new_err(format!("diff failed: {e}")))?;

        let mut text = String::new();
        diff.print(DiffFormat::Patch, |_delta, _hunk, line| {
            let origin = line.origin();
            if matches!(origin, '+' | '-' | ' ') {
                text.push(origin);
            }
            text.push_str(&String::from_utf8_lossy(line.content()));
            true
        })
        .map_err(|e| PyValueError::new_err(format!("diff print failed: {e}")))?;
        Ok(text)
    })
}

/// Full working-tree snapshot mirroring `git status --porcelain=v2 --branch`,
/// the read that runs every turn in `navin/utils/git_state.py`.
///
/// Returns a tuple matching `RepoState` field order:
/// `(is_repo, branch, detached, head, upstream, ahead, behind,
///   staged, unstaged, untracked, conflicted)`.
///
/// Untracked directories are collapsed to a single `dir/` entry (trailing
/// slash), renames report the destination path, and `head` is the first 8 hex
/// of the commit oid (empty on an unborn branch) - all exactly as the porcelain
/// parser produces them.
#[pyfunction]
#[allow(clippy::type_complexity)]
pub fn git_state(
    py: Python<'_>,
    repo_path: String,
) -> PyResult<(
    bool,
    String,
    bool,
    String,
    String,
    i64,
    i64,
    Vec<String>,
    Vec<String>,
    Vec<String>,
    Vec<String>,
)> {
    py.allow_threads(move || {
        let repo = match Repository::discover(&repo_path) {
            Ok(repo) => repo,
            // Not a repository is a normal answer, not an error: is_repo=false.
            Err(_) => {
                return Ok((
                    false,
                    String::new(),
                    false,
                    String::new(),
                    String::new(),
                    0,
                    0,
                    vec![],
                    vec![],
                    vec![],
                    vec![],
                ));
            }
        };

        let detached = repo.head_detached().unwrap_or(false);
        let mut branch = String::new();
        let mut head = String::new();
        if let Ok(head_ref) = repo.head() {
            if let Some(oid) = head_ref.target() {
                head = oid.to_string().chars().take(8).collect();
            }
            if head_ref.is_branch() && !detached {
                branch = head_ref.shorthand().unwrap_or("").to_owned();
            }
        }

        // Upstream tracking and ahead/behind, only meaningful on a branch.
        let mut upstream = String::new();
        let mut ahead: i64 = 0;
        let mut behind: i64 = 0;
        if !branch.is_empty() {
            if let Ok(local) = repo.find_branch(&branch, git2::BranchType::Local) {
                if let Ok(up) = local.upstream() {
                    if let Ok(Some(name)) = up.name() {
                        upstream = name.to_owned();
                    }
                    if let (Some(local_oid), Some(up_oid)) =
                        (local.get().target(), up.get().target())
                    {
                        if let Ok((a, b)) = repo.graph_ahead_behind(local_oid, up_oid) {
                            ahead = a as i64;
                            behind = b as i64;
                        }
                    }
                }
            }
        }

        let mut opts = StatusOptions::new();
        opts.include_untracked(true)
            .recurse_untracked_dirs(false)
            .renames_head_to_index(true)
            .renames_index_to_workdir(true)
            .include_ignored(false)
            .include_unmodified(false)
            .exclude_submodules(false);

        let statuses = match repo.statuses(Some(&mut opts)) {
            Ok(s) => s,
            Err(_) => {
                // A repo we cannot stat is closer to "clean unknown" than an
                // error the model can act on; report the branch, no changes.
                return Ok((
                    true, branch, detached, head, upstream, ahead, behind, vec![], vec![],
                    vec![], vec![],
                ));
            }
        };

        let mut staged: Vec<String> = Vec::new();
        let mut unstaged: Vec<String> = Vec::new();
        let mut untracked: Vec<String> = Vec::new();
        let mut conflicted: Vec<String> = Vec::new();

        for entry in statuses.iter() {
            let st = entry.status();
            // A renamed entry names the destination in its rename delta; that is
            // the path the agent would write to, so prefer it over path().
            let index_path = entry
                .head_to_index()
                .and_then(|d| d.new_file().path())
                .and_then(|p| p.to_str())
                .map(str::to_owned);
            let wt_path = entry
                .index_to_workdir()
                .and_then(|d| d.new_file().path())
                .and_then(|p| p.to_str())
                .map(str::to_owned);
            let fallback = entry.path().map(str::to_owned);

            if st.is_conflicted() {
                if let Some(p) = wt_path.clone().or_else(|| fallback.clone()) {
                    push(&mut conflicted, p);
                }
                continue;
            }
            if st.is_wt_new() && !st.is_index_new() {
                if let Some(p) = wt_path.clone().or_else(|| fallback.clone()) {
                    push(&mut untracked, p);
                }
                // A file can be only untracked; nothing else applies.
                continue;
            }
            let staged_flags = st.is_index_new()
                || st.is_index_modified()
                || st.is_index_deleted()
                || st.is_index_renamed()
                || st.is_index_typechange();
            if staged_flags {
                if let Some(p) = index_path.clone().or_else(|| fallback.clone()) {
                    push(&mut staged, p);
                }
            }
            let wt_flags = st.is_wt_modified()
                || st.is_wt_deleted()
                || st.is_wt_renamed()
                || st.is_wt_typechange();
            if wt_flags {
                if let Some(p) = wt_path.or(fallback) {
                    push(&mut unstaged, p);
                }
            }
        }

        Ok((
            true, branch, detached, head, upstream, ahead, behind, staged, unstaged, untracked,
            conflicted,
        ))
    })
}

fn push(bucket: &mut Vec<String>, path: String) {
    if !path.is_empty() && !bucket.contains(&path) {
        bucket.push(path);
    }
}

/// Current branch name, or None when HEAD is detached or unborn.
#[pyfunction]
pub fn git_current_branch(py: Python<'_>, repo_path: String) -> PyResult<Option<String>> {
    py.allow_threads(move || {
        let repo = open_repo(&repo_path)?;
        let branch = match repo.head() {
            Ok(head) if head.is_branch() => head.shorthand().map(str::to_owned),
            _ => None,
        };
        Ok(branch)
    })
}
