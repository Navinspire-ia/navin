// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

//! Android device preview sessions driven by adb.
//!
//! Streams PNG screenshots, logcat lines, and light metrics off the GIL, and
//! injects tap / swipe / key / text input. Used by the WebUI mobile preview
//! panel and the `mobile` agent tool. Falls back to the pure-Python path when
//! this extension is not built.

use std::collections::VecDeque;
use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};

const MAX_LOG_LINES: usize = 400;
const DEFAULT_FPS: f64 = 4.0;

#[derive(Default)]
struct Shared {
    frame: Vec<u8>,
    width: u32,
    height: u32,
    frame_seq: u64,
    frame_ts_ms: u64,
    logs: VecDeque<String>,
    mem_mb: f64,
    cpu_pct: f64,
    fps: f64,
    error: Option<String>,
}

#[pyclass]
pub struct MobilePreviewSession {
    adb: String,
    serial: Option<String>,
    stop: Arc<AtomicBool>,
    shared: Arc<(Mutex<Shared>, Condvar)>,
    children: Arc<Mutex<Vec<Child>>>,
}

#[pymethods]
impl MobilePreviewSession {
    #[new]
    #[pyo3(signature = (serial = None, fps = DEFAULT_FPS, adb = None))]
    fn new(serial: Option<String>, fps: f64, adb: Option<String>) -> PyResult<Self> {
        let adb_bin = resolve_adb(adb)?;
        let serial = serial.filter(|s| !s.trim().is_empty());
        ensure_device(&adb_bin, serial.as_deref())?;

        let stop = Arc::new(AtomicBool::new(false));
        let shared = Arc::new((Mutex::new(Shared::default()), Condvar::new()));
        let children = Arc::new(Mutex::new(Vec::<Child>::new()));

        let fps = if fps.is_finite() && fps > 0.0 {
            fps.min(20.0)
        } else {
            DEFAULT_FPS
        };
        let interval = Duration::from_secs_f64(1.0 / fps);

        {
            let stop = Arc::clone(&stop);
            let shared = Arc::clone(&shared);
            let adb = adb_bin.clone();
            let serial = serial.clone();
            std::thread::Builder::new()
                .name("navin-mobile-capture".into())
                .spawn(move || capture_loop(adb, serial, stop, shared, interval))
                .map_err(|e| PyRuntimeError::new_err(format!("capture thread: {e}")))?;
        }
        {
            let stop = Arc::clone(&stop);
            let shared = Arc::clone(&shared);
            let children = Arc::clone(&children);
            let adb = adb_bin.clone();
            let serial = serial.clone();
            std::thread::Builder::new()
                .name("navin-mobile-logcat".into())
                .spawn(move || logcat_loop(adb, serial, stop, shared, children))
                .map_err(|e| PyRuntimeError::new_err(format!("logcat thread: {e}")))?;
        }
        {
            let stop = Arc::clone(&stop);
            let shared = Arc::clone(&shared);
            let adb = adb_bin.clone();
            let serial = serial.clone();
            std::thread::Builder::new()
                .name("navin-mobile-metrics".into())
                .spawn(move || metrics_loop(adb, serial, stop, shared))
                .map_err(|e| PyRuntimeError::new_err(format!("metrics thread: {e}")))?;
        }

        Ok(Self {
            adb: adb_bin,
            serial,
            stop,
            shared,
            children,
        })
    }

