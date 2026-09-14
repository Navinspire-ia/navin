//! Daemon lifecycle: wire storage, scheduler, worker and IPC together,
//! run until a shutdown signal, then clean up (endpoint file retracted,
//! jobs drained, SQLite closed).

use anyhow::Result;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;
use tokio::sync::watch;
use tracing::info;

use crate::ipc::events::EventBus;
use crate::ipc::protocol::{RpcErrorCode, PROTOCOL_VERSION};
use crate::ipc::server::{self, Handler};
use crate::policy::config::EvolveConfig;
use crate::project::inspect_project;
use crate::storage::db::Database;
use crate::ENGINE_VERSION;

use super::resource_guard::ResourceGuard;
use super::scheduler::Scheduler;
use super::watcher::run_watcher;
use super::worker::run_worker;

struct DaemonHandler {
    root: PathBuf,
    scheduler: Scheduler,
    guard: ResourceGuard,
    started: Instant,
    /// Lets `engine.shutdown` stop the daemon remotely (dashboard button).
    shutdown: watch::Sender<bool>,
}

impl Handler for DaemonHandler {
    fn handle(&self, method: &str, params: Value) -> Result<Value, (RpcErrorCode, String)> {
        match method {
            "engine.status" => Ok(json!({
                "engine": ENGINE_VERSION,
                "protocol": PROTOCOL_VERSION,
                "root": self.root,
                "uptime_secs": self.started.elapsed().as_secs(),
                "jobs": self.scheduler.snapshot(),
                "resources": self.guard,
            })),
            "project.inspect" => {
                let root = params
                    .get("path")
                    .and_then(Value::as_str)
                    .map(PathBuf::from)
                    .unwrap_or_else(|| self.root.clone());
                inspect_project(&root)
                    .map(|manifest| serde_json::to_value(manifest).unwrap_or_default())
                    .map_err(|e| (RpcErrorCode::Internal, format!("{e:#}")))
            }
            "engine.shutdown" => {
                info!("shutdown requested over IPC");
                let _ = self.shutdown.send(true);
                Ok(json!({ "stopping": true }))
            }
            "job.enqueue" => {
                let kind = params
                    .get("kind")
                    .and_then(Value::as_str)
                    .ok_or((RpcErrorCode::InvalidParams, "missing kind".to_owned()))?;
                let job_params = params.get("params").cloned().unwrap_or(Value::Null);
                self.scheduler
                    .enqueue(kind, job_params)
                    .map(|id| json!({ "job": id }))
                    .map_err(|message| (RpcErrorCode::Busy, message))
            }
            "job.cancel" => {
                let id = params
                    .get("id")
                    .and_then(Value::as_u64)
                    .ok_or((RpcErrorCode::InvalidParams, "missing job id".to_owned()))?;
                self.scheduler
                    .cancel(id)
                    .map(|was| json!({ "job": id, "cancelled": true, "was": was }))
                    .map_err(|message| (RpcErrorCode::InvalidParams, message))
            }
            other => Err((RpcErrorCode::UnknownMethod, format!("unknown method: {other}"))),
        }
    }
}

/// Run the daemon for `root` until ctrl-c (or an IPC bind failure).
pub async fn run_daemon(root: &Path) -> Result<()> {
    let root = root.canonicalize()?;
    let engine_dir = crate::engine_dir(&root);
    std::fs::create_dir_all(&engine_dir)?;

    let config = EvolveConfig::load(&root)?;
    let guard = ResourceGuard::from_limits(&config.evolve.resources);
    let db = Database::open(&engine_dir)?;
    db.record_project(&root)?;

    // Crash recovery: a previous daemon may have died mid-campaign.
    let swept = crate::shadow::ShadowManager::new(&root).cleanup_stale();
    if swept > 0 {
        db.audit("daemon", "shadow.cleanup_stale", Some(&swept.to_string()))?;
    }

    let events = EventBus::new();
    let (scheduler, queue) = Scheduler::new();
    let (shutdown_tx, shutdown_rx) = watch::channel(false);

    let worker = tokio::spawn(run_worker(
        queue,
        scheduler.clone(),
        events.clone(),
        shutdown_rx.clone(),
    ));
    let watcher = tokio::spawn(run_watcher(
        root.clone(),
        scheduler.clone(),
        shutdown_rx.clone(),
    ));

    let handler: Arc<dyn Handler> = Arc::new(DaemonHandler {
        root: root.clone(),
        scheduler,
        guard,
        started: Instant::now(),
        shutdown: shutdown_tx.clone(),
    });

    info!(
        "navin-engine {} serving {} (profile: {})",
        ENGINE_VERSION,
        root.display(),
        config.proof.profile
    );

    let serve = server::serve(&engine_dir, handler, events, shutdown_rx);
    tokio::pin!(serve);
    tokio::select! {
        result = &mut serve => result?,
        _ = tokio::signal::ctrl_c() => {
            info!("shutdown requested");
            let _ = shutdown_tx.send(true);
            // Give the server a moment to retract the endpoint file.
            let _ = tokio::time::timeout(std::time::Duration::from_secs(2), serve).await;
        }
    }
    let _ = shutdown_tx.send(true);
    let _ = tokio::time::timeout(std::time::Duration::from_secs(2), worker).await;
    watcher.abort();
    Ok(())
}
