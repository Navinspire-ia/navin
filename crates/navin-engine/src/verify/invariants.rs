//! Business invariants: project-specific commands declared in
//! `.navin/evolve.toml` that must exit 0 for a candidate to be promotable.
//! Tests prove the code, invariants prove the domain (order totals,
//! duplicate payments, ...). They run inside the shadow, never the workspace.

use serde::{Deserialize, Serialize};
use std::path::Path;
use std::time::Duration;

use crate::policy::config::InvariantSpec;
use crate::runner::SupervisedProcess;

/// Outcome of one full invariant pass.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InvariantOutcome {
    pub checked: usize,
    pub passed: bool,
    /// Names of the invariants that failed, in declaration order.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub failures: Vec<String>,
}

/// Run every declared invariant inside `work_dir` (a shadow). Any non-zero
/// exit, spawn failure or timeout marks that invariant as failed; all of
/// them run even after a failure so the report is complete.
pub async fn run_invariants(
    specs: &[InvariantSpec],
    work_dir: &Path,
    project_root: &Path,
) -> InvariantOutcome {
    let mut failures = Vec::new();
    let log = crate::engine_dir(project_root).join("logs").join("invariants.log");
    for spec in specs {
        let deadline = Duration::from_secs(spec.timeout_secs.max(1));
        let ok = match SupervisedProcess::spawn(&spec.command, work_dir, &log, None) {
            Ok(process) => matches!(process.wait_with_deadline(deadline).await, Ok(0)),
            Err(_) => false,
        };
        if !ok {
            failures.push(spec.name.clone());
        }
    }
    InvariantOutcome { checked: specs.len(), passed: failures.is_empty(), failures }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec(name: &str, command: &str) -> InvariantSpec {
        InvariantSpec { name: name.to_owned(), command: command.to_owned(), timeout_secs: 10 }
    }

    #[tokio::test]
    async fn all_green_invariants_pass() {
        let tmp = tempfile::tempdir().unwrap();
        let specs = vec![spec("a", "true"), spec("b", "exit 0")];
        let outcome = run_invariants(&specs, tmp.path(), tmp.path()).await;
        assert!(outcome.passed);
        assert_eq!(outcome.checked, 2);
        assert!(outcome.failures.is_empty());
    }

    #[tokio::test]
    async fn a_failing_invariant_is_named() {
        let tmp = tempfile::tempdir().unwrap();
        let specs = vec![spec("green", "true"), spec("red", "exit 3"), spec("also_red", "false")];
        let outcome = run_invariants(&specs, tmp.path(), tmp.path()).await;
        assert!(!outcome.passed);
        assert_eq!(outcome.failures, vec!["red".to_owned(), "also_red".to_owned()]);
    }

    #[tokio::test]
    async fn no_specs_means_a_trivially_green_pass() {
        let tmp = tempfile::tempdir().unwrap();
        let outcome = run_invariants(&[], tmp.path(), tmp.path()).await;
        assert!(outcome.passed);
        assert_eq!(outcome.checked, 0);
    }
}
