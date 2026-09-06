//! Native PTY sessions (portable-pty).
//!
//! One dedicated OS thread drains the pty master into a shared buffer; the
//! Python side polls `read()` with a timeout, entirely off the GIL. This is
//! the backend for interactive terminal sessions: spawn, stream, write,
//! resize, kill - no Python event-loop hop per chunk of output.

use std::collections::HashMap;
use std::io::{Read, Write};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use portable_pty::{Child, CommandBuilder, MasterPty, PtySize};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;

#[derive(Default)]
struct Shared {
    buffer: Vec<u8>,
    eof: bool,
}

#[pyclass]
pub struct PtySession {
    master: Mutex<Box<dyn MasterPty + Send>>,
    child: Mutex<Box<dyn Child + Send + Sync>>,
    writer: Mutex<Box<dyn Write + Send>>,
    shared: Arc<(Mutex<Shared>, Condvar)>,
}

#[pymethods]
impl PtySession {
    #[new]
    #[pyo3(signature = (argv, *, cwd = None, env = None, rows = 24, cols = 80))]
    fn new(
        argv: Vec<String>,
        cwd: Option<String>,
        env: Option<HashMap<String, String>>,
        rows: u16,
        cols: u16,
    ) -> PyResult<Self> {
        let (program, args) = argv
            .split_first()
            .ok_or_else(|| PyValueError::new_err("argv must not be empty"))?;

        let pty_system = portable_pty::native_pty_system();
        let pair = pty_system
            .openpty(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|e| PyRuntimeError::new_err(format!("openpty failed: {e}")))?;

        let mut cmd = CommandBuilder::new(program);
        cmd.args(args);
        if let Some(dir) = cwd {
            cmd.cwd(dir);
        }
        if let Some(vars) = env {
            for (key, value) in vars {
                cmd.env(key, value);
            }
        }

        let child = pair
            .slave
            .spawn_command(cmd)
            .map_err(|e| PyRuntimeError::new_err(format!("spawn failed: {e}")))?;
        drop(pair.slave);

        let writer = pair
            .master
            .take_writer()
            .map_err(|e| PyRuntimeError::new_err(format!("writer failed: {e}")))?;
        let mut reader = pair
            .master
            .try_clone_reader()
            .map_err(|e| PyRuntimeError::new_err(format!("reader failed: {e}")))?;

        let shared = Arc::new((Mutex::new(Shared::default()), Condvar::new()));
        let drain = Arc::clone(&shared);
        std::thread::Builder::new()
            .name("navin-pty-reader".into())
            .spawn(move || {
                let mut chunk = [0u8; 8192];
                loop {
                    match reader.read(&mut chunk) {
                        Ok(0) | Err(_) => {
                            let (lock, cvar) = &*drain;
                            lock.lock().unwrap().eof = true;
                            cvar.notify_all();
                            break;
                        }
                        Ok(n) => {
                            let (lock, cvar) = &*drain;
                            lock.lock().unwrap().buffer.extend_from_slice(&chunk[..n]);
                            cvar.notify_all();
                        }
                    }
                }
            })
            .map_err(|e| PyRuntimeError::new_err(format!("reader thread failed: {e}")))?;

        Ok(Self {
            master: Mutex::new(pair.master),
            child: Mutex::new(child),
            writer: Mutex::new(writer),
            shared,
        })
    }

    /// Drain buffered output, waiting up to `timeout_ms` for the first byte.
    /// Returns b"" on timeout or after EOF.
    #[pyo3(signature = (timeout_ms = 0))]
    fn read<'py>(&self, py: Python<'py>, timeout_ms: u64) -> PyResult<Bound<'py, PyBytes>> {
        let shared = Arc::clone(&self.shared);
        let data = py.allow_threads(move || {
            let (lock, cvar) = &*shared;
            let mut state = lock.lock().unwrap();
            if state.buffer.is_empty() && !state.eof && timeout_ms > 0 {
                let (next, _) = cvar
                    .wait_timeout_while(state, Duration::from_millis(timeout_ms), |s| {
                        s.buffer.is_empty() && !s.eof
                    })
                    .unwrap();
                state = next;
            }
            std::mem::take(&mut state.buffer)
        });
        Ok(PyBytes::new(py, &data))
    }

    fn write(&self, data: &[u8]) -> PyResult<()> {
        let mut writer = self.writer.lock().unwrap();
        writer
            .write_all(data)
            .and_then(|_| writer.flush())
            .map_err(|e| PyRuntimeError::new_err(format!("pty write failed: {e}")))
    }

    fn resize(&self, rows: u16, cols: u16) -> PyResult<()> {
        self.master
            .lock()
            .unwrap()
            .resize(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|e| PyRuntimeError::new_err(format!("resize failed: {e}")))
    }

    fn is_alive(&self) -> bool {
        matches!(self.child.lock().unwrap().try_wait(), Ok(None))
    }

    /// Exit code if the process has finished, else None.
    fn exit_code(&self) -> Option<u32> {
        match self.child.lock().unwrap().try_wait() {
            Ok(Some(status)) => Some(status.exit_code()),
            _ => None,
        }
    }

    fn pid(&self) -> Option<u32> {
        self.child.lock().unwrap().process_id()
    }

    fn kill(&self) -> PyResult<()> {
        self.child
            .lock()
            .unwrap()
            .kill()
            .map_err(|e| PyRuntimeError::new_err(format!("kill failed: {e}")))
    }
}
