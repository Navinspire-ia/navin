// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

//! navin-core: native (Rust) hot paths for the navin agent tools.
//!
//! Every function here is an optional accelerator: the Python side keeps a
//! pure-Python fallback, so the agent works without the extension, just
//! slower. Contracts therefore mirror the existing Python call sites exactly.

use pyo3::prelude::*;

mod fs;
mod fulltext;
mod gitx;
mod graph;
mod mobile;
mod procs;
mod pty;
mod scrape;
mod search;
mod symbols;

#[pymodule]
fn navin_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(search::grep_scan, m)?)?;
    m.add_function(wrap_pyfunction!(fs::read_files, m)?)?;
    m.add_function(wrap_pyfunction!(fs::walk, m)?)?;
    m.add_function(wrap_pyfunction!(fs::hash_files, m)?)?;
    m.add_function(wrap_pyfunction!(fs::stat_tree, m)?)?;
    m.add_function(wrap_pyfunction!(procs::list_processes, m)?)?;
    m.add_function(wrap_pyfunction!(procs::process_tree, m)?)?;
    m.add_function(wrap_pyfunction!(procs::kill_tree, m)?)?;
    m.add_function(wrap_pyfunction!(gitx::git_state, m)?)?;
    m.add_function(wrap_pyfunction!(gitx::git_status, m)?)?;
    m.add_function(wrap_pyfunction!(gitx::git_log, m)?)?;
    m.add_function(wrap_pyfunction!(gitx::git_diff, m)?)?;
    m.add_function(wrap_pyfunction!(gitx::git_current_branch, m)?)?;
    m.add_function(wrap_pyfunction!(symbols::ts_extract, m)?)?;
    m.add_function(wrap_pyfunction!(fulltext::fulltext_update, m)?)?;
    m.add_function(wrap_pyfunction!(fulltext::fulltext_search, m)?)?;
    m.add_function(wrap_pyfunction!(scrape::scrape_fetch, m)?)?;
    m.add_function(wrap_pyfunction!(scrape::scrape_extract, m)?)?;
    m.add_function(wrap_pyfunction!(scrape::scrape_clean, m)?)?;
    m.add_function(wrap_pyfunction!(scrape::scrape_export, m)?)?;
    m.add_function(wrap_pyfunction!(graph::graph_build, m)?)?;
    m.add_function(wrap_pyfunction!(graph::graph_layout, m)?)?;
    m.add_function(wrap_pyfunction!(graph::graph_diff, m)?)?;
    m.add_function(wrap_pyfunction!(graph::graph_query, m)?)?;
    m.add_class::<pty::PtySession>()?;
    m.add_class::<mobile::MobilePreviewSession>()?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
