//! Parallel web scrape / extract / clean / export hot path.
//!
//! Called from the Python `scrape` tool. URL safety (SSRF) is enforced on the
//! Python side before these functions see a URL.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rayon::prelude::*;
use scraper::{Html, Selector};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{HashSet, VecDeque};
use std::fs;
use std::io::Write;
use std::path::Path;
use std::time::Duration;
use url::Url;

const DEFAULT_UA: &str =
    "Mozilla/5.0 (compatible; NavinScrape/0.1; +https://navin.ai) AppleWebKit/537.36";
const DEFAULT_TIMEOUT_SECS: u64 = 30;
const DEFAULT_MAX_BYTES: usize = 5 * 1024 * 1024;
const HARD_MAX_PAGES: usize = 500;
const HARD_MAX_CONCURRENCY: usize = 32;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct FetchOptions {
    #[serde(default = "default_timeout")]
    timeout_secs: u64,
    #[serde(default = "default_max_bytes")]
    max_bytes: usize,
    #[serde(default)]
    user_agent: Option<String>,
    #[serde(default)]
    proxy: Option<String>,
    #[serde(default = "default_true")]
    follow_redirects: bool,
    #[serde(default = "default_concurrency")]
    concurrency: usize,
}

fn default_timeout() -> u64 {
    DEFAULT_TIMEOUT_SECS
}
fn default_max_bytes() -> usize {
    DEFAULT_MAX_BYTES
}
fn default_true() -> bool {
    true
}
fn default_concurrency() -> usize {
    8
}

