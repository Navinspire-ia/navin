//! Find out where an application answers, without being told.
//!
//! Asking the user for a probe URL is asking them to know a port that the
//! framework picks. Instead the engine boots the app once, watches what it
//! prints and which localhost port appears, then remembers the answer.

use anyhow::{Context, Result};
use std::collections::HashSet;
use std::path::Path;
use std::time::Duration;

use crate::shadow::sandbox::SandboxLimits;

use super::health::port_open;
use super::logs::tail;
use super::process::SupervisedProcess;

/// Ports worth watching when the app says nothing useful: the defaults of
/// the mainstream web frameworks across every stack we support.
pub const CANDIDATE_PORTS: &[u16] = &[
    3000, 3001, 4000, 4200, 5000, 5001, 5173, 5174, 7000, 7001, 8000, 8001, 8080, 8081, 8088,
    8090, 8443, 9000, 9090, 1323, 2368, 4567, 6006, 8501,
];

const HOST: &str = "127.0.0.1";

/// Ports already in use before we spawn anything, so a port that appears
/// later can be attributed to the boot. Only used where the kernel cannot
/// tell us who owns a socket.
pub async fn busy_ports(candidates: &[u16]) -> HashSet<u16> {
    let mut busy = HashSet::new();
    for &port in candidates {
        if port_open(HOST, port).await {
            busy.insert(port);
        }
    }
    busy
}

/// Ports the process group `pgid` listens on, by matching the sockets in
/// `/proc/net/tcp` with the file descriptors the group holds. `None` off
/// procfs platforms, where ownership cannot be established this way.
///
/// This is what keeps the engine from probing a neighbour's server: a port
/// that merely appeared during the boot proves nothing.
fn ports_of_group(pgid: u32) -> Option<Vec<u16>> {
    if !cfg!(target_os = "linux") || !Path::new("/proc/net/tcp").is_file() {
        return None;
    }
    let owned = socket_inodes_of_group(pgid);
    let mut ports: Vec<u16> = ["/proc/net/tcp", "/proc/net/tcp6"]
        .iter()
        .filter_map(|table| std::fs::read_to_string(table).ok())
        .flat_map(|text| parse_listening(&text))
        .filter(|(_, inode)| owned.contains(inode))
        .map(|(port, _)| port)
        .collect();
    ports.sort_unstable();
    ports.dedup();
    Some(ports)
}

/// Socket inodes held by every process of the group, the group leader
/// included: a framework that forks workers still counts as one app.
fn socket_inodes_of_group(pgid: u32) -> HashSet<u64> {
    let mut inodes = HashSet::new();
    let Ok(entries) = std::fs::read_dir("/proc") else { return inodes };
    for entry in entries.flatten() {
        let Ok(pid) = entry.file_name().to_string_lossy().parse::<u32>() else { continue };
        if process_group_of(pid) != Some(pgid) {
            continue;
        }
        let Ok(fds) = std::fs::read_dir(entry.path().join("fd")) else { continue };
        for fd in fds.flatten() {
            let Ok(link) = std::fs::read_link(fd.path()) else { continue };
            let text = link.to_string_lossy();
            if let Some(inode) = text.strip_prefix("socket:[").and_then(|s| s.strip_suffix(']')) {
                inodes.extend(inode.parse::<u64>().ok());
            }
        }
    }
    inodes
}

/// Field 5 of `/proc/<pid>/stat`. The command name sits in parentheses and
/// may contain anything, so parsing starts after the last one.
fn process_group_of(pid: u32) -> Option<u32> {
    let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    let after_comm = stat.rsplit_once(')')?.1;
    after_comm.split_whitespace().nth(2)?.parse().ok()
}

/// Local port and socket inode of the LISTEN rows of a `/proc/net/tcp`
/// table: `sl local rem st tx:rx tr:tm retrnsmt uid timeout inode`.
fn parse_listening(table: &str) -> Vec<(u16, u64)> {
    table
        .lines()
        .skip(1)
        .filter_map(|line| {
            let fields: Vec<&str> = line.split_whitespace().collect();
            if fields.get(3)? != &"0A" {
                return None;
            }
            let port = u16::from_str_radix(fields.get(1)?.rsplit(':').next()?, 16).ok()?;
            Some((port, fields.get(9)?.parse().ok()?))
        })
        .collect()
}

