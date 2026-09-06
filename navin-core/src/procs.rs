//! Process management on top of sysinfo: no psutil, no `ps` subprocess.

use std::collections::HashMap;

use pyo3::prelude::*;
use sysinfo::{Pid, ProcessRefreshKind, ProcessesToUpdate, RefreshKind, System, UpdateKind};

fn snapshot() -> System {
    System::new_with_specifics(
        RefreshKind::nothing().with_processes(
            ProcessRefreshKind::nothing()
                .with_cmd(UpdateKind::Always)
                .with_memory()
                .with_cpu(),
        ),
    )
}

fn row(pid: &Pid, proc_: &sysinfo::Process) -> (u32, u32, String, u64, String) {
    let ppid = proc_.parent().map(|p| p.as_u32()).unwrap_or(0);
    let name = proc_.name().to_string_lossy().into_owned();
    let cmd = proc_
        .cmd()
        .iter()
        .map(|c| c.to_string_lossy())
        .collect::<Vec<_>>()
        .join(" ");
    (pid.as_u32(), ppid, name, proc_.memory(), cmd)
}

/// List processes as `(pid, ppid, name, memory_bytes, cmdline)` tuples,
/// optionally filtered by a case-insensitive substring of name or cmdline.
#[pyfunction]
#[pyo3(signature = (filter = None))]
pub fn list_processes(
    py: Python<'_>,
    filter: Option<String>,
) -> PyResult<Vec<(u32, u32, String, u64, String)>> {
    Ok(py.allow_threads(move || {
        let sys = snapshot();
        let needle = filter.map(|f| f.to_lowercase());
        let mut rows: Vec<_> = sys
            .processes()
            .iter()
            .map(|(pid, proc_)| row(pid, proc_))
            .filter(|(_, _, name, _, cmd)| {
                needle.as_ref().is_none_or(|n| {
                    name.to_lowercase().contains(n) || cmd.to_lowercase().contains(n)
                })
            })
            .collect();
        rows.sort_by_key(|r| r.0);
        rows
    }))
}

fn descendants(procs: &HashMap<u32, u32>, root: u32) -> Vec<u32> {
    // procs maps pid -> ppid; collect root's transitive children.
    let mut out = vec![root];
    let mut frontier = vec![root];
    while let Some(parent) = frontier.pop() {
        for (pid, ppid) in procs {
            if *ppid == parent && !out.contains(pid) {
                out.push(*pid);
                frontier.push(*pid);
            }
        }
    }
    out
}

/// The pid and all of its transitive children, root first.
#[pyfunction]
pub fn process_tree(py: Python<'_>, pid: u32) -> PyResult<Vec<u32>> {
    Ok(py.allow_threads(move || {
        let sys = snapshot();
        let map: HashMap<u32, u32> = sys
            .processes()
            .iter()
            .map(|(p, proc_)| {
                (p.as_u32(), proc_.parent().map(|pp| pp.as_u32()).unwrap_or(0))
            })
            .collect();
        descendants(&map, pid)
    }))
}

/// Kill a process and its whole subtree, children first so nothing gets
/// reparented and survives. Returns the pids that were signalled.
#[pyfunction]
#[pyo3(signature = (pid, *, force = false))]
pub fn kill_tree(py: Python<'_>, pid: u32, force: bool) -> PyResult<Vec<u32>> {
    Ok(py.allow_threads(move || {
        let mut sys = System::new();
        sys.refresh_processes(ProcessesToUpdate::All, true);
        let map: HashMap<u32, u32> = sys
            .processes()
            .iter()
            .map(|(p, proc_)| {
                (p.as_u32(), proc_.parent().map(|pp| pp.as_u32()).unwrap_or(0))
            })
            .collect();
        let mut targets = descendants(&map, pid);
        targets.reverse(); // leaves first
        let mut killed = Vec::new();
        for target in targets {
            if let Some(proc_) = sys.process(Pid::from_u32(target)) {
                let ok = if force {
                    proc_.kill()
                } else {
                    proc_
                        .kill_with(sysinfo::Signal::Term)
                        .unwrap_or_else(|| proc_.kill())
                };
                if ok {
                    killed.push(target);
                }
            }
        }
        killed
    }))
}
