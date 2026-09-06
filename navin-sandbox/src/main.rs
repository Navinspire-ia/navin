//! navin-sandbox: OS-level isolation for agent shell commands.
//!
//! Usage:
//!   navin-sandbox --workspace DIR [--allow-write PATH]... [--deny-net] \
//!                 [--strict] -- COMMAND [ARGS...]
//!
//! On Linux the sandbox is enforced with:
//!   - Landlock (kernel >= 5.13): read access to the whole filesystem, write
//!     access only to the workspace, /tmp, /dev/null and any --allow-write
//!     paths. Applies to the command and every process it spawns, with no
//!     way to escape from userspace.
//!   - An unprivileged user+network namespace when --deny-net is given: the
//!     command sees only a loopback-less, empty network stack.
//!
//! On macOS the same policy is enforced with Seatbelt (sandbox-exec): reads
//! stay open, writes are confined to the workspace and scratch locations, and
//! --deny-net cuts the network. WSL runs a real Linux kernel, so the Landlock
//! path covers it unchanged.
//!
//! Degradation is explicit: if the kernel cannot enforce a requested
//! restriction, the sandbox refuses to run under --strict and otherwise
//! prints a warning to stderr and runs the command unsandboxed-for-that-axis.
//! The exit code of the command is propagated unchanged.

use std::process::ExitCode;

struct Config {
    workspace: Option<String>,
    allow_write: Vec<String>,
    chdir: Option<String>,
    deny_net: bool,
    strict: bool,
    argv: Vec<String>,
}

fn parse_args() -> Result<Config, String> {
    let mut args = std::env::args().skip(1);
    let mut cfg = Config {
        workspace: None,
        allow_write: Vec::new(),
        chdir: None,
        deny_net: false,
        strict: false,
        argv: Vec::new(),
    };
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--workspace" => {
                cfg.workspace = Some(args.next().ok_or("--workspace needs a path")?)
            }
            "--allow-write" => cfg
                .allow_write
                .push(args.next().ok_or("--allow-write needs a path")?),
            "--chdir" => cfg.chdir = Some(args.next().ok_or("--chdir needs a path")?),
            "--deny-net" => cfg.deny_net = true,
            "--strict" => cfg.strict = true,
            "--" => {
                cfg.argv = args.collect();
                break;
            }
            other => return Err(format!("unknown argument: {other}")),
        }
    }
    if cfg.argv.is_empty() {
        return Err("no command given (use: navin-sandbox [OPTIONS] -- CMD ARGS)".into());
    }
    Ok(cfg)
}

fn main() -> ExitCode {
    let cfg = match parse_args() {
        Ok(cfg) => cfg,
        Err(err) => {
            eprintln!("navin-sandbox: {err}");
            return ExitCode::from(125);
        }
    };
    run(cfg)
}

fn apply_chdir(cfg: &Config) -> Option<ExitCode> {
    let Some(dir) = &cfg.chdir else {
        return None;
    };
    if let Err(err) = std::env::set_current_dir(dir) {
        if cfg.strict {
            eprintln!("navin-sandbox: cannot chdir {dir}: {err}");
            return Some(ExitCode::from(125));
        }
        eprintln!("navin-sandbox: warning: chdir {dir} failed ({err})");
    }
    None
}

#[cfg(target_os = "linux")]
fn run(cfg: Config) -> ExitCode {
    use std::os::unix::process::CommandExt;

    // Network isolation first: unshare affects this process, before exec.
    if cfg.deny_net {
        // CLONE_NEWUSER makes CLONE_NEWNET possible without privileges.
        let flags = libc::CLONE_NEWUSER | libc::CLONE_NEWNET;
        let rc = unsafe { libc::unshare(flags) };
        if rc != 0 {
            let err = std::io::Error::last_os_error();
            if cfg.strict {
                eprintln!("navin-sandbox: cannot isolate network: {err}");
                return ExitCode::from(125);
            }
            eprintln!("navin-sandbox: warning: network not isolated ({err})");
        }
    }

    // Filesystem confinement with Landlock.
    if let Some(workspace) = &cfg.workspace {
        match apply_landlock(workspace, &cfg.allow_write) {
            Ok(enforced) => {
                if !enforced {
                    if cfg.strict {
                        eprintln!(
                            "navin-sandbox: kernel lacks Landlock, refusing under --strict"
                        );
                        return ExitCode::from(125);
                    }
                    eprintln!("navin-sandbox: warning: filesystem not confined (no Landlock)");
                }
            }
            Err(err) => {
                if cfg.strict {
                    eprintln!("navin-sandbox: landlock setup failed: {err}");
                    return ExitCode::from(125);
                }
                eprintln!("navin-sandbox: warning: filesystem not confined ({err})");
            }
        }
    }

    if let Some(code) = apply_chdir(&cfg) {
        return code;
    }

    let error = std::process::Command::new(&cfg.argv[0])
        .args(&cfg.argv[1..])
        .exec();
    // exec only returns on failure.
    eprintln!("navin-sandbox: exec {:?} failed: {error}", cfg.argv[0]);
    ExitCode::from(if error.kind() == std::io::ErrorKind::NotFound {
        127
    } else {
        126
    })
}

