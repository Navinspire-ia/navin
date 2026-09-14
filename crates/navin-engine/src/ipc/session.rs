//! What one connection is allowed to do, decided without touching a socket.
//!
//! The transport moved from a Unix socket to a loopback TCP port so that one
//! implementation serves all three platforms, and a loopback port is reachable
//! by any process on the machine. The token in the endpoint file closes that
//! back down: until a client proves it can read the file, a connection may do
//! nothing at all - not call a method, not receive an event.
//!
//! Keeping that rule here, over `&str` in and [`Outcome`] out, means it is
//! tested on every platform CI runs, including the one where the interesting
//! transport bugs live.

use serde_json::Value;
use tracing::warn;

use super::endpoint::token_matches;
use super::protocol::{Request, Response, RpcErrorCode};
use super::server::Handler;

/// What the connection loop should do with a frame.
#[derive(Debug)]
pub enum Outcome {
    /// Blank line or noise: nothing to send back.
    Ignore,
    Reply(Response),
    /// Send this, then hang up. Credentials are not retried on the same
    /// connection: a client that guesses gets one guess per TCP handshake.
    Reject(Response),
}

pub struct Session {
    token: String,
    authenticated: bool,
}

impl Session {
    pub fn new(token: impl Into<String>) -> Self {
        Session { token: token.into(), authenticated: false }
    }

    /// Whether this connection may be sent daemon events.
    pub fn authenticated(&self) -> bool {
        self.authenticated
    }

    /// Handle one line of the wire protocol.
    ///
    /// The token rides on the request frame rather than a separate handshake
    /// round trip, so a one-shot client (`navin-engine status`, the gateway's
    /// `daemon_call`) still costs exactly one write and one read.
    pub fn handle_line(&mut self, line: &str, handler: &dyn Handler) -> Outcome {
        if line.trim().is_empty() {
            return Outcome::Ignore;
        }
        let request = match serde_json::from_str::<Request>(line) {
            Ok(request) => request,
            Err(err) => {
                warn!("malformed IPC frame: {err}");
                return Outcome::Reply(Response::err(0, RpcErrorCode::InvalidParams, err.to_string()));
            }
        };
        if !self.authenticate(&request) {
            return Outcome::Reject(Response::err(
                request.id,
                RpcErrorCode::Unauthorized,
                "invalid or missing token; read .navin/evolve/endpoint.json",
            ));
        }
        // `auth` exists for a client that wants to check its token before
        // committing to a real call; every other method authenticates on its
        // own frame, so it is never mandatory.
        if request.method == "auth" {
            return Outcome::Reply(Response::ok(request.id, Value::Bool(true)));
        }
        match handler.handle(&request.method, request.params) {
            Ok(result) => Outcome::Reply(Response::ok(request.id, result)),
            Err((code, message)) => Outcome::Reply(Response::err(request.id, code, message)),
        }
    }

    fn authenticate(&mut self, request: &Request) -> bool {
        if self.authenticated {
            return true;
        }
        let Some(given) = request.token.as_deref() else {
            return false;
        };
        self.authenticated = token_matches(&self.token, given);
        self.authenticated
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    struct Echo;

    impl Handler for Echo {
        fn handle(&self, method: &str, params: Value) -> Result<Value, (RpcErrorCode, String)> {
            if method == "boom" {
                return Err((RpcErrorCode::Internal, "exploded".to_owned()));
            }
            Ok(json!({ "method": method, "params": params }))
        }
    }

    fn frame(id: u64, method: &str, token: Option<&str>) -> String {
        let mut value = json!({ "id": id, "method": method, "params": {} });
        if let Some(token) = token {
            value["token"] = json!(token);
        }
        value.to_string()
    }

    fn reply(outcome: Outcome) -> Response {
        match outcome {
            Outcome::Reply(response) | Outcome::Reject(response) => response,
            Outcome::Ignore => panic!("expected a response"),
        }
    }

    #[test]
    fn a_request_carrying_the_token_is_served() {
        let mut session = Session::new("secret");
        let outcome = session.handle_line(&frame(1, "engine.status", Some("secret")), &Echo);
        let response = reply(outcome);
        assert_eq!(response.id, 1);
        assert!(response.error.is_none());
        assert!(session.authenticated());
    }

    #[test]
    fn a_request_without_a_token_never_reaches_the_handler() {
        let mut session = Session::new("secret");
        let outcome = session.handle_line(&frame(1, "engine.shutdown", None), &Echo);
        assert!(matches!(outcome, Outcome::Reject(_)));
        let response = reply(session.handle_line(&frame(2, "engine.shutdown", None), &Echo));
        assert_eq!(response.error.unwrap().code, RpcErrorCode::Unauthorized);
        assert!(!session.authenticated());
    }

    #[test]
    fn a_wrong_token_is_refused_even_when_it_shares_a_prefix() {
        let mut session = Session::new("secretsecret");
        let outcome = session.handle_line(&frame(1, "engine.status", Some("secret")), &Echo);
        assert!(matches!(outcome, Outcome::Reject(_)));
        assert!(!session.authenticated());
    }

    #[test]
    fn later_frames_on_an_authenticated_connection_need_no_token() {
        let mut session = Session::new("secret");
        session.handle_line(&frame(1, "engine.status", Some("secret")), &Echo);
        let response = reply(session.handle_line(&frame(2, "job.enqueue", None), &Echo));
        assert!(response.error.is_none());
    }

    #[test]
    fn an_unauthenticated_connection_is_not_eligible_for_events() {
        let session = Session::new("secret");
        assert!(!session.authenticated());
    }

    #[test]
    fn a_blank_line_is_ignored_rather_than_answered() {
        let mut session = Session::new("secret");
        assert!(matches!(session.handle_line("   ", &Echo), Outcome::Ignore));
    }

    #[test]
    fn garbage_is_answered_before_authentication_without_granting_it() {
        let mut session = Session::new("secret");
        let response = reply(session.handle_line("{not json", &Echo));
        assert_eq!(response.error.unwrap().code, RpcErrorCode::InvalidParams);
        assert!(!session.authenticated());
    }

    #[test]
    fn a_handler_error_keeps_its_code_and_the_connection() {
        let mut session = Session::new("secret");
        let outcome = session.handle_line(&frame(9, "boom", Some("secret")), &Echo);
        assert!(matches!(outcome, Outcome::Reply(_)));
        let error = reply(outcome).error.unwrap();
        assert_eq!(error.code, RpcErrorCode::Internal);
        assert_eq!(error.message, "exploded");
    }

    #[test]
    fn the_auth_method_answers_by_itself_without_a_handler_call() {
        let mut session = Session::new("secret");
        let response = reply(session.handle_line(&frame(3, "auth", Some("secret")), &Echo));
        assert_eq!(response.result, Some(Value::Bool(true)));
    }
}
