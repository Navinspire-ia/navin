//! Loopback IPC server.
//!
//! One task per connection; requests are dispatched to the daemon handler
//! and events from the [`EventBus`] are pushed to every authenticated client.
//!
//! The transport is a TCP listener on `127.0.0.1:0`. See [`super`] for why
//! that replaced the Unix socket rather than sitting beside it.

use anyhow::{Context, Result};
use serde_json::Value;
use std::path::Path;
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};
use tokio::net::{TcpListener, TcpStream};
use tracing::{debug, warn};

use super::endpoint::{new_token, Endpoint, LOOPBACK};
use super::events::EventBus;
use super::protocol::{Request, Response, RpcErrorCode};
use super::session::{Outcome, Session};

/// What the daemon exposes to the protocol layer.
pub trait Handler: Send + Sync + 'static {
    fn handle(&self, method: &str, params: Value) -> Result<Value, (RpcErrorCode, String)>;
}

pub async fn serve(
    engine_dir: &Path,
    handler: Arc<dyn Handler>,
    events: EventBus,
    shutdown: tokio::sync::watch::Receiver<bool>,
) -> Result<()> {
    std::fs::create_dir_all(engine_dir)
        .with_context(|| format!("cannot create {}", engine_dir.display()))?;
    // Bind before publishing: the endpoint file must never advertise a port
    // nothing is listening on, or a client races into a refused connection.
    let listener = TcpListener::bind((LOOPBACK, 0))
        .await
        .with_context(|| format!("cannot bind {LOOPBACK}:0 for IPC"))?;
    let port = listener.local_addr().context("IPC listener has no address")?.port();
    let token = new_token()?;
    Endpoint::new(port, token.clone()).write(engine_dir)?;
    debug!("IPC listening on {LOOPBACK}:{port}");

    let token: Arc<str> = Arc::from(token);
    let mut shutdown = shutdown;
    loop {
        tokio::select! {
            _ = shutdown.changed() => {
                if *shutdown.borrow() {
                    Endpoint::remove(engine_dir);
                    return Ok(());
                }
            }
            accepted = listener.accept() => {
                let (stream, peer) = accepted.context("IPC accept failed")?;
                // Belt and braces: the kernel already refuses non-loopback
                // peers on this listener, but the daemon can run anything a
                // client asks for, so the check is worth its two lines.
                if !peer.ip().is_loopback() {
                    warn!("refused non-loopback IPC peer {peer}");
                    continue;
                }
                let handler = handler.clone();
                let events = events.clone();
                let token = token.clone();
                tokio::spawn(async move {
                    if let Err(err) = serve_stream(stream, handler, events, token).await {
                        debug!("IPC client ended: {err:#}");
                    }
                });
            }
        }
    }
}

async fn serve_stream(
    stream: TcpStream,
    handler: Arc<dyn Handler>,
    events: EventBus,
    token: Arc<str>,
) -> Result<()> {
    // Requests are small and answers matter more than throughput; Nagle only
    // adds latency to a line-per-message protocol.
    let _ = stream.set_nodelay(true);
    let (read, write) = stream.into_split();
    serve_client(read, write, handler, events, token).await
}

/// The connection loop, over anything that reads and writes bytes.
///
/// Generic so the whole protocol - authentication, dispatch, event fan-out -
/// is exercised in-process over a memory duplex, on every platform, without a
/// listening port.
pub async fn serve_client<R, W>(
    read: R,
    mut write: W,
    handler: Arc<dyn Handler>,
    events: EventBus,
    token: Arc<str>,
) -> Result<()>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin,
{
    let mut lines = BufReader::new(read).lines();
    let mut session = Session::new(token.as_ref());
    let mut rx = events.subscribe();

    loop {
        tokio::select! {
            event = rx.recv() => {
                // An anonymous connection learns nothing about this workspace
                // until it has proved it can read the endpoint file.
                if !session.authenticated() {
                    continue;
                }
                if let Ok(event) = event {
                    write_frame(&mut write, &event).await?;
                }
                // Lagged receivers just skip events; they are advisory.
            }
            line = lines.next_line() => {
                let Some(line) = line? else { return Ok(()) };
                match session.handle_line(&line, handler.as_ref()) {
                    Outcome::Ignore => continue,
                    Outcome::Reply(response) => write_frame(&mut write, &response).await?,
                    Outcome::Reject(response) => {
                        write_frame(&mut write, &response).await?;
                        return Ok(());
                    }
                }
            }
        }
    }
}

