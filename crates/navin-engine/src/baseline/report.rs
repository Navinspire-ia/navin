//! Baseline report, persisted under `.navin/baselines/<commit>.json`.

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

use super::latency::LatencyStats;

pub const BASELINE_SCHEMA: &str = "navin-baseline/v1";

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BaselineReport {
    pub schema: String,
    /// Git SHA the baseline was measured at; "workdir" for non-git projects.
    pub commit: String,
    pub collected_at: String,
    /// Where the measurement ran (shadow path or project root).
    pub measured_in: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub build_ms: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub startup_ms: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub latency: Option<LatencyStats>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cpu_percent_avg: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rss_mb_peak: Option<u64>,
    /// What was NOT measured and why: no false guarantees.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub notes: Vec<String>,
}

impl BaselineReport {
    pub fn new(commit: &str, measured_in: &Path) -> Self {
        BaselineReport {
            schema: BASELINE_SCHEMA.to_owned(),
            commit: commit.to_owned(),
            collected_at: now_utc(),
            measured_in: measured_in.display().to_string(),
            ..Default::default()
        }
    }

    /// Write to `.navin/baselines/<commit>.json`, returning the path.
    pub fn save(&self, project_root: &Path) -> Result<PathBuf> {
        let dir = project_root.join(crate::NAVIN_DIR).join("baselines");
        std::fs::create_dir_all(&dir)
            .with_context(|| format!("cannot create {}", dir.display()))?;
        let path = dir.join(format!("{}.json", self.commit));
        let json = serde_json::to_string_pretty(self)?;
        std::fs::write(&path, json)
            .with_context(|| format!("cannot write {}", path.display()))?;
        Ok(path)
    }
}

fn now_utc() -> String {
    // RFC3339 without pulling a datetime crate: seconds since epoch is
    // enough for ordering; readable form comes from SQLite timestamps.
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format!("epoch:{secs}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn report_is_saved_under_baselines() {
        let tmp = tempfile::tempdir().unwrap();
        let mut report = BaselineReport::new("abc123", tmp.path());
        report.build_ms = Some(1200);
        report.notes.push("latency not measured: no HTTP endpoint".to_owned());
        let path = report.save(tmp.path()).unwrap();
        assert!(path.ends_with(".navin/baselines/abc123.json"));
        let loaded: BaselineReport =
            serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(loaded.schema, BASELINE_SCHEMA);
        assert_eq!(loaded.build_ms, Some(1200));
    }
}
