//! Local file hot paths: batch reads, directory walking, content hashing.
//!
//! The agent routinely reads ten files in one turn and hashes whole trees for
//! the incremental code index; doing that from Python serializes on the GIL.
//! Everything here releases the GIL and fans out with rayon.

use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::path::Path;

use memmap2::Mmap;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use rayon::prelude::*;

/// Files at or above this size are mmap'd for hashing (no full copy into a
/// `Vec`). Reads always go through a single open + `read_to_end`: mmap then
/// `to_vec` was strictly worse than a plain read for the `read_files` path.
const MMAP_THRESHOLD: u64 = 256 * 1024;

fn read_one(path: &str, max_bytes: u64) -> Result<Vec<u8>, String> {
    let mut file = fs::File::open(path).map_err(|e| e.to_string())?;
    let meta = file.metadata().map_err(|e| e.to_string())?;
    if !meta.is_file() {
        return Err("not a regular file".to_owned());
    }
    let len = meta.len();
    if len > max_bytes {
        return Err(format!("file too large ({} bytes)", len));
    }
    // Single open: avoid the previous open-for-stat + `fs::read` reopen.
    let mut buf = Vec::with_capacity(len as usize);
    file.read_to_end(&mut buf).map_err(|e| e.to_string())?;
    Ok(buf)
}

/// Read many files in parallel.
///
/// Returns `{path: (bytes | None, error | None)}`: exactly one of the two is
/// set per entry. Decoding stays in Python (`navin.utils.text_decode`), so
/// encoding behaviour is byte-identical with the pure-Python path.
#[pyfunction]
#[pyo3(signature = (paths, *, max_bytes = 50_000_000))]
pub fn read_files(
    py: Python<'_>,
    paths: Vec<String>,
    max_bytes: u64,
) -> PyResult<HashMap<String, (Option<Py<PyBytes>>, Option<String>)>> {
    let results: Vec<(String, Result<Vec<u8>, String>)> = py.allow_threads(|| {
        paths
            .into_par_iter()
            .map(|p| {
                let r = read_one(&p, max_bytes);
                (p, r)
            })
            .collect()
    });
    let mut out = HashMap::with_capacity(results.len());
    for (path, result) in results {
        match result {
            Ok(bytes) => out.insert(path, (Some(PyBytes::new(py, &bytes).into()), None)),
            Err(err) => out.insert(path, (None, Some(err))),
        };
    }
    Ok(out)
}

/// Walk a tree, skipping directories by name, and return
/// `(path, is_dir, mtime, size)` per entry.
///
/// Matches the semantics of the `os.walk` loops in
/// `navin/agent/tools/search.py`: hidden files included, symlinks not
/// followed, no gitignore handling (callers layer that on top when needed).
#[pyfunction]
#[pyo3(signature = (root, *, skip_dirs = Vec::new(), include_dirs = false, max_entries = 1_000_000))]
pub fn walk(
    py: Python<'_>,
    root: String,
    skip_dirs: Vec<String>,
    include_dirs: bool,
    max_entries: usize,
) -> PyResult<Vec<(String, bool, f64, u64)>> {
    Ok(py.allow_threads(move || {
        let skip: std::collections::HashSet<String> = skip_dirs.into_iter().collect();
        let mut out: Vec<(String, bool, f64, u64)> = Vec::new();
        let root_path = Path::new(&root);
        if root_path.is_file() {
            if let Ok(meta) = root_path.metadata() {
                out.push((root, false, mtime_of(&meta), meta.len()));
            }
            return out;
        }

        let mut builder = ignore::WalkBuilder::new(root_path);
        builder
            .standard_filters(false)
            .hidden(false)
            .follow_links(false)
            .sort_by_file_name(std::cmp::Ord::cmp)
            .filter_entry(move |entry| {
                if entry.file_type().is_some_and(|t| t.is_dir()) {
                    if let Some(name) = entry.file_name().to_str() {
                        return !skip.contains(name);
                    }
                }
                true
            });

        for entry in builder.build().flatten() {
            if out.len() >= max_entries {
                break;
            }
            if entry.depth() == 0 {
                continue; // the root itself, like os.walk
            }
            let is_dir = entry.file_type().is_some_and(|t| t.is_dir());
            if is_dir && !include_dirs {
                continue;
            }
            let Some(path_text) = entry.path().to_str().map(str::to_owned) else {
                continue;
            };
            let (mtime, size) = entry
                .metadata()
                .map(|m| (mtime_of(&m), m.len()))
                .unwrap_or((0.0, 0));
            out.push((path_text, is_dir, mtime, size));
        }
        out
    }))
}

