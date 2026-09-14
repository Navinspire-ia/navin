//! Scan a service log for known failure signatures. Substring matching on
//! lowercased text keeps this dependency-free and predictable; each hit
//! carries the offending line as evidence.
//!
//! The built-in catalogue covers the classics; `[[signatures]]` entries in
//! `.navin/evolve.toml` extend it per project, so a failure the catalogue
//! never heard of can still become a finding.

use crate::policy::config::SignatureSpec;

/// A recognised log signature and the cause it points at.
#[derive(Debug, Clone, Copy)]
pub struct LogSignal {
    /// Lowercased marker searched for in each line.
    pub marker: &'static str,
    /// Stable slug used to correlate with symptoms.
    pub id: &'static str,
    pub family: &'static str,
    pub cause: &'static str,
}

/// The catalogue, most specific first (first match per line wins).
pub const SIGNALS: &[LogSignal] = &[
    LogSignal { marker: "panicked at", id: "rust_panic", family: "reliability", cause: "a Rust panic aborted a request handler" },
    LogSignal { marker: "traceback (most recent call last)", id: "py_exception", family: "reliability", cause: "an unhandled Python exception" },
    LogSignal { marker: "goroutine ", id: "go_panic", family: "reliability", cause: "a Go panic unwound its goroutine" },
    LogSignal { marker: "panic:", id: "go_panic", family: "reliability", cause: "a Go panic unwound its goroutine" },
    LogSignal { marker: "segmentation fault", id: "segfault", family: "memory", cause: "a segmentation fault (memory-safety bug)" },
    LogSignal { marker: "sigsegv", id: "segfault", family: "memory", cause: "a segmentation fault (memory-safety bug)" },
    LogSignal { marker: "java.lang.outofmemoryerror", id: "oom", family: "memory", cause: "the JVM ran out of heap" },
    LogSignal { marker: "out of memory", id: "oom", family: "memory", cause: "the process ran out of memory" },
    LogSignal { marker: "cannot allocate memory", id: "oom", family: "memory", cause: "an allocation failed (memory pressure)" },
    LogSignal { marker: "maximum call stack size exceeded", id: "stack_overflow", family: "reliability", cause: "unbounded recursion blew the call stack" },
    LogSignal { marker: "recursionerror", id: "stack_overflow", family: "reliability", cause: "unbounded recursion blew the call stack" },
    LogSignal { marker: "stack overflow", id: "stack_overflow", family: "reliability", cause: "unbounded recursion blew the call stack" },
    LogSignal { marker: "address already in use", id: "port_in_use", family: "reliability", cause: "the listen port was still held from a previous instance" },
    LogSignal { marker: "eaddrinuse", id: "port_in_use", family: "reliability", cause: "the listen port was still held from a previous instance" },
    LogSignal { marker: "too many open files", id: "fd_exhaustion", family: "reliability", cause: "file-descriptor exhaustion under load" },
    LogSignal { marker: "emfile", id: "fd_exhaustion", family: "reliability", cause: "file-descriptor exhaustion under load" },
    LogSignal { marker: "connection refused", id: "conn_refused", family: "reliability", cause: "a dependency the service needs was unreachable" },
    LogSignal { marker: "connection reset by peer", id: "conn_reset", family: "reliability", cause: "a connection was reset mid-request" },
    LogSignal { marker: "econnreset", id: "conn_reset", family: "reliability", cause: "a connection was reset mid-request" },
    LogSignal { marker: "etimedout", id: "net_timeout", family: "reliability", cause: "a network operation timed out" },
    LogSignal { marker: "database is locked", id: "db_locked", family: "database", cause: "SQLite write contention (database is locked)" },
    LogSignal { marker: "too many connections", id: "db_pool", family: "database", cause: "the database connection pool was exhausted" },
    LogSignal { marker: "no space left on device", id: "disk_full", family: "reliability", cause: "the disk filled up" },
    LogSignal { marker: "deadlock", id: "deadlock", family: "concurrency", cause: "a lock ordering / deadlock condition" },
    LogSignal { marker: "uncaught exception", id: "uncaught", family: "reliability", cause: "an uncaught exception crashed the handler" },
    LogSignal { marker: "unhandled rejection", id: "uncaught", family: "reliability", cause: "an unhandled promise rejection" },
];

#[derive(Debug, Clone)]
pub struct SignalHit {
    pub id: String,
    pub family: String,
    pub cause: String,
    /// The trimmed log line that matched.
    pub line: String,
}

