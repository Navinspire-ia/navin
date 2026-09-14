//! Where a running daemon can be reached, and the secret needed to talk to it.
//!
//! The daemon listens on an ephemeral loopback TCP port and publishes it in
//! `<project>/.navin/evolve/endpoint.json`. Clients read that file instead of
//! guessing a path, which is what makes one transport work on Linux, macOS and
//! Windows alike - see [`super`] for why the Unix socket was dropped.
//!
//! The file is the capability. Anything able to read it may drive the daemon,
//! exactly as anything able to reach the old socket file could; anything that
//! cannot read it is refused at the first frame, which the socket never did.
//! On Unix it is created 0600. On Windows it inherits the ACL of the project
//! directory: Python and Rust both write it, neither rewrites ACLs, so the
//! guarantee there is "as private as the workspace".

use anyhow::{anyhow, Context, Result};
use serde::{Deserialize, Serialize};
use std::net::Ipv4Addr;
use std::path::{Path, PathBuf};

/// Loopback only. Never a wildcard bind: a wildcard would put the daemon on
/// the network and, on Windows, raise a firewall prompt on first run.
pub const LOOPBACK: Ipv4Addr = Ipv4Addr::LOCALHOST;

/// Bytes of entropy in the shared secret; rendered as 64 hex characters.
const TOKEN_BYTES: usize = 32;

pub fn endpoint_path(engine_dir: &Path) -> PathBuf {
    engine_dir.join("endpoint.json")
}

/// The published coordinates of a live daemon.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Endpoint {
    /// Always `"tcp"` today. Present so a future transport can be recognised
    /// by a client too old to speak it, instead of misread as this one.
    pub transport: String,
    pub host: String,
    pub port: u16,
    pub token: String,
    /// Whoever holds the listener, so a client can tell a stale file from a
    /// live daemon without connecting.
    pub pid: u32,
    pub protocol: u32,
}

impl Endpoint {
    pub fn new(port: u16, token: String) -> Self {
        Endpoint {
            transport: "tcp".to_owned(),
            host: LOOPBACK.to_string(),
            port,
            token,
            pid: std::process::id(),
            protocol: super::protocol::PROTOCOL_VERSION,
        }
    }

    pub fn write(&self, engine_dir: &Path) -> Result<()> {
        std::fs::create_dir_all(engine_dir)
            .with_context(|| format!("cannot create {}", engine_dir.display()))?;
        let path = endpoint_path(engine_dir);
        let body = serde_json::to_string_pretty(self)?;
        // Written then tightened, so the window where the token is readable
        // by others is as short as one syscall pair.
        std::fs::write(&path, body)
            .with_context(|| format!("cannot write {}", path.display()))?;
        restrict(&path);
        Ok(())
    }

    pub fn read(engine_dir: &Path) -> Result<Endpoint> {
        let path = endpoint_path(engine_dir);
        let text = std::fs::read_to_string(&path)
            .with_context(|| format!("no daemon endpoint at {}", path.display()))?;
        let endpoint: Endpoint = serde_json::from_str(&text)
            .with_context(|| format!("{} is not a readable endpoint file", path.display()))?;
        if endpoint.transport != "tcp" {
            return Err(anyhow!(
                "daemon speaks `{}`, this client speaks `tcp`",
                endpoint.transport
            ));
        }
        if endpoint.port == 0 || endpoint.token.is_empty() {
            return Err(anyhow!("{} is incomplete", path.display()));
        }
        Ok(endpoint)
    }

    /// Best effort: a leftover file only ever costs a client one refused
    /// connection, so failing to remove it is not worth an error.
    pub fn remove(engine_dir: &Path) {
        let _ = std::fs::remove_file(endpoint_path(engine_dir));
    }
}

/// A fresh shared secret, hex-encoded.
pub fn new_token() -> Result<String> {
    let mut bytes = [0u8; TOKEN_BYTES];
    getrandom::getrandom(&mut bytes).map_err(|e| anyhow!("no system entropy: {e}"))?;
    let mut hex = String::with_capacity(TOKEN_BYTES * 2);
    for byte in bytes {
        hex.push_str(&format!("{byte:02x}"));
    }
    Ok(hex)
}

/// Compare two tokens without leaking their common prefix through timing.
pub fn token_matches(expected: &str, given: &str) -> bool {
    let (expected, given) = (expected.as_bytes(), given.as_bytes());
    if expected.len() != given.len() {
        return false;
    }
    let mut diff = 0u8;
    for (a, b) in expected.iter().zip(given) {
        diff |= a ^ b;
    }
    diff == 0
}

#[cfg(unix)]
fn restrict(path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
}

#[cfg(not(unix))]
fn restrict(_path: &Path) {
    // Windows ACLs are inherited from the project directory; tightening them
    // here would need a Win32 call for a file that only ever holds a token
    // scoped to this workspace.
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_written_endpoint_reads_back_identical() {
        let dir = tempfile::tempdir().unwrap();
        let endpoint = Endpoint::new(54321, new_token().unwrap());
        endpoint.write(dir.path()).unwrap();
        assert_eq!(Endpoint::read(dir.path()).unwrap(), endpoint);
    }

    #[test]
    fn tokens_are_64_hex_characters_and_never_repeat() {
        let first = new_token().unwrap();
        let second = new_token().unwrap();
        assert_eq!(first.len(), 64);
        assert!(first.chars().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(first, second);
    }

    #[test]
    fn a_missing_file_is_an_error_rather_than_a_default_endpoint() {
        let dir = tempfile::tempdir().unwrap();
        assert!(Endpoint::read(dir.path()).is_err());
    }

    #[test]
    fn an_endpoint_from_another_transport_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            endpoint_path(dir.path()),
            r#"{"transport":"pipe","host":"127.0.0.1","port":1,"token":"a","pid":2,"protocol":1}"#,
        )
        .unwrap();
        let error = Endpoint::read(dir.path()).unwrap_err().to_string();
        assert!(error.contains("pipe"), "{error}");
    }

    #[test]
    fn a_port_less_endpoint_is_refused_instead_of_dialled() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            endpoint_path(dir.path()),
            r#"{"transport":"tcp","host":"127.0.0.1","port":0,"token":"a","pid":2,"protocol":1}"#,
        )
        .unwrap();
        assert!(Endpoint::read(dir.path()).is_err());
    }

    #[test]
    fn token_comparison_accepts_only_the_exact_secret() {
        assert!(token_matches("abc123", "abc123"));
        assert!(!token_matches("abc123", "abc124"));
        assert!(!token_matches("abc123", "abc12"));
        assert!(!token_matches("abc123", ""));
    }

    #[cfg(unix)]
    #[test]
    fn the_endpoint_file_is_not_readable_by_other_users() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        Endpoint::new(1234, new_token().unwrap()).write(dir.path()).unwrap();
        let mode = std::fs::metadata(endpoint_path(dir.path()))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o077, 0, "group/other bits must be clear");
    }
}
