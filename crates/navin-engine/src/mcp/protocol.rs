//! MCP wire format: JSON-RPC 2.0, one object per line, on stdin/stdout.
//!
//! Only the subset the engine needs is modelled here: the initialize
//! handshake, tool discovery, tool calls and progress notifications.

use serde::Deserialize;
use serde_json::{json, Value};

/// Protocol revision this server was written against. When a host asks for
/// another one its choice is echoed back: the surface used here (tools plus
/// progress) has been stable across revisions, and refusing would break
/// hosts shipping a newer date than this build.
pub const DEFAULT_PROTOCOL_VERSION: &str = "2025-06-18";

pub const PARSE_ERROR: i64 = -32700;
pub const METHOD_NOT_FOUND: i64 = -32601;
pub const INVALID_PARAMS: i64 = -32602;

/// One inbound frame. Requests carry an `id` and expect exactly one reply;
/// notifications have none and must be answered with silence.
#[derive(Debug, Deserialize)]
pub struct Incoming {
    pub method: String,
    #[serde(default)]
    pub id: Option<Value>,
    #[serde(default)]
    pub params: Value,
}

impl Incoming {
    pub fn is_notification(&self) -> bool {
        self.id.is_none()
    }

    /// `params._meta.progressToken`, set by hosts that want live progress.
    pub fn progress_token(&self) -> Option<Value> {
        self.params.get("_meta")?.get("progressToken").cloned()
    }

    pub fn tool_name(&self) -> Option<&str> {
        self.params.get("name")?.as_str()
    }

    pub fn tool_arguments(&self) -> Value {
        self.params
            .get("arguments")
            .cloned()
            .unwrap_or_else(|| json!({}))
    }
}

pub fn success(id: Value, result: Value) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "result": result })
}

pub fn failure(id: Value, code: i64, message: impl Into<String>) -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": id,
        "error": { "code": code, "message": message.into() },
    })
}

pub fn notification(method: &str, params: Value) -> Value {
    json!({ "jsonrpc": "2.0", "method": method, "params": params })
}

/// A tool result the host hands to its model. `is_error` keeps a failed run
/// inside the conversation, so the agent reads the reason and adapts,
/// instead of seeing a protocol error it cannot act on.
pub fn tool_result(text: String, is_error: bool) -> Value {
    json!({
        "content": [{ "type": "text", "text": text }],
        "isError": is_error,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_frame_without_an_id_is_a_notification() {
        let notice: Incoming =
            serde_json::from_str(r#"{"jsonrpc":"2.0","method":"notifications/initialized"}"#)
                .unwrap();
        assert!(notice.is_notification());

        let request: Incoming =
            serde_json::from_str(r#"{"jsonrpc":"2.0","id":7,"method":"tools/list"}"#).unwrap();
        assert!(!request.is_notification());
        assert_eq!(request.id, Some(json!(7)));
    }

    #[test]
    fn tool_calls_carry_their_name_and_arguments() {
        let request: Incoming = serde_json::from_str(
            r#"{"id":1,"method":"tools/call","params":{"name":"prove","arguments":{"profile":"quick"}}}"#,
        )
        .unwrap();
        assert_eq!(request.tool_name(), Some("prove"));
        assert_eq!(request.tool_arguments()["profile"], json!("quick"));
    }

    #[test]
    fn a_missing_arguments_object_reads_as_empty() {
        let request: Incoming =
            serde_json::from_str(r#"{"id":1,"method":"tools/call","params":{"name":"prove"}}"#)
                .unwrap();
        assert_eq!(request.tool_arguments(), json!({}));
    }

    #[test]
    fn the_progress_token_is_read_from_meta() {
        let request: Incoming = serde_json::from_str(
            r#"{"id":1,"method":"tools/call","params":{"name":"prove","_meta":{"progressToken":"abc"}}}"#,
        )
        .unwrap();
        assert_eq!(request.progress_token(), Some(json!("abc")));

        let plain: Incoming =
            serde_json::from_str(r#"{"id":1,"method":"tools/call","params":{"name":"prove"}}"#)
                .unwrap();
        assert_eq!(plain.progress_token(), None);
    }

    #[test]
    fn frames_are_valid_json_rpc() {
        let ok = success(json!(1), json!({ "tools": [] }));
        assert_eq!(ok["jsonrpc"], json!("2.0"));
        assert_eq!(ok["id"], json!(1));

        let bad = failure(json!(2), METHOD_NOT_FOUND, "nope");
        assert_eq!(bad["error"]["code"], json!(METHOD_NOT_FOUND));
        assert_eq!(bad["error"]["message"], json!("nope"));
        assert!(bad.get("result").is_none());

        let notice = notification("notifications/progress", json!({ "progress": 1 }));
        assert!(notice.get("id").is_none());
    }

    #[test]
    fn a_failed_stage_is_reported_as_tool_output() {
        let result = tool_result("cannot find where the app answers".to_owned(), true);
        assert_eq!(result["isError"], json!(true));
        assert_eq!(result["content"][0]["type"], json!("text"));
        assert!(result["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("cannot find"));
    }
}
