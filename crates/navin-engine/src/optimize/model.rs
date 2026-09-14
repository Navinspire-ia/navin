//! Data model for optimization runs: N variants of healthy code compete on
//! one measured objective; every rejection carries its reason.

use serde::{Deserialize, Serialize};

use crate::baseline::latency::LatencyStats;

pub const OPTIMIZE_SCHEMA: &str = "navin-optimize-run/v1";

/// What the run tries to improve. The benchmark is identical for every
/// variant; only the ranking metric changes.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Objective {
    /// Lower P95 latency wins.
    P95,
    /// Higher requests-per-second wins.
    Throughput,
}

impl Objective {
    pub fn parse(text: &str) -> anyhow::Result<Self> {
        match text {
            "p95" => Ok(Objective::P95),
            "throughput" | "rps" => Ok(Objective::Throughput),
            other => anyhow::bail!("unknown objective `{other}` (use p95 or throughput)"),
        }
    }

    pub fn slug(self) -> &'static str {
        match self {
            Objective::P95 => "p95",
            Objective::Throughput => "throughput",
        }
    }
}

/// Positive percentage = the variant improved the objective.
pub fn gain_percent(objective: Objective, base: &LatencyStats, cand: &LatencyStats) -> f64 {
    match objective {
        Objective::P95 => {
            if base.p95_ms <= 0.0 {
                return 0.0;
            }
            (base.p95_ms - cand.p95_ms) / base.p95_ms * 100.0
        }
        Objective::Throughput => {
            if base.rps <= 0.0 {
                return 0.0;
            }
            (cand.rps - base.rps) / base.rps * 100.0
        }
    }
}

/// Failed requests as a fraction of all attempts.
pub fn error_ratio(stats: &LatencyStats) -> f64 {
    let total = stats.requests + stats.failures;
    if total == 0 {
        return 1.0; // A benchmark with zero completed requests is broken.
    }
    stats.failures as f64 / total as f64
}

/// One variant's measured outcome.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VariantOutcome {
    pub candidate_id: String,
    pub rationale: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stats: Option<LatencyStats>,
    /// Sample standard deviation of P95 across repeated benchmark windows.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub p95_std_ms: Option<f64>,
    /// Sample standard deviation of RPS across repeated benchmark windows.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rps_std: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tests_passed: Option<bool>,
    /// Business invariants from evolve.toml, run in the shadow. None when
    /// no invariant is declared.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub invariants_ok: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gain_percent: Option<f64>,
    /// Is the measured difference larger than the combined benchmark noise
    /// (Welch criterion over the repeated windows)? None with a single run.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub significant: Option<bool>,
    /// Differential verifier verdict: byte-identical behaviour on every
    /// replayed vector. None when the check was disabled.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub behavior_equivalent: Option<bool>,
    /// Eligible for the win (measured, tests green, invariants green,
    /// behaviour preserved, no error regression).
    pub eligible: bool,
    pub note: String,
    /// Unified diff of what this variant actually changed, so a rejected
    /// candidate can still be read instead of being taken on faith.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub diff: Option<String>,
}

/// Pick the eligible variant with the best gain, if it clears `min_gain`
/// and the gain is statistically distinguishable from benchmark noise.
pub fn select_winner(variants: &[VariantOutcome], min_gain_percent: f64) -> Option<usize> {
    let mut best: Option<(usize, f64)> = None;
    for (index, variant) in variants.iter().enumerate() {
        if !variant.eligible {
            continue;
        }
        let Some(gain) = variant.gain_percent else { continue };
        if gain < min_gain_percent {
            continue;
        }
        // A gain buried in measurement noise is not a gain.
        if variant.significant == Some(false) {
            continue;
        }
        if best.map(|(_, b)| gain > b).unwrap_or(true) {
            best = Some((index, gain));
        }
    }
    best.map(|(index, _)| index)
}

/// The full optimization run, persisted under `.navin/optimize/<commit>.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OptimizeReport {
    pub schema: String,
    pub commit: String,
    pub collected_at: String,
    pub objective: Objective,
    pub baseline: LatencyStats,
    /// Sample standard deviations of the baseline across repeated windows.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub baseline_p95_std_ms: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub baseline_rps_std: Option<f64>,
    /// How many benchmark windows each measurement averaged over.
    #[serde(default = "default_repeats")]
    pub bench_repeats: usize,
    /// How many business invariants were checked per measurement.
    #[serde(default)]
    pub invariants_checked: usize,
    /// Robustness score of the unmodified code (optimize requires a pass).
    pub baseline_score: u8,
    pub variants: Vec<VariantOutcome>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub winner: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub winner_gain_percent: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub promotion_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub promotion_outcome: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub notes: Vec<String>,
}

