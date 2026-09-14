//! Progress reporting seam. Engine stages emit coarse progress events to a
//! [`ProgressSink`] so a UI can follow a long run live, without the engine
//! ever depending on the IPC layer. The default sink does nothing, so the
//! stages stay usable offline and in tests.

use serde_json::Value;

/// A stage reports progress here. Implementations must be cheap and must
/// never block the caller (events are advisory).
pub trait ProgressSink: Send + Sync {
    fn emit(&self, stage: &str, event: &str, data: Value);
}

/// Drops every event. Used by the CLI and tests.
pub struct NoopSink;

impl ProgressSink for NoopSink {
    fn emit(&self, _stage: &str, _event: &str, _data: Value) {}
}

/// Records events in memory for assertions in tests.
#[cfg(test)]
#[derive(Default)]
pub struct RecordingSink {
    events: std::sync::Mutex<Vec<(String, String)>>,
}

#[cfg(test)]
impl RecordingSink {
    pub fn new() -> Self {
        Self::default()
    }

    /// "stage.event" pairs in the order they were emitted.
    pub fn labels(&self) -> Vec<String> {
        self.events
            .lock()
            .unwrap()
            .iter()
            .map(|(s, e)| format!("{s}.{e}"))
            .collect()
    }
}

#[cfg(test)]
impl ProgressSink for RecordingSink {
    fn emit(&self, stage: &str, event: &str, _data: Value) {
        self.events.lock().unwrap().push((stage.to_owned(), event.to_owned()));
    }
}