/// Return the distinct built-in signals found in `log_text`.
pub fn scan(log_text: &str) -> Vec<SignalHit> {
    scan_with(log_text, &[])
}

/// Return the distinct signals found in `log_text`, each with the first
/// line that triggered it. Project signatures are checked before the
/// built-ins: the operator's knowledge of their own logs wins.
pub fn scan_with(log_text: &str, custom: &[SignatureSpec]) -> Vec<SignalHit> {
    let custom: Vec<&SignatureSpec> = custom
        .iter()
        .filter(|sig| !sig.marker.trim().is_empty() && !sig.id.trim().is_empty())
        .collect();
    let mut hits: Vec<SignalHit> = Vec::new();
    for raw in log_text.lines() {
        let lower = raw.to_lowercase();
        let matched: Option<SignalHit> = custom
            .iter()
            .find(|sig| lower.contains(&sig.marker.to_lowercase()))
            .map(|sig| SignalHit {
                id: sig.id.clone(),
                family: sig.family.clone(),
                cause: sig.cause.clone(),
                line: String::new(),
            })
            .or_else(|| {
                SIGNALS
                    .iter()
                    .find(|sig| lower.contains(sig.marker))
                    .map(|sig| SignalHit {
                        id: sig.id.to_owned(),
                        family: sig.family.to_owned(),
                        cause: sig.cause.to_owned(),
                        line: String::new(),
                    })
            });
        if let Some(mut hit) = matched {
            let already = hits.iter().any(|h| h.id == hit.id);
            if !already {
                hit.line = raw.trim().chars().take(200).collect();
                hits.push(hit);
            }
        }
    }
    hits
}

/// Convenience: does the log contain a signal with this id?
pub fn has(hits: &[SignalHit], id: &str) -> bool {
    hits.iter().any(|h| h.id == id)
}

/// The first hit for an id, if any.
pub fn find<'a>(hits: &'a [SignalHit], id: &str) -> Option<&'a SignalHit> {
    hits.iter().find(|h| h.id == id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recognises_a_panic_and_a_port_conflict() {
        let log = "starting up\nthread 'main' panicked at src/main.rs:10\nError: listen EADDRINUSE address already in use :::3000\nserving";
        let hits = scan(log);
        assert!(has(&hits, "rust_panic"));
        assert!(has(&hits, "port_in_use"));
        assert!(find(&hits, "rust_panic").unwrap().line.contains("panicked"));
    }

    #[test]
    fn deduplicates_repeated_signals() {
        let log = "out of memory\nout of memory\nout of memory";
        let hits = scan(log);
        assert_eq!(hits.iter().filter(|h| h.id == "oom").count(), 1);
    }

    #[test]
    fn clean_log_yields_nothing() {
        assert!(scan("all good\nrequest served in 3ms\n").is_empty());
    }

    #[test]
    fn the_new_builtins_are_recognised() {
        let log = "panic: runtime error: index out of range\n\
                   sqlite3.OperationalError: database is locked\n\
                   Error: read ECONNRESET\n\
                   RecursionError: maximum recursion depth exceeded\n";
        let hits = scan(log);
        assert!(has(&hits, "go_panic"));
        assert!(has(&hits, "db_locked"));
        assert!(has(&hits, "conn_reset"));
        assert!(has(&hits, "stack_overflow"));
    }

    #[test]
    fn a_project_signature_beats_the_builtins_and_extends_them() {
        let custom = vec![SignatureSpec {
            marker: "circuit breaker open".to_owned(),
            id: "breaker".to_owned(),
            family: "reliability".to_owned(),
            cause: "the payment breaker tripped".to_owned(),
        }];
        let log = "WARN circuit breaker open for payments\nout of memory\n";
        let hits = scan_with(log, &custom);
        assert!(has(&hits, "breaker"));
        assert!(has(&hits, "oom"));
        assert_eq!(find(&hits, "breaker").unwrap().cause, "the payment breaker tripped");
    }

    #[test]
    fn blank_custom_signatures_are_ignored() {
        let custom = vec![SignatureSpec {
            marker: "  ".to_owned(),
            id: "blank".to_owned(),
            family: "reliability".to_owned(),
            cause: "nothing".to_owned(),
        }];
        assert!(scan_with("some ordinary line\n", &custom).is_empty());
    }
}