    /// Wait up to `timeout_ms` for a newer frame than `after_seq`.
    #[pyo3(signature = (timeout_ms = 250, after_seq = 0))]
    fn poll_frame<'py>(
        &self,
        py: Python<'py>,
        timeout_ms: u64,
        after_seq: u64,
    ) -> PyResult<Bound<'py, PyDict>> {
        let shared = Arc::clone(&self.shared);
        let snapshot = py.allow_threads(move || {
            let (lock, cvar) = &*shared;
            let mut state = lock.lock().unwrap();
            if state.frame_seq <= after_seq && timeout_ms > 0 {
                let (next, _) = cvar
                    .wait_timeout_while(state, Duration::from_millis(timeout_ms), |s| {
                        s.frame_seq <= after_seq && s.error.is_none()
                    })
                    .unwrap();
                state = next;
            }
            (
                state.frame.clone(),
                state.width,
                state.height,
                state.frame_seq,
                state.frame_ts_ms,
                state.fps,
                state.mem_mb,
                state.cpu_pct,
                state.error.clone(),
            )
        });
        let (frame, w, h, seq, ts, fps, mem, cpu, err) = snapshot;
        let dict = PyDict::new(py);
        dict.set_item("png", PyBytes::new(py, &frame))?;
        dict.set_item("width", w)?;
        dict.set_item("height", h)?;
        dict.set_item("seq", seq)?;
        dict.set_item("ts_ms", ts)?;
        dict.set_item("fps", fps)?;
        dict.set_item("mem_mb", mem)?;
        dict.set_item("cpu_pct", cpu)?;
        if let Some(msg) = err {
            dict.set_item("error", msg)?;
        }
        Ok(dict)
    }

    #[pyo3(signature = (max_lines = 80))]
    fn poll_logs(&self, max_lines: usize) -> Vec<String> {
        let (lock, _) = &*self.shared;
        let state = lock.lock().unwrap();
        let n = max_lines.min(state.logs.len());
        state.logs.iter().rev().take(n).cloned().rev().collect()
    }

    fn metrics<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let (lock, _) = &*self.shared;
        let state = lock.lock().unwrap();
        let dict = PyDict::new(py);
        dict.set_item("fps", state.fps)?;
        dict.set_item("mem_mb", state.mem_mb)?;
        dict.set_item("cpu_pct", state.cpu_pct)?;
        dict.set_item("width", state.width)?;
        dict.set_item("height", state.height)?;
        dict.set_item("frame_seq", state.frame_seq)?;
        Ok(dict)
    }

    fn tap(&self, x: i32, y: i32) -> PyResult<()> {
        self.adb_run(&["shell", "input", "tap", &x.to_string(), &y.to_string()])
    }

    #[pyo3(signature = (x1, y1, x2, y2, duration_ms = 300))]
    fn swipe(&self, x1: i32, y1: i32, x2: i32, y2: i32, duration_ms: i32) -> PyResult<()> {
        self.adb_run(&[
            "shell",
            "input",
            "swipe",
            &x1.to_string(),
            &y1.to_string(),
            &x2.to_string(),
            &y2.to_string(),
            &duration_ms.max(1).to_string(),
        ])
    }

    fn key(&self, keycode: &str) -> PyResult<()> {
        let code = keycode.trim();
        if code.is_empty() {
            return Err(PyValueError::new_err("keycode required"));
        }
        self.adb_run(&["shell", "input", "keyevent", code])
    }

    fn text(&self, value: &str) -> PyResult<()> {
        let escaped = value.replace(' ', "%s").replace('\'', "\\'");
        self.adb_run(&["shell", "input", "text", &escaped])
    }

    /// Accessibility / UIAutomator hierarchy (best-effort source mapping aid).
    fn ui_dump(&self) -> PyResult<String> {
        let _ = self.adb_run(&["shell", "uiautomator", "dump", "/sdcard/window_dump.xml"]);
        let xml = self.adb_output(&["shell", "cat", "/sdcard/window_dump.xml"])?;
        if xml.trim().is_empty() {
            return Err(PyRuntimeError::new_err(
                "ui_dump empty - is a device unlocked and UIAutomator available?",
            ));
        }
        Ok(xml)
    }

    fn device_serial(&self) -> Option<String> {
        self.serial.clone()
    }

    fn is_alive(&self) -> bool {
        !self.stop.load(Ordering::SeqCst)
    }

    fn kill(&self) -> PyResult<()> {
        self.stop.store(true, Ordering::SeqCst);
        let mut kids = self.children.lock().unwrap();
        for child in kids.iter_mut() {
            let _ = child.kill();
        }
        kids.clear();
        Ok(())
    }
}

impl Drop for MobilePreviewSession {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Ok(mut kids) = self.children.lock() {
            for child in kids.iter_mut() {
                let _ = child.kill();
            }
            kids.clear();
        }
    }
}

impl MobilePreviewSession {
    fn adb_cmd(&self) -> Command {
        let mut cmd = Command::new(&self.adb);
        if let Some(serial) = &self.serial {
            cmd.arg("-s").arg(serial);
        }
        cmd
    }

