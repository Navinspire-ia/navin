//! The engine as an MCP server: one tool provider, any AI coding environment.
//!
//! `navin-engine mcp` speaks the Model Context Protocol over stdin/stdout,
//! which is what Cursor, Claude Code, Codex, Gemini CLI, OpenCode and the
//! others already know how to launch. The host's own model becomes the
//! candidate generator: it calls `diagnose` to learn what is measurably
//! broken, writes patches itself, and hands them back through `fix` or
//! `optimize`, where they are applied in a shadow worktree, measured,
//! tested and gated exactly as they are inside Navin. No API key here, no
//! provider configuration: the engine never talks to a model.
//!
//! Requests are served one at a time on purpose. Every stage benchmarks a
//! running app, and two campaigns at once would poison each other's numbers.
//! Stdout carries protocol frames only; logs go to stderr.

mod protocol;
mod tools;

use anyhow::Result;
use serde_json::{json, Value};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use tokio::io::{AsyncBufReadExt, BufReader};

use crate::progress::{NoopSink, ProgressSink};
use protocol::{
    failure, notification, success, Incoming, DEFAULT_PROTOCOL_VERSION, INVALID_PARAMS,
    METHOD_NOT_FOUND, PARSE_ERROR,
};

/// Serialised access to stdout: replies and progress notifications share the
/// single channel the host reads.
#[derive(Clone)]
struct Sender(Arc<Mutex<std::io::Stdout>>);

impl Sender {
    fn new() -> Self {
        Sender(Arc::new(Mutex::new(std::io::stdout())))
    }

    fn send(&self, frame: &Value) {
        let Ok(mut out) = self.0.lock() else { return };
        // A closed stdout means the host is gone; the read loop will see it.
        let _ = writeln!(out, "{frame}");
        let _ = out.flush();
    }
}

/// Forwards engine stage events as MCP progress notifications, so a host
/// that would otherwise time out a ten-minute proof sees it advancing.
struct ProgressBridge {
    sender: Sender,
    token: Value,
    step: AtomicU64,
}

impl ProgressSink for ProgressBridge {
    fn emit(&self, stage: &str, event: &str, data: Value) {
        let step = self.step.fetch_add(1, Ordering::Relaxed) + 1;
        self.sender
            .send(&progress_frame(&self.token, step, stage, event, &data));
    }
}

fn progress_frame(token: &Value, step: u64, stage: &str, event: &str, data: &Value) -> Value {
    let mut message = format!("{stage}.{event}");
    // Fold the payload into the message: extra params risk rejection by
    // hosts that validate notifications against the MCP schema.
    if let Some(fields) = data.as_object().filter(|fields| !fields.is_empty()) {
        let mut detail = Value::Object(fields.clone()).to_string();
        detail.truncate(200);
        message = format!("{message} {detail}");
    }
    notification(
        "notifications/progress",
        json!({
            "progressToken": token,
            "progress": step,
            "message": message,
        }),
    )
}

/// Serve MCP on stdio until the host closes stdin.
pub async fn serve_stdio(default_root: PathBuf) -> Result<()> {
    let root = default_root
        .canonicalize()
        .unwrap_or_else(|_| default_root.clone());
    let sender = Sender::new();
    let mut lines = BufReader::new(tokio::io::stdin()).lines();

    while let Some(line) = lines.next_line().await? {
        if line.trim().is_empty() {
            continue;
        }
        let message: Incoming = match serde_json::from_str(&line) {
            Ok(message) => message,
            Err(error) => {
                sender.send(&failure(Value::Null, PARSE_ERROR, error.to_string()));
                continue;
            }
        };
        // Notifications (`notifications/initialized`, `notifications/cancelled`)
        // must not be answered.
        if message.is_notification() {
            continue;
        }
        let id = message.id.clone().unwrap_or(Value::Null);
        let frame = dispatch(&message, id, &root, &sender).await;
        sender.send(&frame);
    }
    Ok(())
}

async fn dispatch(message: &Incoming, id: Value, root: &Path, sender: &Sender) -> Value {
    match message.method.as_str() {
        "initialize" => success(id, initialize_result(&message.params)),
        "ping" => success(id, json!({})),
        "tools/list" => success(id, json!({ "tools": tools::catalog() })),
        "tools/call" => call_tool(message, id, root, sender).await,
        // Only the tools capability is declared, but hosts probe these two
        // anyway; an empty list is quieter than an error.
        "resources/list" => success(id, json!({ "resources": [] })),
        "prompts/list" => success(id, json!({ "prompts": [] })),
        other => failure(id, METHOD_NOT_FOUND, format!("unsupported method `{other}`")),
    }
}

fn initialize_result(params: &Value) -> Value {
    // Echo the host's revision when it names one: the surface used here has
    // been stable, and refusing a newer date would break the handshake.
    let version = params
        .get("protocolVersion")
        .and_then(Value::as_str)
        .unwrap_or(DEFAULT_PROTOCOL_VERSION);
    json!({
        "protocolVersion": version,
        "capabilities": { "tools": { "listChanged": false } },
        "serverInfo": { "name": "navin-engine", "version": crate::ENGINE_VERSION },
        "instructions": tools::INSTRUCTIONS,
    })
}