fn default_repeats() -> usize {
    1
}

impl OptimizeReport {
    pub fn save(&self, project_root: &std::path::Path) -> anyhow::Result<std::path::PathBuf> {
        use anyhow::Context;
        let dir = project_root.join(crate::NAVIN_DIR).join("optimize");
        std::fs::create_dir_all(&dir)
            .with_context(|| format!("cannot create {}", dir.display()))?;
        let path = dir.join(format!("{}.json", self.commit));
        std::fs::write(&path, serde_json::to_string_pretty(self)?)
            .with_context(|| format!("cannot write {}", path.display()))?;
        crate::report::write_sidecar(&path, &crate::report::optimize(self))?;
        Ok(path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stats(p95: f64, rps: f64, failures: u64) -> LatencyStats {
        LatencyStats { requests: 1000, failures, p50_ms: p95 / 2.0, p95_ms: p95, p99_ms: p95 * 1.2, rps }
    }

    fn variant(id: &str, gain: Option<f64>, eligible: bool) -> VariantOutcome {
        VariantOutcome {
            candidate_id: id.to_owned(),
            rationale: String::new(),
            stats: None,
            p95_std_ms: None,
            rps_std: None,
            tests_passed: None,
            invariants_ok: None,
            gain_percent: gain,
            significant: None,
            behavior_equivalent: None,
            eligible,
            note: String::new(),
            diff: None,
        }
    }

    #[test]
    fn p95_gain_is_relative_improvement() {
        let gain = gain_percent(Objective::P95, &stats(100.0, 50.0, 0), &stats(60.0, 50.0, 0));
        assert!((gain - 40.0).abs() < 1e-9);
        // A regression is a negative gain.
        let loss = gain_percent(Objective::P95, &stats(100.0, 50.0, 0), &stats(150.0, 50.0, 0));
        assert!(loss < 0.0);
    }

    #[test]
    fn throughput_gain_rewards_more_rps() {
        let gain =
            gain_percent(Objective::Throughput, &stats(10.0, 100.0, 0), &stats(10.0, 131.0, 0));
        assert!((gain - 31.0).abs() < 1e-9);
    }

    #[test]
    fn error_ratio_counts_failures() {
        assert_eq!(error_ratio(&stats(10.0, 100.0, 0)), 0.0);
        let broken = LatencyStats { requests: 0, failures: 10, ..Default::default() };
        assert_eq!(error_ratio(&broken), 1.0);
    }

    #[test]
    fn the_best_eligible_gain_wins() {
        let variants = vec![
            variant("small", Some(8.0), true),
            variant("big", Some(31.0), true),
            variant("ineligible", Some(90.0), false),
            variant("unmeasured", None, true),
        ];
        assert_eq!(select_winner(&variants, 5.0), Some(1));
    }

    #[test]
    fn a_gain_below_the_floor_does_not_win() {
        let variants = vec![variant("tiny", Some(2.0), true)];
        assert_eq!(select_winner(&variants, 5.0), None);
    }

    #[test]
    fn a_gain_inside_the_noise_does_not_win() {
        let mut noisy = variant("noisy", Some(12.0), true);
        noisy.significant = Some(false);
        let mut clean = variant("clean", Some(8.0), true);
        clean.significant = Some(true);
        // The larger gain is not significant: the smaller, proven one wins.
        assert_eq!(select_winner(&[noisy, clean], 5.0), Some(1));
    }

    #[test]
    fn without_a_noise_estimate_the_old_behaviour_holds() {
        // significant == None (single run): the gain counts as-is.
        let variants = vec![variant("single", Some(9.0), true)];
        assert_eq!(select_winner(&variants, 5.0), Some(0));
    }

    #[test]
    fn objective_parsing() {
        assert_eq!(Objective::parse("p95").unwrap(), Objective::P95);
        assert_eq!(Objective::parse("rps").unwrap(), Objective::Throughput);
        assert!(Objective::parse("vibes").is_err());
    }
}