/// Returns Ok(true) when the policy is enforced, Ok(false) when the kernel
/// has no Landlock support at all.
#[cfg(target_os = "linux")]
fn apply_landlock(workspace: &str, allow_write: &[String]) -> Result<bool, String> {
    use landlock::{
        Access, AccessFs, PathBeneath, PathFd, Ruleset, RulesetAttr, RulesetCreatedAttr,
        RulesetStatus, ABI,
    };

    let abi = ABI::V2;
    let mut ruleset = Ruleset::default()
        .handle_access(AccessFs::from_all(abi))
        .map_err(|e| e.to_string())?
        .create()
        .map_err(|e| e.to_string())?;

    // Read everywhere; execution of system binaries must keep working.
    ruleset = ruleset
        .add_rule(PathBeneath::new(
            PathFd::new("/").map_err(|e| e.to_string())?,
            AccessFs::from_read(abi),
        ))
        .map_err(|e| e.to_string())?;

    // Write only in the workspace and the usual scratch locations.
    let mut writable: Vec<&str> = vec![workspace, "/tmp", "/var/tmp", "/dev/null", "/dev/shm"];
    for extra in allow_write {
        writable.push(extra);
    }
    for path in writable {
        // Missing optional paths (e.g. /dev/shm in minimal containers) are skipped.
        if let Ok(fd) = PathFd::new(path) {
            ruleset = ruleset
                .add_rule(PathBeneath::new(fd, AccessFs::from_all(abi)))
                .map_err(|e| e.to_string())?;
        }
    }

    let status = ruleset.restrict_self().map_err(|e| e.to_string())?;
    Ok(status.ruleset != RulesetStatus::NotEnforced)
}

#[cfg(target_os = "macos")]
fn run(cfg: Config) -> ExitCode {
    use std::os::unix::process::CommandExt;

    let sandbox_exec = "/usr/bin/sandbox-exec";
    if !std::path::Path::new(sandbox_exec).is_file() {
        if cfg.strict && (cfg.workspace.is_some() || cfg.deny_net) {
            eprintln!("navin-sandbox: sandbox-exec not found, refusing under --strict");
            return ExitCode::from(125);
        }
        eprintln!("navin-sandbox: warning: sandbox-exec not found, running unsandboxed");
        if let Some(code) = apply_chdir(&cfg) {
            return code;
        }
        let error = std::process::Command::new(&cfg.argv[0])
            .args(&cfg.argv[1..])
            .exec();
        eprintln!("navin-sandbox: exec {:?} failed: {error}", cfg.argv[0]);
        return ExitCode::from(if error.kind() == std::io::ErrorKind::NotFound {
            127
        } else {
            126
        });
    }

    if let Some(code) = apply_chdir(&cfg) {
        return code;
    }

    let profile = seatbelt_profile(&cfg);
    let error = std::process::Command::new(sandbox_exec)
        .arg("-p")
        .arg(&profile)
        .args(&cfg.argv)
        .exec();
    eprintln!("navin-sandbox: exec sandbox-exec failed: {error}");
    ExitCode::from(126)
}

/// Build the SBPL policy: allow everything, then take writes (and optionally
/// the network) away, then give writes back beneath the workspace and the
/// scratch locations a shell needs to function at all.
#[cfg(target_os = "macos")]
fn seatbelt_profile(cfg: &Config) -> String {
    let mut profile = String::from("(version 1)\n(allow default)\n");
    if cfg.deny_net {
        profile.push_str("(deny network*)\n");
    }
    let Some(workspace) = &cfg.workspace else {
        return profile;
    };
    profile.push_str("(deny file-write*)\n(allow file-write*\n");
    let mut subpaths: Vec<String> = Vec::new();
    for path in std::iter::once(workspace).chain(cfg.allow_write.iter()) {
        // Seatbelt matches canonical paths and /tmp is a symlink to
        // /private/tmp, so rules are written against the resolved form.
        let canonical = std::fs::canonicalize(path)
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_else(|_| path.clone());
        subpaths.push(canonical);
    }
    // TMPDIR on macOS lives under /private/var/folders.
    for scratch in ["/private/tmp", "/private/var/tmp", "/private/var/folders"] {
        subpaths.push(scratch.to_owned());
    }
    for path in subpaths {
        profile.push_str("  (subpath ");
        profile.push_str(&sbpl_quote(&path));
        profile.push_str(")\n");
    }
    profile.push_str(
        "  (literal \"/dev/null\")\n  (literal \"/dev/zero\")\n  (literal \"/dev/dtracehelper\")\n  (regex #\"^/dev/tty\")\n)\n",
    );
    profile
}

#[cfg(target_os = "macos")]
fn sbpl_quote(path: &str) -> String {
    let mut out = String::with_capacity(path.len() + 2);
    out.push('"');
    for ch in path.chars() {
        if ch == '"' || ch == '\\' {
            out.push('\\');
        }
        out.push(ch);
    }
    out.push('"');
    out
}

#[cfg(not(any(target_os = "linux", target_os = "macos")))]
fn run(cfg: Config) -> ExitCode {
    // No OS enforcement on this platform yet: refuse under --strict so the
    // caller knows, otherwise pass the command through unrestricted.
    if cfg.strict && (cfg.workspace.is_some() || cfg.deny_net) {
        eprintln!("navin-sandbox: no sandbox backend on this platform");
        return ExitCode::from(125);
    }
    eprintln!("navin-sandbox: warning: no sandbox backend on this platform");
    if let Some(code) = apply_chdir(&cfg) {
        return code;
    }
    let status = std::process::Command::new(&cfg.argv[0])
        .args(&cfg.argv[1..])
        .status();
    match status {
        Ok(s) => ExitCode::from(s.code().unwrap_or(1) as u8),
        Err(err) => {
            eprintln!("navin-sandbox: exec failed: {err}");
            ExitCode::from(126)
        }
    }
}