impl Default for FetchOptions {
    fn default() -> Self {
        Self {
            timeout_secs: DEFAULT_TIMEOUT_SECS,
            max_bytes: DEFAULT_MAX_BYTES,
            user_agent: None,
            proxy: None,
            follow_redirects: true,
            concurrency: 8,
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CrawlOptions {
    #[serde(flatten)]
    fetch: FetchOptions,
    #[serde(default = "default_depth")]
    max_depth: usize,
    #[serde(default = "default_max_pages")]
    max_pages: usize,
    #[serde(default = "default_true")]
    same_domain: bool,
}

fn default_depth() -> usize {
    1
}
fn default_max_pages() -> usize {
    25
}

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct PageRecord {
    url: String,
    final_url: String,
    status: u16,
    title: String,
    text: String,
    markdown: String,
    links: Vec<String>,
    meta: serde_json::Map<String, Value>,
    content_type: String,
    bytes: usize,
    error: Option<String>,
}

fn parse_options(raw: Option<&str>) -> PyResult<FetchOptions> {
    match raw {
        None | Some("") => Ok(FetchOptions::default()),
        Some(s) => serde_json::from_str(s)
            .map_err(|e| PyValueError::new_err(format!("invalid options JSON: {e}"))),
    }
}

fn parse_crawl_options(raw: Option<&str>) -> PyResult<CrawlOptions> {
    match raw {
        None | Some("") => Ok(CrawlOptions {
            fetch: FetchOptions::default(),
            max_depth: 1,
            max_pages: 25,
            same_domain: true,
        }),
        Some(s) => serde_json::from_str(s)
            .map_err(|e| PyValueError::new_err(format!("invalid options JSON: {e}"))),
    }
}

fn build_client(opts: &FetchOptions) -> Result<reqwest::blocking::Client, String> {
    let mut builder = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(opts.timeout_secs.max(1)))
        .user_agent(opts.user_agent.as_deref().unwrap_or(DEFAULT_UA))
        .redirect(if opts.follow_redirects {
            reqwest::redirect::Policy::limited(5)
        } else {
            reqwest::redirect::Policy::none()
        })
        .pool_max_idle_per_host(opts.concurrency.max(1).min(HARD_MAX_CONCURRENCY));
    if let Some(proxy) = opts.proxy.as_deref().filter(|p| !p.is_empty()) {
        let p = reqwest::Proxy::all(proxy).map_err(|e| e.to_string())?;
        builder = builder.proxy(p);
    }
    builder.build().map_err(|e| e.to_string())
}

fn fetch_one(client: &reqwest::blocking::Client, url: &str, max_bytes: usize) -> PageRecord {
    let mut record = PageRecord {
        url: url.to_string(),
        final_url: url.to_string(),
        status: 0,
        title: String::new(),
        text: String::new(),
        markdown: String::new(),
        links: Vec::new(),
        meta: serde_json::Map::new(),
        content_type: String::new(),
        bytes: 0,
        error: None,
    };
    match client.get(url).send() {
        Ok(resp) => {
            record.status = resp.status().as_u16();
            record.final_url = resp.url().to_string();
            record.content_type = resp
                .headers()
                .get(reqwest::header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok())
                .unwrap_or("")
                .to_string();
            match resp.bytes() {
                Ok(bytes) => {
                    let sliced = if bytes.len() > max_bytes {
                        &bytes[..max_bytes]
                    } else {
                        &bytes
                    };
                    record.bytes = sliced.len();
                    let body = String::from_utf8_lossy(sliced).into_owned();
                    if looks_like_html(&record.content_type, &body) {
                        enrich_from_html(&mut record, &body);
                    } else {
                        record.text = clean_text(&body);
                        record.markdown = record.text.clone();
                    }
                }
                Err(e) => record.error = Some(format!("read body: {e}")),
            }
        }
        Err(e) => record.error = Some(e.to_string()),
    }
    record
}

fn looks_like_html(content_type: &str, body: &str) -> bool {
    let ct = content_type.to_ascii_lowercase();
    if ct.contains("html") || ct.contains("xml") {
        return true;
    }
    let trimmed = body.trim_start();
    trimmed.starts_with("<!DOCTYPE")
        || trimmed.starts_with("<!doctype")
        || trimmed.starts_with("<html")
        || trimmed.starts_with("<HTML")
}

fn enrich_from_html(record: &mut PageRecord, html: &str) {
    let extracted = extract_html(html, &record.final_url);
    record.title = extracted.title;
    record.text = extracted.text;
    record.markdown = extracted.markdown;
    record.links = extracted.links;
    record.meta = extracted.meta;
}

#[derive(Default)]
struct Extracted {
    title: String,
    text: String,
    markdown: String,
    links: Vec<String>,
    meta: serde_json::Map<String, Value>,
}

fn extract_html(html: &str, base_url: &str) -> Extracted {
    let document = Html::parse_document(html);
    let mut out = Extracted::default();

    if let Ok(sel) = Selector::parse("title") {
        if let Some(node) = document.select(&sel).next() {
            out.title = clean_text(&node.text().collect::<String>());
        }
    }
    if out.title.is_empty() {
        if let Ok(sel) = Selector::parse("h1") {
            if let Some(node) = document.select(&sel).next() {
                out.title = clean_text(&node.text().collect::<String>());
            }
        }
    }

    if let Ok(sel) = Selector::parse("meta") {
        for el in document.select(&sel) {
            let name = el
                .value()
                .attr("name")
                .or_else(|| el.value().attr("property"))
                .unwrap_or("")
                .to_ascii_lowercase();
            let content = el.value().attr("content").unwrap_or("").trim();
            if name.is_empty() || content.is_empty() {
                continue;
            }
            if matches!(
                name.as_str(),
                "description"
                    | "og:title"
                    | "og:description"
                    | "og:url"
                    | "og:type"
                    | "twitter:title"
                    | "twitter:description"
                    | "keywords"
                    | "author"
                    | "robots"
            ) {
                out.meta
                    .insert(name, Value::String(content.to_string()));
            }
        }
    }

    let base = Url::parse(base_url).ok();
    if let Ok(sel) = Selector::parse("a[href]") {
        let mut seen = HashSet::new();
        for el in document.select(&sel) {
            let href = el.value().attr("href").unwrap_or("").trim();
            if href.is_empty() || href.starts_with('#') || href.starts_with("javascript:") {
                continue;
            }
            let absolute = match (&base, Url::parse(href)) {
                (_, Ok(u)) if matches!(u.scheme(), "http" | "https") => u.to_string(),
                (Some(b), Err(_)) => match b.join(href) {
                    Ok(u) if matches!(u.scheme(), "http" | "https") => u.to_string(),
                    _ => continue,
                },
                _ => continue,
            };
            if seen.insert(absolute.clone()) {
                out.links.push(absolute);
            }
        }
    }

    // Prefer main/article; fall back to body with chrome stripped.
    let main_html = select_main_html(&document);
    out.markdown = html_to_markdown(&main_html);
    // Insert newlines at block boundaries before stripping tags so words
    // from adjacent elements do not glue together.
    let mut text_html = main_html.clone();
    for tag in [
        "p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section",
    ] {
        text_html = text_html.replace(&format!("</{tag}>"), &format!("</{tag}>\n"));
        text_html = text_html.replace(&format!("</{}>", tag.to_ascii_uppercase()), &format!("</{tag}>\n"));
    }
    text_html = text_html.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n");
    out.text = clean_text(&strip_tags(&text_html));
    if out.text.is_empty() {
        out.text = out.markdown.clone();
    }
    if out.title.is_empty() {
        if let Some(Value::String(t)) = out.meta.get("og:title") {
            out.title = t.clone();
        }
    }
    out
}

fn select_main_html(document: &Html) -> String {
    for css in ["article", "main", "[role=main]", ".content", "#content"] {
        if let Ok(sel) = Selector::parse(css) {
            if let Some(node) = document.select(&sel).next() {
                return node.html();
            }
        }
    }
    if let Ok(sel) = Selector::parse("body") {
        if let Some(body) = document.select(&sel).next() {
            // Drop obvious chrome.
            let mut clone = body.html();
            for noise in [
                "script", "style", "noscript", "nav", "footer", "header", "aside", "form",
            ] {
                clone = strip_tag_blocks(&clone, noise);
            }
            return clone;
        }
    }
    document.html()
}

fn strip_tag_blocks(html: &str, tag: &str) -> String {
    let lower = html.to_ascii_lowercase();
    let open = format!("<{tag}");
    let close = format!("</{tag}>");
    let mut out = String::with_capacity(html.len());
    let mut i = 0;
    let bytes = html.as_bytes();
    let lower_bytes = lower.as_bytes();
    while i < bytes.len() {
        if let Some(rel) = find_subslice(&lower_bytes[i..], open.as_bytes()) {
            let start = i + rel;
            out.push_str(&html[i..start]);
            if let Some(end_rel) = find_subslice(&lower_bytes[start..], close.as_bytes()) {
                i = start + end_rel + close.len();
            } else {
                break;
            }
        } else {
            out.push_str(&html[i..]);
            break;
        }
    }
    out
}

fn find_subslice(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

fn strip_tags(html: &str) -> String {
    let mut out = String::with_capacity(html.len());
    let mut in_tag = false;
    for ch in html.chars() {
        match ch {
            '<' => in_tag = true,
            '>' => in_tag = false,
            _ if !in_tag => out.push(ch),
            _ => {}
        }
    }
    html_escape_decode(&out)
}

fn html_escape_decode(s: &str) -> String {
    s.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
}

fn html_to_markdown(html: &str) -> String {
    // Lightweight structural conversion - good enough for RAG / export.
    let mut md = html.to_string();
    for (tag, prefix) in [
        ("h1", "# "),
        ("h2", "## "),
        ("h3", "### "),
        ("h4", "#### "),
        ("h5", "##### "),
        ("h6", "###### "),
    ] {
        md = replace_block_tag(&md, tag, prefix, "\n\n");
    }
    md = replace_block_tag(&md, "p", "", "\n\n");
    md = replace_block_tag(&md, "li", "- ", "\n");
    md = replace_block_tag(&md, "br", "", "\n");
    md = strip_tag_blocks(&md, "script");
    md = strip_tag_blocks(&md, "style");
    clean_text(&strip_tags(&md))
}

fn replace_block_tag(html: &str, tag: &str, prefix: &str, suffix: &str) -> String {
    let open = format!("<{tag}");
    let close = format!("</{tag}>");
    let lower = html.to_ascii_lowercase();
    let mut out = String::with_capacity(html.len());
    let mut i = 0;
    let bytes = html.as_bytes();
    let lower_bytes = lower.as_bytes();
    while i < bytes.len() {
        if let Some(rel) = find_subslice(&lower_bytes[i..], open.as_bytes()) {
            let start = i + rel;
            out.push_str(&html[i..start]);
            // skip to end of opening tag
            let after_open = match find_subslice(&bytes[start..], b">") {
                Some(n) => start + n + 1,
                None => break,
            };
            if let Some(end_rel) = find_subslice(&lower_bytes[after_open..], close.as_bytes()) {
                let inner = &html[after_open..after_open + end_rel];
                out.push_str(prefix);
                out.push_str(inner.trim());
                out.push_str(suffix);
                i = after_open + end_rel + close.len();
            } else {
                out.push_str(&html[start..]);
                break;
            }
        } else {
            out.push_str(&html[i..]);
            break;
        }
    }
    out
}

fn clean_text(text: &str) -> String {
    let decoded = html_escape_decode(text);
    let mut lines: Vec<String> = Vec::new();
    for line in decoded.lines() {
        let collapsed: String = line.split_whitespace().collect::<Vec<_>>().join(" ");
        if !collapsed.is_empty() {
            lines.push(collapsed);
        } else if lines.last().map(|l| !l.is_empty()).unwrap_or(false) {
            lines.push(String::new());
        }
    }
    while lines.last().map(|l| l.is_empty()).unwrap_or(false) {
        lines.pop();
    }
    lines.join("\n").trim().to_string()
}

fn host_of(url: &str) -> Option<String> {
    Url::parse(url)
        .ok()
        .and_then(|u| u.host_str().map(|h| h.to_ascii_lowercase()))
}

/// Fetch many URLs in parallel. `urls` is a JSON array of strings.
/// `options_json` is optional FetchOptions.
#[pyfunction]
pub fn scrape_fetch(urls_json: &str, options_json: Option<&str>) -> PyResult<String> {
    let urls: Vec<String> = serde_json::from_str(urls_json)
        .map_err(|e| PyValueError::new_err(format!("urls must be a JSON array: {e}")))?;
    if urls.is_empty() {
        return Err(PyValueError::new_err("urls must not be empty"));
    }
    if urls.len() > HARD_MAX_PAGES {
        return Err(PyValueError::new_err(format!(
            "too many urls (max {HARD_MAX_PAGES})"
        )));
    }
    let opts = parse_options(options_json)?;
    let client = build_client(&opts).map_err(PyValueError::new_err)?;
    let max_bytes = opts.max_bytes.max(1024);
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(opts.concurrency.max(1).min(HARD_MAX_CONCURRENCY))
        .build()
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    let pages: Vec<PageRecord> = pool.install(|| {
        urls.par_iter()
            .map(|u| fetch_one(&client, u, max_bytes))
            .collect()
    });
    Ok(serde_json::to_string(&json!({ "pages": pages })).unwrap())
}

/// Extract structured content from raw HTML.
#[pyfunction]
pub fn scrape_extract(html: &str, base_url: &str) -> PyResult<String> {
    let extracted = extract_html(html, base_url);
    Ok(serde_json::to_string(&json!({
        "title": extracted.title,
        "text": extracted.text,
        "markdown": extracted.markdown,
        "links": extracted.links,
        "meta": extracted.meta,
    }))
    .unwrap())
}

/// Normalize whitespace / HTML entities in text.
#[pyfunction]
pub fn scrape_clean(text: &str) -> PyResult<String> {
    Ok(clean_text(text))
}

/// Internal legacy crawler. Python owns the public crawl contract.
#[allow(dead_code)]
fn scrape_crawl(seeds_json: &str, options_json: Option<&str>) -> PyResult<String> {
    let seeds: Vec<String> = serde_json::from_str(seeds_json)
        .map_err(|e| PyValueError::new_err(format!("seeds must be a JSON array: {e}")))?;
    if seeds.is_empty() {
        return Err(PyValueError::new_err("seeds must not be empty"));
    }
    let opts = parse_crawl_options(options_json)?;
    let max_pages = opts.max_pages.max(1).min(HARD_MAX_PAGES);
    let max_depth = opts.max_depth.min(5);
    let client = build_client(&opts.fetch).map_err(PyValueError::new_err)?;
    let max_bytes = opts.fetch.max_bytes.max(1024);

    let seed_hosts: HashSet<String> = seeds.iter().filter_map(|u| host_of(u)).collect();
    let mut seen: HashSet<String> = HashSet::new();
    let mut queue: VecDeque<(String, usize)> = VecDeque::new();
    for s in &seeds {
        if seen.insert(s.clone()) {
            queue.push_back((s.clone(), 0));
        }
    }

    let mut pages: Vec<PageRecord> = Vec::new();
    while let Some((url, depth)) = queue.pop_front() {
        if pages.len() >= max_pages {
            break;
        }
        let page = fetch_one(&client, &url, max_bytes);
        if depth < max_depth && page.error.is_none() {
            for link in &page.links {
                if seen.len() >= max_pages * 4 {
                    break;
                }
                if opts.same_domain {
                    match host_of(link) {
                        Some(h) if seed_hosts.contains(&h) => {}
                        _ => continue,
                    }
                }
                if seen.insert(link.clone()) {
                    queue.push_back((link.clone(), depth + 1));
                }
            }
        }
        pages.push(page);
    }

    Ok(serde_json::to_string(&json!({
        "pages": pages,
        "queued": queue.len(),
        "seen": seen.len(),
    }))
    .unwrap())
}

/// Export page records (JSON array) to csv / json / jsonl / xml / xlsx / md / report.
#[pyfunction]
pub fn scrape_export(records_json: &str, format: &str, path: &str) -> PyResult<String> {
    let records: Value = serde_json::from_str(records_json)
        .map_err(|e| PyValueError::new_err(format!("records must be JSON: {e}")))?;
    let rows = match &records {
        Value::Array(a) => a.clone(),
        Value::Object(o) => o
            .get("pages")
            .and_then(|p| p.as_array())
            .cloned()
            .unwrap_or_else(|| vec![records.clone()]),
        other => vec![other.clone()],
    };
    let fmt = format.trim().to_ascii_lowercase();
    let out_path = Path::new(path);
    if let Some(parent) = out_path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent).map_err(|e| PyValueError::new_err(e.to_string()))?;
        }
    }