async fn call_tool(message: &Incoming, id: Value, root: &Path, sender: &Sender) -> Value {
    let Some(name) = message.tool_name() else {
        return failure(id, INVALID_PARAMS, "tools/call needs a tool name");
    };
    if !tools::is_known(name) {
        return failure(id, INVALID_PARAMS, format!("unknown tool `{name}`"));
    }
    let arguments = message.tool_arguments();
    let sink: Box<dyn ProgressSink> = match message.progress_token() {
        Some(token) => Box::new(ProgressBridge {
            sender: sender.clone(),
            token,
            step: AtomicU64::new(0),
        }),
        None => Box::new(NoopSink),
    };

    match tools::call(name, &arguments, root, sink.as_ref()).await {
        Ok(text) => success(id, protocol::tool_result(text, false)),
        // A stage that could not run is the agent's problem to work around,
        // not the host's: hand it back as readable tool output.
        Err(error) => success(id, protocol::tool_result(format!("{error:#}"), true)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(body: &str) -> Incoming {
        serde_json::from_str(body).unwrap()
    }

    async fn reply(body: &str) -> Value {
        let sender = Sender::new();
        let message = request(body);
        let id = message.id.clone().unwrap_or(Value::Null);
        dispatch(&message, id, Path::new("."), &sender).await
    }

    #[tokio::test]
    async fn the_handshake_declares_tools_and_echoes_the_revision() {
        let frame = reply(
            r#"{"id":1,"method":"initialize","params":{"protocolVersion":"2099-01-01","capabilities":{}}}"#,
        )
        .await;
        assert_eq!(frame["result"]["protocolVersion"], json!("2099-01-01"));
        assert_eq!(frame["result"]["serverInfo"]["name"], json!("navin-engine"));
        assert!(frame["result"]["capabilities"]["tools"].is_object());
        assert!(frame["result"]["instructions"]
            .as_str()
            .unwrap()
            .contains("diagnose"));
    }

    #[tokio::test]
    async fn a_host_that_names_no_revision_gets_the_default() {
        let frame = reply(r#"{"id":1,"method":"initialize","params":{}}"#).await;
        assert_eq!(
            frame["result"]["protocolVersion"],
            json!(DEFAULT_PROTOCOL_VERSION)
        );
    }

    #[tokio::test]
    async fn tools_are_listed_with_their_schemas() {
        let frame = reply(r#"{"id":2,"method":"tools/list"}"#).await;
        let listed = frame["result"]["tools"].as_array().unwrap();
        let names: Vec<&str> = listed
            .iter()
            .map(|tool| tool["name"].as_str().unwrap())
            .collect();
        assert!(names.contains(&"diagnose"));
        assert!(names.contains(&"fix"));
        assert!(names.contains(&"optimize"));
    }

    #[tokio::test]
    async fn probes_for_undeclared_capabilities_get_empty_lists() {
        assert_eq!(
            reply(r#"{"id":3,"method":"resources/list"}"#).await["result"]["resources"],
            json!([])
        );
        assert_eq!(
            reply(r#"{"id":4,"method":"prompts/list"}"#).await["result"]["prompts"],
            json!([])
        );
        assert_eq!(reply(r#"{"id":5,"method":"ping"}"#).await["result"], json!({}));
    }

    #[tokio::test]
    async fn an_unsupported_method_is_a_protocol_error() {
        let frame = reply(r#"{"id":6,"method":"sampling/createMessage"}"#).await;
        assert_eq!(frame["error"]["code"], json!(METHOD_NOT_FOUND));
    }

    #[tokio::test]
    async fn an_unknown_tool_is_refused_before_anything_runs() {
        let frame =
            reply(r#"{"id":7,"method":"tools/call","params":{"name":"delete_everything"}}"#).await;
        assert_eq!(frame["error"]["code"], json!(INVALID_PARAMS));
        assert!(frame["error"]["message"]
            .as_str()
            .unwrap()
            .contains("delete_everything"));
    }

    #[tokio::test]
    async fn a_tool_that_cannot_run_answers_inside_the_conversation() {
        // `fix` without a finding: the agent must be able to read why and retry.
        let frame = reply(
            r#"{"id":8,"method":"tools/call","params":{"name":"fix","arguments":{"candidates":[]}}}"#,
        )
        .await;
        assert_eq!(frame["result"]["isError"], json!(true));
        assert!(frame["result"]["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("finding"));
    }

    #[tokio::test]
    async fn a_read_only_tool_runs_end_to_end() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("package.json"),
            r#"{"name":"demo","scripts":{"dev":"node server.js","test":"node --test"}}"#,
        )
        .unwrap();
        let root = dir.path().canonicalize().unwrap();
        let body = json!({
            "id": 9,
            "method": "tools/call",
            "params": { "name": "inspect_project", "arguments": { "path": root } },
        });
        let frame = reply(&body.to_string()).await;
        assert_eq!(frame["result"]["isError"], json!(false));
        let text = frame["result"]["content"][0]["text"].as_str().unwrap();
        assert!(text.contains("\"units\""), "manifest expected, got {text}");
    }

    #[test]
    fn progress_events_become_standard_notifications() {
        let frame = progress_frame(
            &json!("tok-1"),
            1,
            "proof",
            "started",
            &json!({ "profile": "quick" }),
        );
        assert_eq!(frame["method"], json!("notifications/progress"));
        assert_eq!(frame["params"]["progressToken"], json!("tok-1"));
        assert_eq!(frame["params"]["progress"], json!(1));
        let message = frame["params"]["message"].as_str().unwrap();
        assert!(message.starts_with("proof.started"));
        assert!(message.contains("quick"));
        // No payload: the label alone, and no stray fields for a strict host.
        let bare = progress_frame(&json!(3), 2, "proof", "fault_done", &Value::Null);
        assert_eq!(bare["params"]["message"], json!("proof.fault_done"));
        assert_eq!(bare["params"].as_object().unwrap().len(), 3);
    }

    #[test]
    fn progress_messages_stay_short() {
        let long = json!({ "log": "x".repeat(4000) });
        let frame = progress_frame(&json!(1), 1, "optimize", "variant", &long);
        assert!(frame["params"]["message"].as_str().unwrap().len() < 300);
    }
}