async fn write_frame<W, T>(write: &mut W, frame: &T) -> Result<()>
where
    W: AsyncWrite + Unpin,
    T: serde::Serialize,
{
    let mut body = serde_json::to_vec(frame)?;
    body.push(b'\n');
    write.write_all(&body).await?;
    Ok(())
}

/// One-shot client call, used by the CLI (`status`, `inspect --daemon`).
pub async fn call(engine_dir: &Path, method: &str, params: Value) -> Result<Value> {
    let endpoint = Endpoint::read(engine_dir)?;
    let address = format!("{}:{}", endpoint.host, endpoint.port);
    let stream = TcpStream::connect(&address)
        .await
        .with_context(|| format!("daemon not reachable at {address}"))?;
    let _ = stream.set_nodelay(true);
    let (read, mut write) = stream.into_split();
    let request = Request {
        id: 1,
        method: method.to_owned(),
        params,
        token: Some(endpoint.token),
    };
    write_frame(&mut write, &request).await?;

    let mut lines = BufReader::new(read).lines();
    while let Some(line) = lines.next_line().await? {
        // Skip event frames; we only want our response.
        if let Ok(response) = serde_json::from_str::<Response>(&line) {
            if response.id == request.id {
                if let Some(error) = response.error {
                    anyhow::bail!("{:?}: {}", error.code, error.message);
                }
                return Ok(response.result.unwrap_or(Value::Null));
            }
        }
    }
    anyhow::bail!("daemon closed the connection without answering")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use tokio::io::AsyncBufRead;
    use tokio::sync::watch;

    struct Counting(AtomicUsize);

    impl Handler for Counting {
        fn handle(&self, method: &str, _params: Value) -> Result<Value, (RpcErrorCode, String)> {
            self.0.fetch_add(1, Ordering::SeqCst);
            Ok(json!({ "method": method, "calls": self.0.load(Ordering::SeqCst) }))
        }
    }

    fn handler() -> Arc<Counting> {
        Arc::new(Counting(AtomicUsize::new(0)))
    }

    async fn next_frame<R: AsyncBufRead + Unpin>(reader: &mut R) -> Value {
        let mut line = String::new();
        reader.read_line(&mut line).await.unwrap();
        serde_json::from_str(&line).unwrap_or(Value::Null)
    }

    /// Run the connection loop over an in-memory pipe: no port, no platform.
    fn spawn_duplex(
        token: &str,
        handler: Arc<dyn Handler>,
        events: EventBus,
    ) -> tokio::io::DuplexStream {
        let (client, server) = tokio::io::duplex(64 * 1024);
        let (read, write) = tokio::io::split(server);
        let token: Arc<str> = Arc::from(token);
        tokio::spawn(async move {
            let _ = serve_client(read, write, handler, events, token).await;
        });
        client
    }

    #[tokio::test]
    async fn an_authenticated_request_gets_its_answer() {
        let counter = handler();
        let client = spawn_duplex("tok", counter.clone(), EventBus::new());
        let (read, mut write) = tokio::io::split(client);
        let mut reader = BufReader::new(read);

        write
            .write_all(b"{\"id\":1,\"method\":\"engine.status\",\"token\":\"tok\"}\n")
            .await
            .unwrap();
        let frame = next_frame(&mut reader).await;
        assert_eq!(frame["id"], 1);
        assert_eq!(frame["result"]["method"], "engine.status");
        assert_eq!(counter.0.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn a_tokenless_request_is_refused_and_the_handler_never_runs() {
        let counter = handler();
        let client = spawn_duplex("tok", counter.clone(), EventBus::new());
        let (read, mut write) = tokio::io::split(client);
        let mut reader = BufReader::new(read);

        write
            .write_all(b"{\"id\":4,\"method\":\"engine.shutdown\"}\n")
            .await
            .unwrap();
        let frame = next_frame(&mut reader).await;
        assert_eq!(frame["error"]["code"], "unauthorized");
        assert_eq!(counter.0.load(Ordering::SeqCst), 0);

        // The refusal closes the connection: no second guess.
        assert_eq!(next_frame(&mut reader).await, Value::Null);
    }

    #[tokio::test]
    async fn events_reach_an_authenticated_client_only() {
        let events = EventBus::new();
        let anonymous = spawn_duplex("tok", handler(), events.clone());
        let client = spawn_duplex("tok", handler(), events.clone());
        let (read, mut write) = tokio::io::split(client);
        let mut reader = BufReader::new(read);

        write
            .write_all(b"{\"id\":1,\"method\":\"engine.status\",\"token\":\"tok\"}\n")
            .await
            .unwrap();
        assert_eq!(next_frame(&mut reader).await["id"], 1);

        events.publish(super::super::protocol::Event::new("run.started", json!({ "job": 3 })));
        let frame = next_frame(&mut reader).await;
        assert_eq!(frame["kind"], "event");
        assert_eq!(frame["event"], "run.started");

        // The anonymous connection was never told anything; reading it back
        // would block, so assert on what it did get instead: nothing yet.
        let (anon_read, _anon_write) = tokio::io::split(anonymous);
        let mut anon = BufReader::new(anon_read);
        let idle = tokio::time::timeout(
            std::time::Duration::from_millis(150),
            next_frame(&mut anon),
        )
        .await;
        assert!(idle.is_err(), "an unauthenticated client received a frame");
    }

    #[tokio::test]
    async fn serve_publishes_an_endpoint_that_call_can_use() {
        let dir = tempfile::tempdir().unwrap();
        let (tx, rx) = watch::channel(false);
        let engine_dir = dir.path().to_path_buf();
        let counter = handler();
        let server = tokio::spawn({
            let counter = counter.clone();
            async move { serve(&engine_dir, counter, EventBus::new(), rx).await }
        });

        // The endpoint file appears as soon as the listener is bound.
        let path = super::super::endpoint::endpoint_path(dir.path());
        for _ in 0..100 {
            if path.is_file() {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        }
        let endpoint = Endpoint::read(dir.path()).unwrap();
        assert_eq!(endpoint.transport, "tcp");
        assert_eq!(endpoint.host, "127.0.0.1");
        assert!(endpoint.port > 0);

        let result = call(dir.path(), "engine.status", json!({})).await.unwrap();
        assert_eq!(result["method"], "engine.status");

        tx.send(true).unwrap();
        let _ = tokio::time::timeout(std::time::Duration::from_secs(2), server).await;
        assert!(!path.exists(), "shutdown must retract the endpoint file");
    }

    #[tokio::test]
    async fn a_client_with_the_wrong_token_is_turned_away_by_the_real_listener() {
        let dir = tempfile::tempdir().unwrap();
        let (tx, rx) = watch::channel(false);
        let engine_dir = dir.path().to_path_buf();
        let counter = handler();
        let server = tokio::spawn({
            let counter = counter.clone();
            async move { serve(&engine_dir, counter, EventBus::new(), rx).await }
        });
        for _ in 0..100 {
            if super::super::endpoint::endpoint_path(dir.path()).is_file() {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        }
        let endpoint = Endpoint::read(dir.path()).unwrap();

        let stream = TcpStream::connect((endpoint.host.as_str(), endpoint.port))
            .await
            .unwrap();
        let (read, mut write) = stream.into_split();
        write
            .write_all(b"{\"id\":1,\"method\":\"engine.status\",\"token\":\"guessed\"}\n")
            .await
            .unwrap();
        let mut reader = BufReader::new(read);
        let frame = next_frame(&mut reader).await;
        assert_eq!(frame["error"]["code"], "unauthorized");
        assert_eq!(counter.0.load(Ordering::SeqCst), 0);

        tx.send(true).unwrap();
        let _ = tokio::time::timeout(std::time::Duration::from_secs(2), server).await;
    }

    #[tokio::test]
    async fn calling_without_a_daemon_says_so_instead_of_hanging() {
        let dir = tempfile::tempdir().unwrap();
        let error = call(dir.path(), "engine.status", json!({}))
            .await
            .unwrap_err()
            .to_string();
        assert!(error.contains("no daemon endpoint"), "{error}");
    }

    #[tokio::test]
    async fn a_stale_endpoint_file_reads_as_unreachable_not_as_a_live_daemon() {
        let dir = tempfile::tempdir().unwrap();
        // Bind then drop, so the port is almost certainly free again.
        let port = {
            let probe = TcpListener::bind((LOOPBACK, 0)).await.unwrap();
            probe.local_addr().unwrap().port()
        };
        Endpoint::new(port, "stale".to_owned()).write(dir.path()).unwrap();
        let error = call(dir.path(), "engine.status", json!({}))
            .await
            .unwrap_err()
            .to_string();
        assert!(error.contains("not reachable"), "{error}");
    }
}