/// The port an application announces in its own startup output. Frameworks
/// are talkative: "Running on http://127.0.0.1:5000", "Local: http://
/// localhost:5173/", "listening on port 3000", "Tomcat started on port 8080".
pub fn port_in_output(text: &str) -> Option<u16> {
    let lower = text.to_lowercase();
    if let Some(port) = scan_after(&lower, "http://") {
        return Some(port);
    }
    for marker in ["port(s): ", "port ", "port=", "port:", "listening on "] {
        if let Some(port) = scan_after(&lower, marker) {
            return Some(port);
        }
    }
    None
}

/// Walk every occurrence of `marker` and return the first plausible port
/// that follows it. `http://host:port` needs the colon skipped first.
fn scan_after(text: &str, marker: &str) -> Option<u16> {
    let mut rest = text;
    while let Some(at) = rest.find(marker) {
        let after = &rest[at + marker.len()..];
        let digits = if marker == "http://" {
            after.split(['/', ' ', '\n', '\r', '"', '\'']).next().unwrap_or("").rsplit(':').next()
        } else {
            Some(after)
        };
        if let Some(port) = digits.and_then(leading_port) {
            return Some(port);
        }
        rest = &rest[at + marker.len()..];
    }
    None
}

/// Read a port at the start of `text`, rejecting the noise (a 4-digit year,
/// a timestamp, a privileged port we could not have opened).
fn leading_port(text: &str) -> Option<u16> {
    let digits: String = text.trim_start().chars().take_while(char::is_ascii_digit).collect();
    let port: u16 = digits.parse().ok()?;
    (port >= 1024).then_some(port)
}

/// The port the booting app serves on: one it owns when the kernel can say
/// so, otherwise a well-known port that was free before the boot.
async fn opened_port(pgid: u32, hints: &[u16], busy: &HashSet<u16>) -> Option<u16> {
    if let Some(mine) = ports_of_group(pgid) {
        // An app can open several ports (metrics, debugger, HMR): a port
        // the project pointed at wins, then whatever answers HTTP.
        if let Some(hinted) = mine.iter().find(|port| hints.contains(port)) {
            return Some(*hinted);
        }
        for port in &mine {
            if speaks_http(*port).await {
                return Some(*port);
            }
        }
        return mine.first().copied();
    }
    let watched = hints.iter().copied().chain(CANDIDATE_PORTS.iter().copied());
    for port in watched.filter(|port| !busy.contains(port)) {
        if port_open(HOST, port).await {
            return Some(port);
        }
    }
    None
}

async fn speaks_http(port: u16) -> bool {
    crate::baseline::latency::get_once(HOST, port, "/").await.is_ok()
}

/// Boot `start_cmd`, learn where it answers, shut it down.
///
/// `hints` are ports the project itself suggests (compose file, framework
/// default); they are checked first but never trusted blindly.
pub async fn discover_url(
    start_cmd: &str,
    dir: &Path,
    log_path: &Path,
    hints: &[u16],
    ready_timeout: Duration,
    limits: Option<SandboxLimits>,
) -> Result<String> {
    let mut watched: Vec<u16> = hints.to_vec();
    watched.extend_from_slice(CANDIDATE_PORTS);
    let busy = busy_ports(&watched).await;

    let mut process = SupervisedProcess::spawn(start_cmd, dir, log_path, limits)?;
    let deadline = tokio::time::Instant::now() + ready_timeout;
    let mut found: Option<u16> = None;
    let mut exited: Option<i32> = None;
    while tokio::time::Instant::now() < deadline {
        tokio::time::sleep(Duration::from_millis(250)).await;
        if let Some(port) = opened_port(process.pid, hints, &busy).await {
            found = Some(port);
            break;
        }
        // What the app claims about itself, for the platforms where the
        // kernel does not tell us which sockets belong to the group.
        let printed = std::fs::read_to_string(log_path).unwrap_or_default();
        if let Some(port) = port_in_output(&printed) {
            if port_open(HOST, port).await {
                found = Some(port);
                break;
            }
        }
        if let Some(code) = process.try_exit_code()? {
            exited = Some(code);
            break;
        }
    }
    process.kill_tree().await.ok();

    match (found, exited) {
        (Some(port), _) => Ok(format!("http://{HOST}:{port}/")),
        (None, Some(code)) => anyhow::bail!(
            "cannot find where the app answers: it exited with code {code} \
             before opening a port\n--- log tail ---\n{}",
            tail(log_path, 30)
        ),
        (None, None) => anyhow::bail!(
            "cannot find where the app answers: no local port opened within {}s, \
             pass an explicit URL\n--- log tail ---\n{}",
            ready_timeout.as_secs(),
            tail(log_path, 30)
        ),
    }
}

