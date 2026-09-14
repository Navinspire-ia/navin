//! Connection-flood fault: open many half-open connections at once
//! (Slowloris-style) and require the service to keep serving afterwards.

use std::time::Duration;
use tokio::io::AsyncWriteExt;
use tokio::net::TcpStream;

use super::super::checks::no_crash;
use super::super::model::{CheckResult, FaultOutcome, Verdict};
use super::super::service::ServiceManager;

pub async fn run(svc: &ServiceManager, connections: usize, hold: Duration) -> FaultOutcome {
    let host = svc.host.clone();
    let port = svc.port;

    // Open connections, send a partial request, and hold them open.
    let mut held = Vec::new();
    for _ in 0..connections {
        match tokio::time::timeout(Duration::from_millis(500), TcpStream::connect((host.as_str(), port)))
            .await
        {
            Ok(Ok(mut stream)) => {
                let _ = stream.write_all(b"GET / HTTP/1.1\r\n").await;
                held.push(stream);
            }
            _ => break,
        }
    }
    let opened = held.len();

    // Meanwhile a normal client should still get through (degraded is ok).
    let reachable_during = svc.is_healthy().await;

    tokio::time::sleep(hold).await;
    drop(held); // release the flood

    // Give the server a beat to reclaim sockets, then confirm recovery.
    tokio::time::sleep(Duration::from_millis(300)).await;
    let alive_after = svc.is_healthy().await;

    let during = if reachable_during {
        CheckResult::new("reachable_under_flood", Verdict::Pass, "served a client during the flood")
    } else {
        // Being unreachable during a flood is a soft warning, not a crash,
        // as long as it recovers afterwards.
        CheckResult::new("reachable_under_flood", Verdict::Weak, "unreachable while flooded")
    };

    FaultOutcome::new(
        "connection_flood",
        format!("{opened} half-open connections held for {}s", hold.as_secs()),
        vec![during, no_crash(alive_after)],
    )
    .with_evidence(vec![format!("{opened}/{connections} connections opened")])
}
