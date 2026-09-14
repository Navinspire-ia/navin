//! Network chaos fault: a TCP proxy sits between the prober and the
//! service, adding latency to every connection and hard-resetting every
//! Nth one. The service must keep answering through the chaos and must be
//! perfectly clean again the moment the proxy goes away. Nothing here
//! touches the service itself: the chaos lives entirely in the wire.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use tokio::io::copy_bidirectional;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::watch;

use crate::baseline::latency::probe_specs;

use super::super::checks::{error_rate, no_crash};
use super::super::model::{CheckResult, FaultOutcome, Verdict};
use super::super::service::ServiceManager;

/// How the wire misbehaves while the fault runs.
#[derive(Debug, Clone, Copy)]
pub struct ChaosConfig {
    /// Extra latency added to every proxied connection.
    pub delay: Duration,
    /// Every Nth connection is dropped without a byte of reply (0 = never).
    pub reset_every: u64,
}

/// Counters shared with the proxy loop so the outcome can report honestly.
#[derive(Default)]
struct ChaosCounters {
    connections: AtomicU64,
    resets: AtomicU64,
}

pub async fn run(
    svc: &ServiceManager,
    config: ChaosConfig,
    duration: Duration,
    concurrency: usize,
    max_error_ratio: f64,
) -> FaultOutcome {
    let description = format!(
        "+{}ms latency, 1/{} connections reset, {}s under load",
        config.delay.as_millis(),
        config.reset_every.max(1),
        duration.as_secs()
    );

    // Stand the proxy up on an ephemeral local port.
    let (proxy_port, counters, stop) = match start_proxy(&svc.host, svc.port, config).await {
        Ok(started) => started,
        Err(err) => {
            return FaultOutcome::new(
                "network_chaos",
                description,
                vec![CheckResult::new(
                    "proxy_started",
                    Verdict::Fail,
                    format!("chaos proxy could not start: {err:#}"),
                )],
            );
        }
    };

    // Drive the whole benchmark through the degraded wire.
    let chaos_stats = probe_specs(&svc.host, proxy_port, &svc.specs, duration, concurrency).await;
    drop(stop); // Closing the channel stops the accept loop.

    // The moment the chaos is gone, the service must answer cleanly again.
    let alive = svc.is_healthy().await;
    let direct =
        probe_specs(&svc.host, svc.port, &svc.specs, Duration::from_secs(2), concurrency.min(8))
            .await;

    let served = CheckResult::new(
        "served_under_chaos",
        if chaos_stats.requests > 0 { Verdict::Pass } else { Verdict::Fail },
        format!(
            "{} requests completed through the degraded wire ({} failed)",
            chaos_stats.requests, chaos_stats.failures
        ),
    );
    let clean_after = error_rate(direct.failures, direct.requests + direct.failures, max_error_ratio);

    FaultOutcome::new("network_chaos", description, vec![no_crash(alive), served, clean_after])
        .with_evidence(vec![
            format!(
                "proxied {} connections, injected {} resets",
                counters.connections.load(Ordering::Relaxed),
                counters.resets.load(Ordering::Relaxed)
            ),
            format!(
                "under chaos: {} req, p95 {} ms; after chaos: p95 {} ms, {} rps",
                chaos_stats.requests, chaos_stats.p95_ms, direct.p95_ms, direct.rps
            ),
        ])
}

/// Bind the proxy and spawn its accept loop. Returns the listening port,
/// the shared counters, and a guard whose drop stops the loop.
async fn start_proxy(
    host: &str,
    upstream_port: u16,
    config: ChaosConfig,
) -> anyhow::Result<(u16, Arc<ChaosCounters>, watch::Sender<bool>)> {
    let listener = TcpListener::bind((host, 0)).await?;
    let port = listener.local_addr()?.port();
    let counters = Arc::new(ChaosCounters::default());
    let (stop_tx, mut stop_rx) = watch::channel(false);

    let host = host.to_owned();
    let loop_counters = Arc::clone(&counters);
    tokio::spawn(async move {
        loop {
            let accepted = tokio::select! {
                accepted = listener.accept() => accepted,
                _ = stop_rx.changed() => break,
            };
            let Ok((downstream, _)) = accepted else { break };
            let n = loop_counters.connections.fetch_add(1, Ordering::Relaxed) + 1;

            // Deterministic reset schedule: reproducible chaos beats dice.
            if config.reset_every > 0 && n % config.reset_every == 0 {
                loop_counters.resets.fetch_add(1, Ordering::Relaxed);
                drop(downstream);
                continue;
            }

            let host = host.clone();
            tokio::spawn(async move {
                tokio::time::sleep(config.delay).await;
                let mut downstream = downstream;
                if let Ok(mut upstream) = TcpStream::connect((host.as_str(), upstream_port)).await
                {
                    let _ = copy_bidirectional(&mut downstream, &mut upstream).await;
                }
            });
        }
    });

    Ok((port, counters, stop_tx))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    /// A raw hello server plus the chaos proxy: requests through the proxy
    /// must succeed (slower) and the reset schedule must actually drop 1/N.
    #[tokio::test]
    async fn proxy_adds_latency_and_resets_every_nth_connection() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let upstream_port = listener.local_addr().unwrap().port();
        tokio::spawn(async move {
            loop {
                let Ok((mut socket, _)) = listener.accept().await else { break };
                tokio::spawn(async move {
                    let mut buf = [0u8; 512];
                    let _ = socket.read(&mut buf).await;
                    let _ = socket
                        .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
                        .await;
                });
            }
        });

        let config = ChaosConfig { delay: Duration::from_millis(30), reset_every: 3 };
        let (proxy_port, counters, _stop) =
            start_proxy("127.0.0.1", upstream_port, config).await.unwrap();

        let mut ok = 0u32;
        let mut dropped = 0u32;
        for _ in 0..9 {
            match crate::baseline::latency::get_once("127.0.0.1", proxy_port, "/").await {
                Ok(ms) => {
                    ok += 1;
                    assert!(ms >= 30.0, "proxied request must carry the injected delay");
                }
                Err(_) => dropped += 1,
            }
        }
        assert_eq!(ok, 6, "two thirds of connections should pass");
        assert_eq!(dropped, 3, "every 3rd connection must be reset");
        assert_eq!(counters.resets.load(Ordering::Relaxed), 3);
    }
}
