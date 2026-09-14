//! Readiness checks: a service is "up" when its TCP port accepts.

use std::time::{Duration, Instant};
use tokio::net::TcpStream;

const POLL_INTERVAL: Duration = Duration::from_millis(100);

pub async fn port_open(host: &str, port: u16) -> bool {
    tokio::time::timeout(
        Duration::from_millis(500),
        TcpStream::connect((host, port)),
    )
    .await
    .map(|res| res.is_ok())
    .unwrap_or(false)
}

/// Wait until the port accepts; returns the time it took.
pub async fn wait_for_port(host: &str, port: u16, timeout: Duration) -> Option<Duration> {
    let started = Instant::now();
    while started.elapsed() < timeout {
        if port_open(host, port).await {
            return Some(started.elapsed());
        }
        tokio::time::sleep(POLL_INTERVAL).await;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn detects_a_listening_port() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let waited = wait_for_port("127.0.0.1", port, Duration::from_secs(2)).await;
        assert!(waited.is_some());
    }

    #[tokio::test]
    async fn times_out_on_a_dead_port() {
        // A port we bound then dropped is very likely closed.
        let port = crate::runner::ports::free_port().unwrap();
        let waited = wait_for_port("127.0.0.1", port, Duration::from_millis(300)).await;
        assert!(waited.is_none());
    }
}