    fn adb_run(&self, args: &[&str]) -> PyResult<()> {
        let status = self
            .adb_cmd()
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .status()
            .map_err(|e| PyRuntimeError::new_err(format!("adb failed: {e}")))?;
        if !status.success() {
            return Err(PyRuntimeError::new_err(format!(
                "adb {:?} exited with {status}",
                args
            )));
        }
        Ok(())
    }

    fn adb_output(&self, args: &[&str]) -> PyResult<String> {
        let out = self
            .adb_cmd()
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()
            .map_err(|e| PyRuntimeError::new_err(format!("adb failed: {e}")))?;
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    }
}

fn resolve_adb(explicit: Option<String>) -> PyResult<String> {
    if let Some(path) = explicit.filter(|s| !s.trim().is_empty()) {
        return Ok(path);
    }
    Ok("adb".into())
}

fn ensure_device(adb: &str, serial: Option<&str>) -> PyResult<()> {
    let mut cmd = Command::new(adb);
    if let Some(s) = serial {
        cmd.arg("-s").arg(s);
    }
    let out = cmd
        .args(["devices"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .map_err(|e| PyRuntimeError::new_err(format!("adb devices failed: {e}")))?;
    let text = String::from_utf8_lossy(&out.stdout);
    let ready = text.lines().skip(1).any(|line| {
        let line = line.trim();
        !line.is_empty() && (line.contains("\tdevice") || line.ends_with(" device"))
    });
    if !ready {
        return Err(PyRuntimeError::new_err(
            "no Android device/emulator online (adb devices). Start an AVD or plug a phone.",
        ));
    }
    Ok(())
}

fn capture_loop(
    adb: String,
    serial: Option<String>,
    stop: Arc<AtomicBool>,
    shared: Arc<(Mutex<Shared>, Condvar)>,
    interval: Duration,
) {
    let mut last = Instant::now()
        .checked_sub(interval)
        .unwrap_or_else(Instant::now);
    let mut recent: VecDeque<Instant> = VecDeque::new();
    while !stop.load(Ordering::SeqCst) {
        let elapsed = last.elapsed();
        if elapsed < interval {
            std::thread::sleep(interval - elapsed);
        }
        last = Instant::now();
        match screencap(&adb, serial.as_deref()) {
            Ok(png) => {
                let (w, h) = png_size(&png).unwrap_or((0, 0));
                let now = Instant::now();
                recent.push_back(now);
                while recent
                    .front()
                    .map(|t| now.duration_since(*t) > Duration::from_secs(2))
                    .unwrap_or(false)
                {
                    recent.pop_front();
                }
                let fps = if recent.len() >= 2 {
                    (recent.len() - 1) as f64
                        / now
                            .duration_since(*recent.front().unwrap())
                            .as_secs_f64()
                            .max(0.001)
                } else {
                    0.0
                };
                let (lock, cvar) = &*shared;
                let mut state = lock.lock().unwrap();
                state.frame = png;
                state.width = w;
                state.height = h;
                state.frame_seq = state.frame_seq.saturating_add(1);
                state.frame_ts_ms = unix_ms();
                state.fps = fps;
                state.error = None;
                cvar.notify_all();
            }
            Err(msg) => {
                let (lock, cvar) = &*shared;
                let mut state = lock.lock().unwrap();
                state.error = Some(msg);
                cvar.notify_all();
                std::thread::sleep(Duration::from_millis(800));
            }
        }
    }
}

fn logcat_loop(
    adb: String,
    serial: Option<String>,
    stop: Arc<AtomicBool>,
    shared: Arc<(Mutex<Shared>, Condvar)>,
    children: Arc<Mutex<Vec<Child>>>,
) {
    let mut clear = Command::new(&adb);
    if let Some(s) = &serial {
        clear.arg("-s").arg(s);
    }
    let _ = clear.args(["logcat", "-c"]).status();

    let mut cmd = Command::new(&adb);
    if let Some(s) = &serial {
        cmd.arg("-s").arg(s);
    }
    cmd.args(["logcat", "-v", "time"])
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            let (lock, _) = &*shared;
            lock.lock().unwrap().error = Some(format!("logcat spawn failed: {e}"));
            return;
        }
    };
    let Some(out) = child.stdout.take() else {
        return;
    };
    children.lock().unwrap().push(child);
    let reader = BufReader::new(out);
    for line in reader.lines() {
        if stop.load(Ordering::SeqCst) {
            break;
        }
        let Ok(line) = line else { break };
        let line = line.trim_end().to_string();
        if line.is_empty() {
            continue;
        }
        let (lock, _) = &*shared;
        let mut state = lock.lock().unwrap();
        state.logs.push_back(line);
        while state.logs.len() > MAX_LOG_LINES {
            state.logs.pop_front();
        }
    }
}