/// Remembered answer for a given start command, so the extra boot happens
/// once per project instead of once per run.
pub struct UrlCache {
    path: std::path::PathBuf,
}

impl UrlCache {
    pub fn new(project_root: &Path) -> Self {
        Self { path: project_root.join(".navin").join("evolve").join("probe-url.json") }
    }

    pub fn get(&self, start_cmd: &str) -> Option<String> {
        let text = std::fs::read_to_string(&self.path).ok()?;
        let value: serde_json::Value = serde_json::from_str(&text).ok()?;
        (value.get("start")?.as_str()? == start_cmd)
            .then(|| value.get("url")?.as_str().map(str::to_owned))
            .flatten()
    }

    pub fn put(&self, start_cmd: &str, url: &str) -> Result<()> {
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).context("cannot create the cache directory")?;
        }
        let value = serde_json::json!({ "start": start_cmd, "url": url });
        std::fs::write(&self.path, serde_json::to_vec_pretty(&value)?)
            .context("cannot persist the discovered URL")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn framework_banners_are_understood() {
        let cases = [
            (" * Running on http://127.0.0.1:5000 (Press CTRL+C to quit)", 5000),
            ("INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C)", 8000),
            ("  ➜  Local:   http://localhost:5173/", 5173),
            ("Starting development server at http://127.0.0.1:8000/", 8000),
            ("server listening on port 3000", 3000),
            ("Tomcat started on port(s): 8080 (http)", 8080),
            ("[INFO] Now listening on: http://localhost:5001", 5001),
        ];
        for (line, expected) in cases {
            assert_eq!(port_in_output(line), Some(expected), "{line}");
        }
    }

    #[test]
    fn noise_is_not_mistaken_for_a_port() {
        assert_eq!(port_in_output("2026-08-18 01:32:11 booting worker"), None);
        assert_eq!(port_in_output("compiled 42 modules in 900 ms"), None);
        // Privileged ports cannot come from an unprivileged app under test.
        assert_eq!(port_in_output("listening on port 80"), None);
    }

    #[test]
    fn only_listening_rows_are_read_from_a_proc_table() {
        let table = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n   \
             0: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000 1000 0 51234\n   \
             1: 0100007F:C350 0100007F:9C40 01 00000000:00000000 00:00000000 00000000 1000 0 51299\n";
        assert_eq!(parse_listening(table), vec![(8080, 51234)]);
    }

    #[tokio::test]
    async fn a_port_opened_by_the_app_is_discovered() {
        let tmp = tempfile::tempdir().unwrap();
        let log = tmp.path().join("boot.log");
        let port = crate::runner::ports::free_port().unwrap();
        // Announce nothing useful: the port scan must carry the discovery.
        let cmd = format!(
            "python3 -c 'import socket,time; s=socket.socket(); \
             s.bind((\"127.0.0.1\",{port})); s.listen(); time.sleep(30)'"
        );
        let url = discover_url(&cmd, tmp.path(), &log, &[port], Duration::from_secs(15), None)
            .await
            .unwrap();
        assert_eq!(url, format!("http://127.0.0.1:{port}/"));
    }

    #[tokio::test]
    async fn an_app_that_never_listens_is_reported() {
        let tmp = tempfile::tempdir().unwrap();
        let log = tmp.path().join("boot.log");
        let err = discover_url("echo nothing-here && exit 1", tmp.path(), &log, &[], Duration::from_secs(5), None)
            .await
            .unwrap_err();
        let message = format!("{err:#}");
        assert!(message.contains("exited with code 1"), "{message}");
    }

    #[test]
    fn the_cache_only_answers_for_the_same_command() {
        let tmp = tempfile::tempdir().unwrap();
        let cache = UrlCache::new(tmp.path());
        cache.put("npm run dev", "http://127.0.0.1:5173/").unwrap();
        assert_eq!(cache.get("npm run dev").as_deref(), Some("http://127.0.0.1:5173/"));
        assert_eq!(cache.get("npm start"), None);
    }
}
