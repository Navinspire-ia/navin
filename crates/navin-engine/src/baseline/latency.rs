//! Minimal HTTP/1.1 latency probe for localhost shadows (plain http only,
//! no TLS: probes must never target a production endpoint).
//!
//! A probe is described by a [`ProbeSpec`]: method, path, headers and body.
//! Several specs probe several routes in rotation, so authenticated APIs
//! and POST endpoints are as measurable as a bare GET on `/`.

use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::time::{Duration, Instant};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;

use crate::policy::config::TargetSection;

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct LatencyStats {
    pub requests: u64,
    pub failures: u64,
    pub p50_ms: f64,
    pub p95_ms: f64,
    pub p99_ms: f64,
    pub rps: f64,
}

/// One request the prober knows how to send.
#[derive(Debug, Clone)]
pub struct ProbeSpec {
    pub method: String,
    pub path: String,
    /// Extra headers; Host, Connection and Content-Length are always
    /// managed by the prober and filtered out of this list.
    pub headers: Vec<(String, String)>,
    pub body: String,
}

impl ProbeSpec {
    /// A plain GET, the historical default.
    pub fn get(path: &str) -> Self {
        ProbeSpec {
            method: "GET".to_owned(),
            path: path.to_owned(),
            headers: Vec::new(),
            body: String::new(),
        }
    }

    /// Raw HTTP/1.1 request bytes for this spec.
    fn render(&self, host: &str) -> Vec<u8> {
        let method = if self.method.trim().is_empty() {
            "GET"
        } else {
            self.method.trim()
        };
        let mut request = format!(
            "{method} {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\nUser-Agent: navin-engine\r\n",
            path = if self.path.is_empty() { "/" } else { &self.path },
        );
        for (name, value) in &self.headers {
            let lowered = name.to_ascii_lowercase();
            if matches!(lowered.as_str(), "host" | "connection" | "content-length") {
                continue;
            }
            request.push_str(&format!("{name}: {value}\r\n"));
        }
        if !self.body.is_empty() || method != "GET" {
            request.push_str(&format!("Content-Length: {}\r\n", self.body.len()));
        }
        request.push_str("\r\n");
        let mut bytes = request.into_bytes();
        bytes.extend_from_slice(self.body.as_bytes());
        bytes
    }
}

/// The probe set for a target: the discovered URL path first, then every
/// extra path from `[target]`, all carrying the configured method, headers
/// and body.
pub fn specs_for(target: &TargetSection, base_path: &str) -> Vec<ProbeSpec> {
    let headers: Vec<(String, String)> = target
        .probe_headers
        .iter()
        .map(|(name, value)| (name.clone(), value.clone()))
        .collect();
    let mut paths: Vec<String> = vec![if base_path.is_empty() { "/".to_owned() } else { base_path.to_owned() }];
    for extra in &target.probe_paths {
        let extra = extra.trim();
        if extra.is_empty() {
            continue;
        }
        let normalized = if extra.starts_with('/') { extra.to_owned() } else { format!("/{extra}") };
        if !paths.contains(&normalized) {
            paths.push(normalized);
        }
    }
    paths
        .into_iter()
        .map(|path| ProbeSpec {
            method: target.probe_method.clone(),
            path,
            headers: headers.clone(),
            body: target.probe_body.clone(),
        })
        .collect()
}

/// Index into a sorted sample set for quantile `q` (nearest-rank).
pub fn percentile(sorted_ms: &[f64], q: f64) -> f64 {
    if sorted_ms.is_empty() {
        return 0.0;
    }
    let rank = (q * sorted_ms.len() as f64).ceil() as usize;
    sorted_ms[rank.clamp(1, sorted_ms.len()) - 1]
}

/// One HTTP GET, returning the round-trip in milliseconds. Public so the
/// Proof engine can reuse it as a health probe.
pub async fn get_once(host: &str, port: u16, path: &str) -> Result<f64> {
    one_request(host, port, &ProbeSpec::get(path)).await
}

/// One request described by a spec (method, headers, body included).
pub async fn request_once(host: &str, port: u16, spec: &ProbeSpec) -> Result<f64> {
    one_request(host, port, spec).await
}

async fn one_request(host: &str, port: u16, spec: &ProbeSpec) -> Result<f64> {
    let started = Instant::now();
    let mut stream = TcpStream::connect((host, port)).await?;
    stream.write_all(&spec.render(host)).await?;
    let mut buf = Vec::with_capacity(4096);
    stream.read_to_end(&mut buf).await?;
    anyhow::ensure!(buf.starts_with(b"HTTP/1."), "not an HTTP response");
    Ok(started.elapsed().as_secs_f64() * 1000.0)
}

/// Sequentially probe one GET path for `duration` with `concurrency`
/// parallel loops. Thin wrapper kept for single-route callers.
pub async fn probe(
    host: &str,
    port: u16,
    path: &str,
    duration: Duration,
    concurrency: usize,
) -> LatencyStats {
    probe_specs(host, port, &[ProbeSpec::get(path)], duration, concurrency).await
}

