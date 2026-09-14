//! Load fault: hammer the service far above baseline concurrency and
//! check it survives, keeps errors bounded, and respects the RSS ceiling.

use std::time::{Duration, Instant};

use crate::baseline::latency::probe_specs;
use crate::baseline::memory::MemorySampler;

use super::super::checks::{error_rate, no_crash, resource_bound};
use super::super::model::FaultOutcome;
use super::super::service::ServiceManager;

pub async fn run(
    svc: &ServiceManager,
    duration: Duration,
    concurrency: usize,
    max_error_ratio: f64,
    rss_limit_mb: u64,
) -> FaultOutcome {
    let pid = svc.pid();
    let deadline = Instant::now() + duration;

    // Sample RSS while the storm runs.
    let sampler = pid.map(|pid| {
        tokio::spawn(async move {
            let mut memory = MemorySampler::new(pid);
            let mut peak = 0u64;
            while Instant::now() < deadline {
                if let Some(rss) = memory.sample() {
                    peak = peak.max(rss);
                }
                tokio::time::sleep(Duration::from_millis(250)).await;
            }
            peak / (1024 * 1024)
        })
    });

    let stats = probe_specs(&svc.host, svc.port, &svc.specs, duration, concurrency).await;
    let rss_peak_mb = match sampler {
        Some(handle) => handle.await.unwrap_or(0),
        None => 0,
    };

    let alive = svc.is_healthy().await;
    let total = stats.requests + stats.failures;
    let checks = vec![
        no_crash(alive),
        error_rate(stats.failures, total, max_error_ratio),
        resource_bound(rss_peak_mb, rss_limit_mb),
    ];

    FaultOutcome::new(
        "load",
        format!("{concurrency} concurrent clients for {}s", duration.as_secs()),
        checks,
    )
    .with_evidence(vec![format!(
        "{} req, p95 {} ms, p99 {} ms, {} rps",
        stats.requests, stats.p95_ms, stats.p99_ms, stats.rps
    )])
}
