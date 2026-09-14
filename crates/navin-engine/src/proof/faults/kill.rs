//! Kill/recovery fault: hard-kill the process, restart it, and require it
//! to become healthy again within a time bound.

use std::time::Duration;

use super::super::checks::recovery;
use super::super::model::{CheckResult, FaultOutcome, Verdict};
use super::super::service::ServiceManager;

pub async fn run(svc: &mut ServiceManager, recovery_bound: Duration) -> FaultOutcome {
    // Kill the whole tree, then bring it back and time the recovery.
    if let Err(err) = svc.kill().await {
        return FaultOutcome::new(
            "kill_recovery",
            "hard-kill then restart",
            vec![CheckResult::new(
                "kill",
                Verdict::Fail,
                format!("could not kill the service: {err:#}"),
            )],
        );
    }

    let restart = match svc.restart().await {
        Ok(_) => svc.wait_healthy(recovery_bound).await,
        Err(err) => {
            return FaultOutcome::new(
                "kill_recovery",
                "hard-kill then restart",
                vec![CheckResult::new(
                    "recovery",
                    Verdict::Fail,
                    format!("restart failed: {err:#}"),
                )],
            );
        }
    };

    let (recovered, secs) = match restart {
        Some(elapsed) => (true, elapsed.as_secs_f64()),
        None => (false, recovery_bound.as_secs_f64()),
    };

    FaultOutcome::new(
        "kill_recovery",
        "hard-kill then restart",
        vec![recovery(recovered, secs, recovery_bound.as_secs_f64())],
    )
}
