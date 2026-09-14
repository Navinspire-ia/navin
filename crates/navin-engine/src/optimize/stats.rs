//! Statistical confidence for benchmarks. One run proves nothing: the same
//! code on the same machine varies by several percent between windows. Every
//! measurement is therefore repeated, summarised as mean and sample standard
//! deviation, and a gain only counts when it clears the combined noise of
//! both distributions (a Welch-style two-sample criterion).

use crate::baseline::latency::LatencyStats;

/// Critical factor applied to the combined standard error. 2.0 approximates
/// a 95% two-sided confidence bound for the small sample counts used here.
const T_CRITICAL: f64 = 2.0;

/// Mean and sample standard deviation of one measured metric.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Sample {
    pub mean: f64,
    pub std_dev: f64,
    pub n: usize,
}

/// Summarise raw values (sample standard deviation, n-1 denominator).
pub fn sample(values: &[f64]) -> Sample {
    let n = values.len();
    if n == 0 {
        return Sample { mean: 0.0, std_dev: 0.0, n: 0 };
    }
    let mean = values.iter().sum::<f64>() / n as f64;
    if n < 2 {
        return Sample { mean, std_dev: 0.0, n };
    }
    let var = values.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / (n - 1) as f64;
    Sample { mean, std_dev: var.sqrt(), n }
}

/// Is the difference between two sampled means larger than the measurement
/// noise? Welch criterion: |mean_a - mean_b| > t * sqrt(sa^2/na + sb^2/nb).
/// With fewer than two runs on either side there is no noise estimate, so
/// the difference is accepted as-is (degrades to the single-run behaviour).
pub fn significant(a: &Sample, b: &Sample) -> bool {
    if a.n < 2 || b.n < 2 {
        return true;
    }
    let standard_error =
        (a.std_dev.powi(2) / a.n as f64 + b.std_dev.powi(2) / b.n as f64).sqrt();
    (a.mean - b.mean).abs() > T_CRITICAL * standard_error
}

/// Collapse repeated benchmark windows into one representative LatencyStats:
/// latency quantiles and RPS are averaged, request counts are summed.
pub fn aggregate(runs: &[LatencyStats]) -> LatencyStats {
    if runs.is_empty() {
        return LatencyStats::default();
    }
    let n = runs.len() as f64;
    LatencyStats {
        requests: runs.iter().map(|r| r.requests).sum(),
        failures: runs.iter().map(|r| r.failures).sum(),
        p50_ms: runs.iter().map(|r| r.p50_ms).sum::<f64>() / n,
        p95_ms: runs.iter().map(|r| r.p95_ms).sum::<f64>() / n,
        p99_ms: runs.iter().map(|r| r.p99_ms).sum::<f64>() / n,
        rps: runs.iter().map(|r| r.rps).sum::<f64>() / n,
    }
}

/// The p95 values of each run, for building a distribution sample.
pub fn p95_series(runs: &[LatencyStats]) -> Vec<f64> {
    runs.iter().map(|r| r.p95_ms).collect()
}

/// The RPS values of each run, for building a distribution sample.
pub fn rps_series(runs: &[LatencyStats]) -> Vec<f64> {
    runs.iter().map(|r| r.rps).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sample_mean_and_std_are_correct() {
        let s = sample(&[100.0, 110.0, 120.0]);
        assert!((s.mean - 110.0).abs() < 1e-9);
        assert!((s.std_dev - 10.0).abs() < 1e-9);
        assert_eq!(s.n, 3);
    }

    #[test]
    fn single_value_has_zero_std() {
        let s = sample(&[42.0]);
        assert_eq!(s.mean, 42.0);
        assert_eq!(s.std_dev, 0.0);
    }

    #[test]
    fn a_small_gain_inside_the_noise_is_not_significant() {
        // baseline: 120 +- 4, candidate: 117 +- 5 -> diff 3 < 2*sqrt(16/3+25/3)
        let base = sample(&[116.0, 120.0, 124.0]);
        let cand = sample(&[112.0, 117.0, 122.0]);
        assert!(!significant(&base, &cand));
    }

    #[test]
    fn a_large_gain_outside_the_noise_is_significant() {
        // baseline: ~120 +- 3, candidate: ~92 +- 2 -> clearly separated.
        let base = sample(&[117.0, 120.0, 123.0]);
        let cand = sample(&[90.0, 92.0, 94.0]);
        assert!(significant(&base, &cand));
    }

    #[test]
    fn single_runs_degrade_to_raw_comparison() {
        let base = sample(&[120.0]);
        let cand = sample(&[119.0]);
        assert!(significant(&base, &cand));
    }

    #[test]
    fn aggregate_averages_quantiles_and_sums_counts() {
        let runs = vec![
            LatencyStats { requests: 100, failures: 1, p50_ms: 10.0, p95_ms: 20.0, p99_ms: 30.0, rps: 500.0 },
            LatencyStats { requests: 200, failures: 3, p50_ms: 12.0, p95_ms: 24.0, p99_ms: 34.0, rps: 700.0 },
        ];
        let agg = aggregate(&runs);
        assert_eq!(agg.requests, 300);
        assert_eq!(agg.failures, 4);
        assert!((agg.p95_ms - 22.0).abs() < 1e-9);
        assert!((agg.rps - 600.0).abs() < 1e-9);
    }
}
