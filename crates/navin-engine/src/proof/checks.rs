//! Invariant checks shared by every fault. Each returns a [`CheckResult`]
//! with an honest verdict and, where it makes sense, the number behind it.

use super::model::{CheckResult, Verdict};

/// The service process must still be answering after the fault.
pub fn no_crash(alive_after: bool) -> CheckResult {
    if alive_after {
        CheckResult::new("no_crash", Verdict::Pass, "service still serving after the fault")
    } else {
        CheckResult::new("no_crash", Verdict::Fail, "service was not serving after the fault")
    }
}

/// After a kill, the service must come back healthy within `bound_secs`.
pub fn recovery(recovered: bool, secs: f64, bound_secs: f64) -> CheckResult {
    if !recovered {
        return CheckResult::new(
            "recovery",
            Verdict::Fail,
            format!("did not recover within {bound_secs:.0}s"),
        )
        .with_metric(secs, bound_secs);
    }
    // Recovering in the last 20% of the budget is a soft warning.
    let verdict = if secs <= bound_secs * 0.8 { Verdict::Pass } else { Verdict::Weak };
    CheckResult::new("recovery", verdict, format!("recovered in {secs:.2}s"))
        .with_metric(secs, bound_secs)
}

/// Under load, the failure ratio must stay at or below `max_ratio`.
pub fn error_rate(failures: u64, total: u64, max_ratio: f64) -> CheckResult {
    let ratio = if total == 0 { 1.0 } else { failures as f64 / total as f64 };
    let verdict = if ratio <= max_ratio {
        Verdict::Pass
    } else if ratio <= max_ratio * 5.0 {
        Verdict::Weak
    } else {
        Verdict::Fail
    };
    CheckResult::new(
        "error_rate",
        verdict,
        format!("{failures}/{total} failed ({:.2}%)", ratio * 100.0),
    )
    .with_metric(ratio, max_ratio)
}

/// Peak RSS under stress must respect the policy ceiling.
pub fn resource_bound(rss_mb: u64, limit_mb: u64) -> CheckResult {
    if limit_mb == 0 {
        return CheckResult::new("resource_bound", Verdict::Pass, "no memory ceiling configured");
    }
    let verdict = if rss_mb <= limit_mb {
        Verdict::Pass
    } else if rss_mb <= limit_mb * 2 {
        Verdict::Weak
    } else {
        Verdict::Fail
    };
    CheckResult::new(
        "resource_bound",
        verdict,
        format!("peak RSS {rss_mb} MB (ceiling {limit_mb} MB)"),
    )
    .with_metric(rss_mb as f64, limit_mb as f64)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn error_rate_grades_by_severity() {
        assert_eq!(error_rate(0, 100, 0.01).verdict, Verdict::Pass);
        assert_eq!(error_rate(3, 100, 0.01).verdict, Verdict::Weak);
        assert_eq!(error_rate(50, 100, 0.01).verdict, Verdict::Fail);
        // No requests at all is a failure, not a free pass.
        assert_eq!(error_rate(0, 0, 0.01).verdict, Verdict::Fail);
    }

    #[test]
    fn recovery_warns_when_it_only_just_made_it() {
        assert_eq!(recovery(true, 1.0, 10.0).verdict, Verdict::Pass);
        assert_eq!(recovery(true, 9.5, 10.0).verdict, Verdict::Weak);
        assert_eq!(recovery(false, 10.0, 10.0).verdict, Verdict::Fail);
    }

    #[test]
    fn resource_bound_grades_overshoot() {
        assert_eq!(resource_bound(100, 512).verdict, Verdict::Pass);
        assert_eq!(resource_bound(700, 512).verdict, Verdict::Weak);
        assert_eq!(resource_bound(2000, 512).verdict, Verdict::Fail);
    }
}