fn mtime_of(meta: &fs::Metadata) -> f64 {
    meta.modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// Nanosecond mtime, matching Python's ``os.stat().st_mtime_ns`` (both read
/// the same ``st_mtim`` on Linux, so the fingerprints compare equal).
fn mtime_ns_of(meta: &fs::Metadata) -> i64 {
    use std::time::UNIX_EPOCH;
    match meta.modified() {
        Ok(t) => match t.duration_since(UNIX_EPOCH) {
            Ok(d) => d.as_nanos() as i64,
            Err(e) => -(e.duration().as_nanos() as i64),
        },
        Err(_) => 0,
    }
}

/// Stat many workspace-relative paths under ``root`` in parallel:
/// ``{rel: (mtime_ns, size)}``. Paths that cannot be stat'd are simply absent,
/// so the caller skips them exactly as a failing ``os.stat`` would.
///
/// This is the code index's change detector: one native pass replaces a
/// per-file Python ``stat()`` loop over the whole repo, and the ``(mtime_ns,
/// size)`` pair is byte-identical to what the pure-Python path stored, so the
/// cache stays valid across the switch.
#[pyfunction]
pub fn stat_tree(
    py: Python<'_>,
    root: String,
    rels: Vec<String>,
) -> PyResult<HashMap<String, (i64, u64)>> {
    Ok(py.allow_threads(move || {
        let root_path = Path::new(&root);
        rels.into_par_iter()
            .filter_map(|rel| {
                let meta = fs::metadata(root_path.join(&rel)).ok()?;
                Some((rel, (mtime_ns_of(&meta), meta.len())))
            })
            .collect()
    }))
}

/// BLAKE3-hash many files in parallel: `{path: hex_digest}`.
///
/// Unreadable files are simply absent from the result. This is the change
/// detector for the incremental code index: hashing a whole tree is pure
/// CPU + IO and needs neither the GIL nor Python's per-call overhead.
#[pyfunction]
#[pyo3(signature = (paths, *, max_bytes = 50_000_000))]
pub fn hash_files(
    py: Python<'_>,
    paths: Vec<String>,
    max_bytes: u64,
) -> PyResult<HashMap<String, String>> {
    Ok(py.allow_threads(move || {
        paths
            .into_par_iter()
            .filter_map(|p| {
                let mut file = fs::File::open(&p).ok()?;
                let meta = file.metadata().ok()?;
                if !meta.is_file() || meta.len() > max_bytes {
                    return None;
                }
                let mut hasher = blake3::Hasher::new();
                if meta.len() >= MMAP_THRESHOLD {
                    // Safety: the mapping is hashed before the file handle
                    // drops; a concurrent truncation is the same hazard a
                    // plain read has.
                    let map = unsafe { Mmap::map(&file) }.ok()?;
                    hasher.update_rayon(&map);
                } else {
                    // Reuse the open handle instead of `fs::read` reopen.
                    let mut buf = Vec::with_capacity(meta.len() as usize);
                    file.read_to_end(&mut buf).ok()?;
                    hasher.update(&buf);
                }
                Some((p, hasher.finalize().to_hex().to_string()))
            })
            .collect()
    }))
}