fn metrics_loop(
    adb: String,
    serial: Option<String>,
    stop: Arc<AtomicBool>,
    shared: Arc<(Mutex<Shared>, Condvar)>,
) {
    while !stop.load(Ordering::SeqCst) {
        let mem = parse_mem_mb(&adb_output(
            &adb,
            serial.as_deref(),
            &["shell", "dumpsys", "meminfo"],
        ));
        let cpu = parse_cpu_pct(&adb_output(
            &adb,
            serial.as_deref(),
            &["shell", "top", "-n", "1", "-d", "1"],
        ));
        {
            let (lock, _) = &*shared;
            let mut state = lock.lock().unwrap();
            if let Some(v) = mem {
                state.mem_mb = v;
            }
            if let Some(v) = cpu {
                state.cpu_pct = v;
            }
        }
        for _ in 0..20 {
            if stop.load(Ordering::SeqCst) {
                return;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    }
}

fn screencap(adb: &str, serial: Option<&str>) -> Result<Vec<u8>, String> {
    let mut cmd = Command::new(adb);
    if let Some(s) = serial {
        cmd.arg("-s").arg(s);
    }
    let out = cmd
        .args(["exec-out", "screencap", "-p"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .map_err(|e| format!("screencap failed: {e}"))?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        return Err(format!("screencap exit {}: {err}", out.status));
    }
    let png = normalize_png(out.stdout);
    if png_size(&png).is_none() {
        return Err("screencap did not return a PNG".into());
    }
    Ok(png)
}

fn normalize_png(data: Vec<u8>) -> Vec<u8> {
    if data.starts_with(b"\x89PNG\r\n\x1a\n") {
        return data;
    }
    data.into_iter().filter(|&b| b != b'\r').collect()
}

fn png_size(data: &[u8]) -> Option<(u32, u32)> {
    if data.len() < 24 || &data[0..8] != b"\x89PNG\r\n\x1a\n" {
        return None;
    }
    let w = u32::from_be_bytes(data[16..20].try_into().ok()?);
    let h = u32::from_be_bytes(data[20..24].try_into().ok()?);
    Some((w, h))
}

fn adb_output(adb: &str, serial: Option<&str>, args: &[&str]) -> String {
    let mut cmd = Command::new(adb);
    if let Some(s) = serial {
        cmd.arg("-s").arg(s);
    }
    match cmd
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
    {
        Ok(o) => String::from_utf8_lossy(&o.stdout).into_owned(),
        Err(_) => String::new(),
    }
}

fn parse_mem_mb(text: &str) -> Option<f64> {
    for line in text.lines() {
        let lower = line.to_ascii_lowercase();
        if lower.contains("memtotal") {
            let kb: f64 = line.split_whitespace().find_map(|t| t.parse().ok())?;
            return Some(kb / 1024.0);
        }
        if line.trim_start().starts_with("TOTAL") {
            let parts: Vec<_> = line.split_whitespace().collect();
            if let Some(v) = parts.get(1).and_then(|t| t.parse::<f64>().ok()) {
                return Some(if v > 10_000.0 { v / 1024.0 } else { v });
            }
        }
    }
    None
}

fn parse_cpu_pct(text: &str) -> Option<f64> {
    for line in text.lines().take(8) {
        if let Some(rest) = line.strip_prefix("CPU:") {
            if let Some(v) = rest
                .split_whitespace()
                .find(|t| t.ends_with('%'))
                .and_then(|t| t.trim_end_matches('%').parse().ok())
            {
                return Some(v);
            }
        }
    }
    None
}

fn unix_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}