/// Probe a set of request specs in rotation for `duration` with
/// `concurrency` parallel loops.
pub async fn probe_specs(
    host: &str,
    port: u16,
    specs: &[ProbeSpec],
    duration: Duration,
    concurrency: usize,
) -> LatencyStats {
    let specs: Vec<ProbeSpec> = if specs.is_empty() {
        vec![ProbeSpec::get("/")]
    } else {
        specs.to_vec()
    };
    let deadline = Instant::now() + duration;
    let mut tasks = Vec::new();
    for worker in 0..concurrency.max(1) {
        let host = host.to_owned();
        let specs = specs.clone();
        tasks.push(tokio::spawn(async move {
            let mut samples = Vec::new();
            let mut failures = 0u64;
            // Stagger the starting spec per worker so every route is under
            // load at the same time, not in synchronized waves.
            let mut index = worker % specs.len();
            while Instant::now() < deadline {
                let spec = &specs[index];
                index = (index + 1) % specs.len();
                match tokio::time::timeout(
                    Duration::from_secs(10),
                    one_request(&host, port, spec),
                )
                .await
                {
                    Ok(Ok(ms)) => samples.push(ms),
                    _ => failures += 1,
                }
            }
            (samples, failures)
        }));
    }

    let mut all = Vec::new();
    let mut failures = 0u64;
    for task in tasks {
        if let Ok((samples, failed)) = task.await {
            all.extend(samples);
            failures += failed;
        }
    }
    all.sort_by(|a, b| a.partial_cmp(b).expect("latency is finite"));
    let requests = all.len() as u64;
    LatencyStats {
        requests,
        failures,
        p50_ms: round1(percentile(&all, 0.50)),
        p95_ms: round1(percentile(&all, 0.95)),
        p99_ms: round1(percentile(&all, 0.99)),
        rps: round1(requests as f64 / duration.as_secs_f64().max(0.001)),
    }
}

fn round1(v: f64) -> f64 {
    (v * 10.0).round() / 10.0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percentiles_use_nearest_rank() {
        let samples: Vec<f64> = (1..=100).map(|n| n as f64).collect();
        assert_eq!(percentile(&samples, 0.50), 50.0);
        assert_eq!(percentile(&samples, 0.95), 95.0);
        assert_eq!(percentile(&samples, 0.99), 99.0);
        assert_eq!(percentile(&[], 0.5), 0.0);
        assert_eq!(percentile(&[7.0], 0.99), 7.0);
    }

    #[tokio::test]
    async fn probe_measures_a_tiny_http_server() {
        // One-line HTTP server: accept, answer 200, close.
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        tokio::spawn(async move {
            loop {
                let Ok((mut socket, _)) = listener.accept().await else { break };
                tokio::spawn(async move {
                    let mut buf = [0u8; 1024];
                    let _ = socket.read(&mut buf).await;
                    let _ = socket
                        .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
                        .await;
                });
            }
        });

        let stats = probe("127.0.0.1", port, "/", Duration::from_millis(600), 2).await;
        assert!(stats.requests > 0, "no successful requests");
        assert!(stats.p50_ms > 0.0);
        assert!(stats.p95_ms >= stats.p50_ms);
    }

    #[test]
    fn a_spec_renders_method_headers_and_body() {
        let spec = ProbeSpec {
            method: "POST".to_owned(),
            path: "/api/items".to_owned(),
            headers: vec![
                ("Authorization".to_owned(), "Bearer tok".to_owned()),
                // Managed headers must not be overridable by config.
                ("Connection".to_owned(), "keep-alive".to_owned()),
                ("Content-Length".to_owned(), "9999".to_owned()),
            ],
            body: "{\"a\":1}".to_owned(),
        };
        let rendered = String::from_utf8(spec.render("127.0.0.1")).unwrap();
        assert!(rendered.starts_with("POST /api/items HTTP/1.1\r\n"));
        assert!(rendered.contains("Authorization: Bearer tok\r\n"));
        assert!(rendered.contains("Connection: close\r\n"));
        assert!(!rendered.contains("keep-alive"));
        assert_eq!(rendered.matches("Content-Length:").count(), 1);
        assert!(rendered.contains("Content-Length: 7\r\n"));
        assert!(rendered.ends_with("\r\n\r\n{\"a\":1}"));
    }

    #[test]
    fn specs_for_prepends_the_base_path_and_normalizes_extras() {
        let mut target = TargetSection {
            probe_paths: vec!["health".to_owned(), "/api/items".to_owned(), "/".to_owned()],
            ..TargetSection::default()
        };
        target.probe_headers.insert("X-Token".to_owned(), "t".to_owned());
        let specs = specs_for(&target, "/");
        let paths: Vec<&str> = specs.iter().map(|s| s.path.as_str()).collect();
        assert_eq!(paths, vec!["/", "/health", "/api/items"]);
        assert!(specs.iter().all(|s| s.headers == vec![("X-Token".to_owned(), "t".to_owned())]));
    }

    #[tokio::test]
    async fn probe_specs_rotates_over_every_route() {
        use std::collections::HashSet;
        use std::sync::{Arc, Mutex};

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let seen: Arc<Mutex<HashSet<String>>> = Arc::default();
        let record = Arc::clone(&seen);
        tokio::spawn(async move {
            loop {
                let Ok((mut socket, _)) = listener.accept().await else { break };
                let record = Arc::clone(&record);
                tokio::spawn(async move {
                    let mut buf = [0u8; 1024];
                    let n = socket.read(&mut buf).await.unwrap_or(0);
                    let text = String::from_utf8_lossy(&buf[..n]).to_string();
                    if let Some(line) = text.lines().next() {
                        record.lock().unwrap().insert(line.to_owned());
                    }
                    let _ = socket
                        .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
                        .await;
                });
            }
        });

        let specs = vec![ProbeSpec::get("/"), ProbeSpec::get("/health")];
        let stats = probe_specs("127.0.0.1", port, &specs, Duration::from_millis(600), 2).await;
        assert!(stats.requests > 1);
        let seen = seen.lock().unwrap();
        assert!(seen.contains("GET / HTTP/1.1"), "{seen:?}");
        assert!(seen.contains("GET /health HTTP/1.1"), "{seen:?}");
    }
}
