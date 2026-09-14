//! Malformed-input fault: send garbage bytes, bad request lines, and an
//! oversized header to the service, then require it to still serve.

use std::time::Duration;
use tokio::io::AsyncWriteExt;
use tokio::net::TcpStream;

use super::super::checks::no_crash;
use super::super::model::FaultOutcome;
use super::super::service::ServiceManager;

async fn send_raw(host: &str, port: u16, payload: &[u8]) {
    if let Ok(Ok(mut stream)) =
        tokio::time::timeout(Duration::from_secs(2), TcpStream::connect((host, port))).await
    {
        let _ = tokio::time::timeout(Duration::from_secs(2), stream.write_all(payload)).await;
        let _ = stream.shutdown().await;
    }
}

pub async fn run(svc: &ServiceManager) -> FaultOutcome {
    let host = svc.host.clone();
    let port = svc.port;

    let payloads: Vec<Vec<u8>> = vec![
        b"\x00\x01\x02 not http at all\r\n\r\n".to_vec(),
        b"GET / HTTP/1.1\r\n".to_vec(), // truncated: no blank line
        b"PWNZ / HTTP/9.9\r\nHost: x\r\n\r\n".to_vec(),
        {
            // Oversized header line.
            let mut buf = b"GET / HTTP/1.1\r\nX-Flood: ".to_vec();
            buf.extend(std::iter::repeat_n(b'A', 64 * 1024));
            buf.extend_from_slice(b"\r\n\r\n");
            buf
        },
    ];

    let sent = payloads.len();
    for payload in &payloads {
        send_raw(&host, port, payload).await;
    }

    // After the abuse, a well-formed request must still succeed.
    let alive = svc.is_healthy().await;

    FaultOutcome::new(
        "malformed",
        "garbage, truncated, bogus-version and oversized requests",
        vec![no_crash(alive)],
    )
    .with_evidence(vec![format!("{sent} malformed requests sent")])
}
