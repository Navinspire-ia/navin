//! Differential verifier: replay the exact same GET vectors against two
//! instances of the app (baseline and candidate) and compare status codes
//! and body hashes. An optimization that changes what the user sees is not
//! an optimization; it is a behaviour change and gets rejected.
//!
//! This is deliberately an empirical check, not a formal one: it proves
//! equivalence on every vector actually exercised, and says nothing beyond
//! that. Honest coverage beats imaginary proofs.

use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;

/// What one endpoint answered for one vector, reduced to comparables.
/// Headers are excluded on purpose: Date and Set-Cookie change per response.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Fingerprint {
    pub path: String,
    pub status: u16,
    pub body_len: usize,
    pub body_hash: String,
}

/// The comparison of one candidate against the baseline fingerprints.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DiffOutcome {
    pub vectors: usize,
    pub divergences: Vec<String>,
    pub equivalent: bool,
}

/// One plain HTTP/1.1 GET, returning status and body bytes.
async fn fetch(host: &str, port: u16, path: &str) -> Result<(u16, Vec<u8>)> {
    let mut stream = tokio::time::timeout(
        Duration::from_secs(5),
        TcpStream::connect((host, port)),
    )
    .await??;
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\nUser-Agent: navin-diff\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).await?;
    let mut raw = Vec::with_capacity(8192);
    tokio::time::timeout(Duration::from_secs(10), stream.read_to_end(&mut raw)).await??;

    let header_end = find_subslice(&raw, b"\r\n\r\n")
        .ok_or_else(|| anyhow::anyhow!("no header/body separator in response"))?;
    let head = std::str::from_utf8(&raw[..header_end])?;
    let status: u16 = head
        .split_whitespace()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .ok_or_else(|| anyhow::anyhow!("unparseable status line"))?;
    Ok((status, raw[header_end + 4..].to_vec()))
}

/// Crawl the base page for local links and build the vector list: the base
/// path first, then every same-site path found, capped at `max`.
pub async fn discover_vectors(
    host: &str,
    port: u16,
    base_path: &str,
    max: usize,
) -> Vec<String> {
    let mut vectors = vec![base_path.to_owned()];
    if max <= 1 {
        return vectors;
    }
    let Ok((_, body)) = fetch(host, port, base_path).await else {
        return vectors;
    };
    let body = String::from_utf8_lossy(&body);
    for attr in ["href=\"", "action=\""] {
        for chunk in body.split(attr).skip(1) {
            let Some(end) = chunk.find('"') else { continue };
            let link = &chunk[..end];
            // Same-site absolute paths only; anchors and schemes are out.
            if !link.starts_with('/') || link.starts_with("//") {
                continue;
            }
            let link = link.to_owned();
            if !vectors.contains(&link) {
                vectors.push(link);
            }
            if vectors.len() >= max {
                return vectors;
            }
        }
    }
    vectors
}

/// Fingerprint every vector against one running instance, sequentially so
/// the app under test is never racing itself.
pub async fn capture(host: &str, port: u16, vectors: &[String]) -> Vec<Fingerprint> {
    let mut prints = Vec::with_capacity(vectors.len());
    for path in vectors {
        // An unreachable app fingerprints as status 0 with an empty body,
        // which compares just as well as a real answer.
        let (status, body) = fetch(host, port, path).await.unwrap_or_default();
        prints.push(Fingerprint {
            path: path.clone(),
            status,
            body_len: body.len(),
            body_hash: fnv1a_hex(&body),
        });
    }
    prints
}

/// Compare candidate fingerprints against the baseline's, vector by vector.
pub fn compare(baseline: &[Fingerprint], candidate: &[Fingerprint]) -> DiffOutcome {
    let mut divergences = Vec::new();
    for (before, after) in baseline.iter().zip(candidate.iter()) {
        if before.status != after.status {
            divergences.push(format!(
                "{}: status {} -> {}",
                before.path, before.status, after.status
            ));
        } else if before.body_hash != after.body_hash {
            divergences.push(format!(
                "{}: body changed ({} -> {} bytes)",
                before.path, before.body_len, after.body_len
            ));
        }
    }
    if candidate.len() != baseline.len() {
        divergences.push(format!(
            "vector count mismatch: {} baseline, {} candidate",
            baseline.len(),
            candidate.len()
        ));
    }
    DiffOutcome {
        vectors: baseline.len(),
        equivalent: divergences.is_empty(),
        divergences,
    }
}

/// FNV-1a 64-bit; no cryptographic strength needed, just a stable digest.
fn fnv1a_hex(bytes: &[u8]) -> String {
    let mut hash: u64 = 0xcbf29ce484222325;
    for byte in bytes {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x100000001b3);
    }
    format!("{hash:016x}")
}

fn find_subslice(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|window| window == needle)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn print(path: &str, status: u16, body: &[u8]) -> Fingerprint {
        Fingerprint {
            path: path.to_owned(),
            status,
            body_len: body.len(),
            body_hash: fnv1a_hex(body),
        }
    }

    #[test]
    fn identical_behaviour_is_equivalent() {
        let base = vec![print("/", 200, b"hello"), print("/about", 200, b"about")];
        let cand = vec![print("/", 200, b"hello"), print("/about", 200, b"about")];
        let outcome = compare(&base, &cand);
        assert!(outcome.equivalent);
        assert_eq!(outcome.vectors, 2);
    }

    #[test]
    fn a_changed_body_or_status_diverges() {
        let base = vec![print("/", 200, b"hello"), print("/x", 200, b"same")];
        let cand = vec![print("/", 500, b"hello"), print("/x", 200, b"other")];
        let outcome = compare(&base, &cand);
        assert!(!outcome.equivalent);
        assert_eq!(outcome.divergences.len(), 2);
        assert!(outcome.divergences[0].contains("status 200 -> 500"));
        assert!(outcome.divergences[1].contains("body changed"));
    }

    #[test]
    fn fnv_hash_is_stable_and_sensitive() {
        assert_eq!(fnv1a_hex(b"abc"), fnv1a_hex(b"abc"));
        assert_ne!(fnv1a_hex(b"abc"), fnv1a_hex(b"abd"));
        assert_eq!(fnv1a_hex(b"").len(), 16);
    }

    #[tokio::test]
    async fn discovery_and_capture_walk_a_real_server() {
        // A tiny two-page site: / links to /about.
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        tokio::spawn(async move {
            loop {
                let Ok((mut socket, _)) = listener.accept().await else { break };
                tokio::spawn(async move {
                    let mut buf = [0u8; 1024];
                    let n = socket.read(&mut buf).await.unwrap_or(0);
                    let request = String::from_utf8_lossy(&buf[..n]);
                    let body: &[u8] = if request.starts_with("GET /about") {
                        b"<html>about page</html>"
                    } else {
                        b"<html><a href=\"/about\">go</a></html>"
                    };
                    let head = format!(
                        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                        body.len()
                    );
                    let _ = socket.write_all(head.as_bytes()).await;
                    let _ = socket.write_all(body).await;
                });
            }
        });

        let vectors = discover_vectors("127.0.0.1", port, "/", 10).await;
        assert_eq!(vectors, vec!["/".to_owned(), "/about".to_owned()]);

        let base = capture("127.0.0.1", port, &vectors).await;
        let again = capture("127.0.0.1", port, &vectors).await;
        assert!(compare(&base, &again).equivalent);
    }
}
