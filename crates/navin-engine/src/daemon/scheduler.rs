//! Job scheduler: a bounded queue drained by workers.
//!
//! Sprint 1 ships the queue and job states; later sprints enqueue proof
//! and evolve campaigns here. Event-driven by design: an empty queue
//! costs nothing.

use serde::Serialize;
use serde_json::Value;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use tokio::sync::{mpsc, watch};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum JobState {
    Queued,
    Running,
    Completed,
    Failed,
    Cancelled,
}

/// Wording used in operator-facing messages.
fn state_name(state: JobState) -> &'static str {
    match state {
        JobState::Queued => "queued",
        JobState::Running => "running",
        JobState::Completed => "completed",
        JobState::Failed => "failed",
        JobState::Cancelled => "cancelled",
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct JobRecord {
    pub id: u64,
    pub kind: String,
    pub state: JobState,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
}

#[derive(Debug)]
pub struct Job {
    pub id: u64,
    pub kind: String,
    pub params: Value,
}

/// Shared scheduler handle: enqueue jobs and inspect their states.
#[derive(Clone)]
pub struct Scheduler {
    next_id: Arc<AtomicU64>,
    records: Arc<Mutex<HashMap<u64, JobRecord>>>,
    /// One stop switch per live job, so a campaign can be called off while
    /// it runs instead of being waited out.
    stops: Arc<Mutex<HashMap<u64, watch::Sender<bool>>>>,
    tx: mpsc::Sender<Job>,
}

const QUEUE_DEPTH: usize = 64;

impl Scheduler {
    /// Returns the handle plus the receiving end the worker drains.
    pub fn new() -> (Self, mpsc::Receiver<Job>) {
        let (tx, rx) = mpsc::channel(QUEUE_DEPTH);
        (
            Scheduler {
                next_id: Arc::new(AtomicU64::new(1)),
                records: Arc::new(Mutex::new(HashMap::new())),
                stops: Arc::new(Mutex::new(HashMap::new())),
                tx,
            },
            rx,
        )
    }

    pub fn enqueue(&self, kind: &str, params: Value) -> Result<u64, String> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let job = Job { id, kind: kind.to_owned(), params };
        self.records.lock().expect("scheduler lock").insert(
            id,
            JobRecord { id, kind: kind.to_owned(), state: JobState::Queued, detail: None },
        );
        self.tx
            .try_send(job)
            .map_err(|_| "engine queue is full, retry later".to_owned())?;
        Ok(id)
    }

    pub fn set_state(&self, id: u64, state: JobState, detail: Option<String>) {
        if let Some(record) = self.records.lock().expect("scheduler lock").get_mut(&id) {
            record.state = state;
            record.detail = detail;
        }
    }

    /// The stop switch of a job, created on first use. The worker watches it
    /// for the whole run; a stop asked before the worker got there is
    /// already recorded in the job state.
    pub fn stop_signal(&self, id: u64) -> watch::Receiver<bool> {
        let mut stops = self.stops.lock().expect("scheduler lock");
        stops.entry(id).or_insert_with(|| watch::channel(false).0).subscribe()
    }

    /// Ask a job to stop. Queued jobs are cancelled outright; a running one
    /// is signalled and stops at its next step, undoing its own shadow.
    pub fn cancel(&self, id: u64) -> Result<JobState, String> {
        let state = self
            .records
            .lock()
            .expect("scheduler lock")
            .get(&id)
            .map(|record| record.state)
            .ok_or_else(|| format!("no job {id}"))?;
        match state {
            JobState::Queued => {
                self.set_state(id, JobState::Cancelled, Some("cancelled before it started".into()));
                Ok(JobState::Queued)
            }
            JobState::Running => {
                let mut stops = self.stops.lock().expect("scheduler lock");
                let stop = stops.entry(id).or_insert_with(|| watch::channel(false).0);
                let _ = stop.send(true);
                Ok(JobState::Running)
            }
            done => Err(format!("job {id} is already {}", state_name(done))),
        }
    }

    /// True when the job was called off while it waited in the queue.
    pub fn is_cancelled(&self, id: u64) -> bool {
        self.records
            .lock()
            .expect("scheduler lock")
            .get(&id)
            .map(|record| record.state == JobState::Cancelled)
            .unwrap_or(false)
    }

    /// Forget the stop switch of a finished job.
    pub fn forget_stop(&self, id: u64) {
        self.stops.lock().expect("scheduler lock").remove(&id);
    }

    pub fn snapshot(&self) -> Vec<JobRecord> {
        let mut jobs: Vec<JobRecord> = self
            .records
            .lock()
            .expect("scheduler lock")
            .values()
            .cloned()
            .collect();
        jobs.sort_by_key(|j| j.id);
        jobs
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn jobs_move_through_states() {
        let (scheduler, mut rx) = Scheduler::new();
        let id = scheduler.enqueue("baseline.run", Value::Null).unwrap();
        assert_eq!(scheduler.snapshot()[0].state, JobState::Queued);

        let job = rx.recv().await.unwrap();
        assert_eq!(job.id, id);
        scheduler.set_state(id, JobState::Running, None);
        assert_eq!(scheduler.snapshot()[0].state, JobState::Running);
        scheduler.set_state(id, JobState::Completed, None);
        assert_eq!(scheduler.snapshot()[0].state, JobState::Completed);
    }

    #[tokio::test]
    async fn a_queued_job_is_cancelled_outright() {
        let (scheduler, _rx) = Scheduler::new();
        let id = scheduler.enqueue("proof.run", Value::Null).unwrap();

        assert_eq!(scheduler.cancel(id).unwrap(), JobState::Queued);
        assert!(scheduler.is_cancelled(id));
        assert_eq!(scheduler.snapshot()[0].detail.as_deref(), Some("cancelled before it started"));
    }

    #[tokio::test]
    async fn a_running_job_is_signalled_to_stop() {
        let (scheduler, mut rx) = Scheduler::new();
        let id = scheduler.enqueue("proof.run", Value::Null).unwrap();
        rx.recv().await.unwrap();
        scheduler.set_state(id, JobState::Running, None);
        let mut stop = scheduler.stop_signal(id);
        assert!(!*stop.borrow());

        assert_eq!(scheduler.cancel(id).unwrap(), JobState::Running);
        stop.changed().await.unwrap();
        assert!(*stop.borrow());
    }

    #[tokio::test]
    async fn a_finished_job_says_it_is_too_late() {
        let (scheduler, _rx) = Scheduler::new();
        let id = scheduler.enqueue("proof.run", Value::Null).unwrap();
        scheduler.set_state(id, JobState::Completed, None);

        assert_eq!(scheduler.cancel(id).unwrap_err(), format!("job {id} is already completed"));
        assert_eq!(scheduler.cancel(999).unwrap_err(), "no job 999");
    }
}
