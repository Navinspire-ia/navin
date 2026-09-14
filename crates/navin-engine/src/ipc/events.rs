//! Broadcast bus carrying daemon events to every connected IPC client.

use tokio::sync::broadcast;

use super::protocol::Event;

const EVENT_BUFFER: usize = 256;

/// Cloneable handle publishing engine events.
#[derive(Debug, Clone)]
pub struct EventBus {
    tx: broadcast::Sender<Event>,
}

impl EventBus {
    pub fn new() -> Self {
        let (tx, _) = broadcast::channel(EVENT_BUFFER);
        EventBus { tx }
    }

    /// Publish, ignoring the "no subscribers" case: events are advisory.
    pub fn publish(&self, event: Event) {
        let _ = self.tx.send(event);
    }

    pub fn subscribe(&self) -> broadcast::Receiver<Event> {
        self.tx.subscribe()
    }
}

impl Default for EventBus {
    fn default() -> Self {
        Self::new()
    }
}
