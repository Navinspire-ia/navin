//! Ranked full-text search over the project, backed by tantivy.
//!
//! The Python code index owns change detection (it already fingerprints every
//! file); this module only maintains the on-disk tantivy index it is handed
//! and answers BM25 queries. Both operations release the GIL: indexing a
//! large batch is CPU-bound in tantivy's segment writers, queries are
//! mmap-bound.

use std::fs;
use std::io::Read;
use std::path::Path;

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use rayon::prelude::*;
use serde::Serialize;
use tantivy::collector::TopDocs;
use tantivy::query::QueryParser;
use tantivy::schema::{Schema, Value, INDEXED, STORED, STRING, TEXT};
use tantivy::snippet::SnippetGenerator;
use tantivy::{doc, Index, TantivyDocument, Term};

const MAX_FILE_BYTES: u64 = 2 * 1024 * 1024;
const WRITER_BUDGET_BYTES: usize = 64 * 1024 * 1024;

#[derive(Serialize)]
struct Hit {
    path: String,
    score: f32,
    snippet: String,
}

fn schema() -> Schema {
    let mut builder = Schema::builder();
    builder.add_text_field("path", STRING | STORED);
    builder.add_text_field("content", TEXT | STORED);
    builder.add_i64_field("mtime_ns", INDEXED | STORED);
    builder.build()
}

fn open_or_create(index_dir: &Path) -> Result<Index, String> {
    fs::create_dir_all(index_dir).map_err(|e| e.to_string())?;
    match Index::open_in_dir(index_dir) {
        Ok(index) => Ok(index),
        Err(_) => Index::create_in_dir(index_dir, schema()).map_err(|e| e.to_string()),
    }
}

fn readable_text(full: &Path) -> Option<String> {
    let mut file = fs::File::open(full).ok()?;
    let meta = file.metadata().ok()?;
    if !meta.is_file() || meta.len() > MAX_FILE_BYTES {
        return None;
    }
    let mut bytes = Vec::with_capacity(meta.len() as usize);
    file.read_to_end(&mut bytes).ok()?;
    let probe = &bytes[..bytes.len().min(4096)];
    if probe.contains(&0) {
        return None; // binary
    }
    Some(String::from_utf8_lossy(&bytes).into_owned())
}

fn update_impl(
    index_dir: &Path,
    root: &Path,
    changed: &[(String, i64)],
    removed: &[String],
) -> Result<usize, String> {
    let index = open_or_create(index_dir)?;
    let schema = index.schema();
    let path_field = schema.get_field("path").map_err(|e| e.to_string())?;
    let content_field = schema.get_field("content").map_err(|e| e.to_string())?;
    let mtime_field = schema.get_field("mtime_ns").map_err(|e| e.to_string())?;

    let mut writer = index
        .writer::<TantivyDocument>(WRITER_BUDGET_BYTES)
        .map_err(|e| e.to_string())?;
    for rel in removed {
        writer.delete_term(Term::from_field_text(path_field, rel));
    }
    // Always delete changed paths first (same as before): unreadable / binary
    // files stay removed from the index even when content cannot be re-added.
    for (rel, _) in changed {
        writer.delete_term(Term::from_field_text(path_field, rel));
    }

    // Read file bodies in parallel; tantivy writer stays single-threaded.
    let documents: Vec<(String, i64, String)> = changed
        .par_iter()
        .filter_map(|(rel, mtime_ns)| {
            let content = readable_text(&root.join(rel))?;
            Some((rel.clone(), *mtime_ns, content))
        })
        .collect();

    let mut indexed = 0usize;
    for (rel, mtime_ns, content) in documents {
        writer
            .add_document(doc!(
                path_field => rel.as_str(),
                content_field => content,
                mtime_field => mtime_ns,
            ))
            .map_err(|e| e.to_string())?;
        indexed += 1;
    }
    writer.commit().map_err(|e| e.to_string())?;
    Ok(indexed)
}

fn search_impl(index_dir: &Path, query_text: &str, limit: usize) -> Result<String, String> {
    let index = Index::open_in_dir(index_dir).map_err(|e| e.to_string())?;
    let schema = index.schema();
    let path_field = schema.get_field("path").map_err(|e| e.to_string())?;
    let content_field = schema.get_field("content").map_err(|e| e.to_string())?;

    let reader = index.reader().map_err(|e| e.to_string())?;
    let searcher = reader.searcher();
    let parser = QueryParser::for_index(&index, vec![content_field]);
    let (query, _errors) = parser.parse_query_lenient(query_text);

    let top = searcher
        .search(&query, &TopDocs::with_limit(limit.max(1)).order_by_score())
        .map_err(|e| e.to_string())?;
    let snippets = SnippetGenerator::create(&searcher, &query, content_field).ok();

    let mut hits: Vec<Hit> = Vec::with_capacity(top.len());
    for (score, address) in top {
        let document: TantivyDocument = searcher.doc(address).map_err(|e| e.to_string())?;
        let path = document
            .get_first(path_field)
            .and_then(|value| value.as_str())
            .unwrap_or("")
            .to_string();
        if path.is_empty() {
            continue;
        }
        let snippet = snippets
            .as_ref()
            .map(|generator| generator.snippet_from_doc(&document).fragment().to_string())
            .unwrap_or_default();
        hits.push(Hit { path, score, snippet });
    }
    serde_json::to_string(&hits).map_err(|e| e.to_string())
}

/// Add, replace or remove documents in the full-text index.
///
/// `changed` carries `(rel_path, mtime_ns)` pairs to (re)index from `root`;
/// `removed` carries rel_paths to drop. Returns how many files were indexed.
#[pyfunction]
#[pyo3(signature = (index_dir, root, changed, removed))]
pub fn fulltext_update(
    py: Python<'_>,
    index_dir: &str,
    root: &str,
    changed: Vec<(String, i64)>,
    removed: Vec<String>,
) -> PyResult<usize> {
    let index_dir = Path::new(index_dir).to_path_buf();
    let root = Path::new(root).to_path_buf();
    py.allow_threads(move || update_impl(&index_dir, &root, &changed, &removed))
        .map_err(PyRuntimeError::new_err)
}

/// BM25 search; returns a JSON array of `{path, score, snippet}`.
#[pyfunction]
#[pyo3(signature = (index_dir, query, limit = 20))]
pub fn fulltext_search(
    py: Python<'_>,
    index_dir: &str,
    query: &str,
    limit: usize,
) -> PyResult<String> {
    let index_dir = Path::new(index_dir).to_path_buf();
    let query = query.to_string();
    py.allow_threads(move || search_impl(&index_dir, &query, limit))
        .map_err(PyRuntimeError::new_err)
}