    match fmt.as_str() {
        "json" => {
            let pretty = serde_json::to_string_pretty(&rows)
                .map_err(|e| PyValueError::new_err(e.to_string()))?;
            fs::write(out_path, pretty).map_err(|e| PyValueError::new_err(e.to_string()))?;
        }
        "jsonl" => {
            let mut f =
                fs::File::create(out_path).map_err(|e| PyValueError::new_err(e.to_string()))?;
            for row in &rows {
                writeln!(f, "{}", serde_json::to_string(row).unwrap())
                    .map_err(|e| PyValueError::new_err(e.to_string()))?;
            }
        }
        "csv" => write_csv(out_path, &rows)?,
        "xml" => write_xml(out_path, &rows)?,
        "xlsx" | "excel" => write_xlsx(out_path, &rows)?,
        "md" | "markdown" => write_markdown(out_path, &rows)?,
        "report" | "html" => write_report(out_path, &rows)?,
        other => {
            return Err(PyValueError::new_err(format!(
                "unsupported format '{other}' (csv, json, jsonl, xml, xlsx, md, report)"
            )));
        }
    }

    Ok(serde_json::to_string(&json!({
        "path": path,
        "format": fmt,
        "count": rows.len(),
    }))
    .unwrap())
}

fn row_fields(row: &Value) -> (String, String, String, String, String) {
    let url = row
        .get("url")
        .or_else(|| row.get("finalUrl"))
        .or_else(|| row.get("final_url"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let title = row
        .get("title")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let status = row
        .get("status")
        .map(|v| v.to_string())
        .unwrap_or_default();
    let text = row
        .get("text")
        .or_else(|| row.get("markdown"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let error = row
        .get("error")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    (url, title, status, text, error)
}

fn write_csv(path: &Path, rows: &[Value]) -> PyResult<()> {
    let mut wtr =
        csv::Writer::from_path(path).map_err(|e| PyValueError::new_err(e.to_string()))?;
    wtr.write_record(["url", "title", "status", "text", "error"])
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    for row in rows {
        let (url, title, status, text, error) = row_fields(row);
        let clipped = if text.len() > 32000 {
            format!("{}…", &text[..32000])
        } else {
            text
        };
        wtr.write_record([&url, &title, &status, &clipped, &error])
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
    }
    wtr.flush()
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(())
}

fn write_xml(path: &Path, rows: &[Value]) -> PyResult<()> {
    let mut out = String::from("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<pages>\n");
    for row in rows {
        let (url, title, status, text, error) = row_fields(row);
        out.push_str("  <page>\n");
        out.push_str(&format!("    <url>{}</url>\n", xml_escape(&url)));
        out.push_str(&format!("    <title>{}</title>\n", xml_escape(&title)));
        out.push_str(&format!("    <status>{}</status>\n", xml_escape(&status)));
        out.push_str(&format!("    <text>{}</text>\n", xml_escape(&text)));
        if !error.is_empty() {
            out.push_str(&format!("    <error>{}</error>\n", xml_escape(&error)));
        }
        out.push_str("  </page>\n");
    }
    out.push_str("</pages>\n");
    fs::write(path, out).map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(())
}

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

fn write_xlsx(path: &Path, rows: &[Value]) -> PyResult<()> {
    let mut workbook = rust_xlsxwriter::Workbook::new();
    let worksheet = workbook.add_worksheet();
    worksheet
        .set_name("pages")
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    for (col, header) in ["url", "title", "status", "text", "error"]
        .iter()
        .enumerate()
    {
        worksheet
            .write_string(0, col as u16, *header)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
    }
    for (i, row) in rows.iter().enumerate() {
        let (url, title, status, text, error) = row_fields(row);
        let r = (i + 1) as u32;
        let clipped = if text.len() > 32000 {
            format!("{}…", &text[..32000])
        } else {
            text
        };
        worksheet
            .write_string(r, 0, &url)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        worksheet
            .write_string(r, 1, &title)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        worksheet
            .write_string(r, 2, &status)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        worksheet
            .write_string(r, 3, &clipped)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        worksheet
            .write_string(r, 4, &error)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
    }
    workbook
        .save(path)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(())
}

fn write_markdown(path: &Path, rows: &[Value]) -> PyResult<()> {
    let mut out = String::from("# Scrape export\n\n");
    for (i, row) in rows.iter().enumerate() {
        let (url, title, status, text, error) = row_fields(row);
        let heading = if title.is_empty() {
            format!("Page {}", i + 1)
        } else {
            title.clone()
        };
        out.push_str(&format!("## {heading}\n\n"));
        out.push_str(&format!("- URL: {url}\n"));
        out.push_str(&format!("- Status: {status}\n"));
        if !error.is_empty() {
            out.push_str(&format!("- Error: {error}\n"));
        }
        out.push('\n');
        if let Some(md) = row.get("markdown").and_then(|v| v.as_str()) {
            out.push_str(md);
        } else {
            out.push_str(&text);
        }
        out.push_str("\n\n---\n\n");
    }
    fs::write(path, out).map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(())
}

fn write_report(path: &Path, rows: &[Value]) -> PyResult<()> {
    let ok = rows
        .iter()
        .filter(|r| r.get("error").and_then(|e| e.as_str()).unwrap_or("").is_empty())
        .count();
    let mut out = String::from(
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\"/><title>Scrape report</title>",
    );
    out.push_str(
        "<style>body{font-family:system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#111}\
         table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:.5rem;text-align:left;vertical-align:top}\
         th{background:#f4f4f4} .err{color:#b00020} .ok{color:#0a7a28}</style></head><body>",
    );
    out.push_str(&format!(
        "<h1>Scrape report</h1><p>{} page(s) - {} ok, {} with errors.</p><table><thead><tr>\
         <th>#</th><th>Title</th><th>URL</th><th>Status</th><th>Notes</th></tr></thead><tbody>",
        rows.len(),
        ok,
        rows.len() - ok
    ));
    for (i, row) in rows.iter().enumerate() {
        let (url, title, status, text, error) = row_fields(row);
        let note = if !error.is_empty() {
            format!("<span class=\"err\">{}</span>", xml_escape(&error))
        } else {
            let preview: String = text.chars().take(160).collect();
            xml_escape(&preview)
        };
        out.push_str(&format!(
            "<tr><td>{}</td><td>{}</td><td><a href=\"{}\">{}</a></td><td class=\"{}\">{}</td><td>{}</td></tr>",
            i + 1,
            xml_escape(&title),
            xml_escape(&url),
            xml_escape(&url),
            if error.is_empty() { "ok" } else { "err" },
            xml_escape(&status),
            note
        ));
    }
    out.push_str("</tbody></table></body></html>");
    fs::write(path, out).map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(())
}
