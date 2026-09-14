//! Port utilities for health checks and load probes.

use anyhow::{Context, Result};
use std::net::TcpListener;

/// A currently free localhost port (the OS picks it).
pub fn free_port() -> Result<u16> {
    let listener = TcpListener::bind("127.0.0.1:0").context("cannot probe for a free port")?;
    Ok(listener.local_addr()?.port())
}

/// Extract host and port from an `http://host:port/path` URL, defaulting
/// to port 80. Only plain http is supported: probes target the shadow on
/// localhost, never a TLS production endpoint.
pub fn parse_http_url(url: &str) -> Result<(String, u16, String)> {
    let rest = url
        .strip_prefix("http://")
        .context("only http:// URLs are supported for local probes")?;
    let (authority, path) = match rest.split_once('/') {
        Some((authority, path)) => (authority, format!("/{path}")),
        None => (rest, "/".to_owned()),
    };
    let (host, port) = match authority.rsplit_once(':') {
        Some((host, port)) => (host.to_owned(), port.parse::<u16>().context("invalid port")?),
        None => (authority.to_owned(), 80),
    };
    anyhow::ensure!(!host.is_empty(), "missing host in {url}");
    Ok((host, port, path))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn urls_are_parsed() {
        let (host, port, path) = parse_http_url("http://127.0.0.1:8080/health").unwrap();
        assert_eq!((host.as_str(), port, path.as_str()), ("127.0.0.1", 8080, "/health"));
        let (host, port, path) = parse_http_url("http://localhost").unwrap();
        assert_eq!((host.as_str(), port, path.as_str()), ("localhost", 80, "/"));
        assert!(parse_http_url("https://example.com").is_err());
    }

    #[test]
    fn free_port_is_returned() {
        let port = free_port().unwrap();
        assert!(port > 0);
    }
}
