//! `.navin/evolve.toml` with safe defaults.
//!
//! Safe mode is the default: promotions land on their own branch (or in a
//! patch bundle without git), nothing is auto-merged, destructive families
//! are off, and resource ceilings are conservative. A missing file means
//! defaults; a broken file is an error rather than silently permissive.

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::Path;

use crate::NAVIN_DIR;

pub const CONFIG_FILE: &str = "evolve.toml";

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct EvolveConfig {
    pub proof: ProofSection,
    pub evolve: EvolveSection,
    /// What the engine measures and how it talks to it. Everything here is
    /// optional: without it the target is an HTTP service probed with one
    /// GET on the discovered URL, exactly as before.
    pub target: TargetSection,
    /// Business invariants: commands that must exit 0 for a candidate to be
    /// promotable. They run inside the shadow, after the test suite.
    ///
    /// ```toml
    /// [[invariants]]
    /// name = "no_duplicate_payments"
    /// command = "python verify_payments.py"
    /// ```
    pub invariants: Vec<InvariantSpec>,
    /// Project-specific log signatures, matched next to the built-in
    /// catalogue during diagnosis. A signature the built-ins do not know
    /// stops being invisible the day you declare it here.
    ///
    /// ```toml
    /// [[signatures]]
    /// marker = "circuit breaker open"
    /// id = "breaker_open"
    /// family = "reliability"
    /// cause = "the payment circuit breaker tripped under load"
    /// ```
    pub signatures: Vec<SignatureSpec>,
}

/// How to reach and exercise the application under test.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct TargetSection {
    /// "http" (default): a service answering on a local port.
    /// "worker": a long-running process with no port (queue consumer, CLI
    /// daemon, cron worker). Health is process liveness plus `health_cmd`.
    pub kind: String,
    /// Extra URL paths probed alongside the target URL, so load and
    /// benchmarks exercise more than one route.
    pub probe_paths: Vec<String>,
    /// Headers added to every probe (e.g. an Authorization token for an
    /// authenticated API). Host, Connection and Content-Length are managed
    /// by the prober and cannot be overridden.
    pub probe_headers: BTreeMap<String, String>,
    /// HTTP method for the probes; empty means GET.
    pub probe_method: String,
    /// Request body sent with every probe (POST/PUT payloads).
    pub probe_body: String,
    /// Worker targets: a command whose exit 0 means "healthy".
    pub health_cmd: String,
    /// Worker targets: one unit of work. Load and benchmarks run it
    /// concurrently and time each invocation, which is what makes a CLI
    /// or a port-less worker measurable.
    pub exercise_cmd: String,
}

impl Default for TargetSection {
    fn default() -> Self {
        TargetSection {
            kind: "http".to_owned(),
            probe_paths: Vec::new(),
            probe_headers: BTreeMap::new(),
            probe_method: String::new(),
            probe_body: String::new(),
            health_cmd: String::new(),
            exercise_cmd: String::new(),
        }
    }
}

impl TargetSection {
    pub fn is_worker(&self) -> bool {
        self.kind.eq_ignore_ascii_case("worker")
    }
}

/// A project-declared log signature (matched as a lowercased substring,
/// like the built-in catalogue: predictable and dependency-free).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignatureSpec {
    /// Substring searched for in each lowercased log line.
    pub marker: String,
    /// Stable slug for the finding id (`log.<id>`).
    pub id: String,
    #[serde(default = "default_signature_family")]
    pub family: String,
    /// The root cause this signature points at, in one sentence.
    pub cause: String,
}

fn default_signature_family() -> String {
    "reliability".to_owned()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InvariantSpec {
    pub name: String,
    pub command: String,
    #[serde(default = "default_invariant_timeout")]
    pub timeout_secs: u64,
}

fn default_invariant_timeout() -> u64 {
    120
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct ProofSection {
    pub enabled: bool,
    /// quick | standard | deep | nightly
    pub profile: String,
}

impl Default for ProofSection {
    fn default() -> Self {
        ProofSection { enabled: true, profile: "standard".to_owned() }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct EvolveSection {
    pub enabled: bool,
    /// safe | trusted | autonomous. Safe is the public default.
    pub mode: String,
    pub allowed: AllowedFamilies,
    pub promotion: PromotionSection,
    pub resources: ResourceLimits,
    pub budget: BudgetSection,
    pub generator: GeneratorSection,
}

impl Default for EvolveSection {
    fn default() -> Self {
        EvolveSection {
            // Enabled out of the box: in safe mode a promotion only ever
            // creates a branch (or a patch bundle), never merges, so the
            // default is useful without being destructive. `enabled = false`
            // turns the engine back into a pure measuring instrument.
            enabled: true,
            mode: "safe".to_owned(),
            allowed: AllowedFamilies::default(),
            promotion: PromotionSection::default(),
            resources: ResourceLimits::default(),
            budget: BudgetSection::default(),
            generator: GeneratorSection::default(),
        }
    }
}

/// How candidate fixes are synthesised. The engine never calls an LLM
/// itself; it shells out to an external bridge (provided by the desktop
/// app) that receives a finding on stdin and returns candidates on stdout.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct GeneratorSection {
    /// Command line of the bridge, e.g. "python3 -m navin.evolve.bridge".
    /// When empty, no candidates are generated (proof/diagnose only).
    pub command: String,
    /// Hard timeout for one bridge invocation.
    pub timeout_secs: u64,
}

impl Default for GeneratorSection {
    fn default() -> Self {
        GeneratorSection { command: String::new(), timeout_secs: 120 }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct AllowedFamilies {
    pub performance: bool,
    pub memory: bool,
    pub database: bool,
    pub reliability: bool,
    pub concurrency: bool,
    pub security: bool,
    pub dependencies: bool,
}

impl Default for AllowedFamilies {
    fn default() -> Self {
        AllowedFamilies {
            performance: true,
            memory: true,
            database: true,
            reliability: true,
            concurrency: false,
            // Off by default: these change externally visible behavior.
            security: false,
            dependencies: false,
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct PromotionSection {
    /// Merging someone else's branch is never automatic unless asked for.
    pub auto_merge: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct ResourceLimits {
    pub max_cpu_percent: u8,
    pub max_memory_mb: u64,
    pub max_disk_mb: u64,
    pub max_runtime_minutes: u64,
}

impl Default for ResourceLimits {
    fn default() -> Self {
        ResourceLimits {
            max_cpu_percent: 15,
            max_memory_mb: 512,
            max_disk_mb: 4096,
            max_runtime_minutes: 30,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct BudgetSection {
    pub max_candidates: u32,
    pub max_runtime_minutes: u64,
    pub max_llm_cost_usd: f64,
}

impl Default for BudgetSection {
    fn default() -> Self {
        BudgetSection {
            max_candidates: 100,
            max_runtime_minutes: 30,
            max_llm_cost_usd: 2.0,
        }
    }
}

impl EvolveConfig {
    /// Load from `<root>/.navin/evolve.toml`; defaults when absent.
    pub fn load(project_root: &Path) -> Result<Self> {
        let path = project_root.join(NAVIN_DIR).join(CONFIG_FILE);
        if !path.is_file() {
            return Ok(EvolveConfig::default());
        }
        let text = std::fs::read_to_string(&path)
            .with_context(|| format!("cannot read {}", path.display()))?;
        toml::from_str(&text).with_context(|| format!("invalid {}", path.display()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_are_safe() {
        let config = EvolveConfig::default();
        // Enabled by default, but safe mode never merges anything.
        assert!(config.evolve.enabled);
        assert_eq!(config.evolve.mode, "safe");
        assert!(!config.evolve.promotion.auto_merge);
        assert!(!config.evolve.allowed.security);
        assert!(!config.evolve.allowed.dependencies);
        assert_eq!(config.evolve.resources.max_cpu_percent, 15);
        assert_eq!(config.target.kind, "http");
        assert!(!config.target.is_worker());
        assert!(config.signatures.is_empty());
    }

    #[test]
    fn target_section_is_parsed() {
        let tmp = tempfile::tempdir().unwrap();
        let navin = tmp.path().join(NAVIN_DIR);
        std::fs::create_dir_all(&navin).unwrap();
        std::fs::write(
            navin.join(CONFIG_FILE),
            "[target]\nkind = \"worker\"\nhealth_cmd = \"redis-cli ping\"\n\
             exercise_cmd = \"python worker_job.py\"\n\
             probe_paths = [\"/health\", \"/api/items\"]\n\
             probe_method = \"POST\"\nprobe_body = \"{}\"\n\
             [target.probe_headers]\nAuthorization = \"Bearer token\"\n",
        )
        .unwrap();

        let config = EvolveConfig::load(tmp.path()).unwrap();
        assert!(config.target.is_worker());
        assert_eq!(config.target.health_cmd, "redis-cli ping");
        assert_eq!(config.target.probe_paths, vec!["/health", "/api/items"]);
        assert_eq!(config.target.probe_method, "POST");
        assert_eq!(
            config.target.probe_headers.get("Authorization").map(String::as_str),
            Some("Bearer token")
        );
    }

    #[test]
    fn custom_signatures_are_parsed_with_a_default_family() {
        let tmp = tempfile::tempdir().unwrap();
        let navin = tmp.path().join(NAVIN_DIR);
        std::fs::create_dir_all(&navin).unwrap();
        std::fs::write(
            navin.join(CONFIG_FILE),
            "[[signatures]]\nmarker = \"circuit breaker open\"\nid = \"breaker\"\n\
             cause = \"the breaker tripped\"\n",
        )
        .unwrap();

        let config = EvolveConfig::load(tmp.path()).unwrap();
        assert_eq!(config.signatures.len(), 1);
        assert_eq!(config.signatures[0].id, "breaker");
        assert_eq!(config.signatures[0].family, "reliability");
    }

    #[test]
    fn partial_file_keeps_defaults_for_the_rest() {
        let tmp = tempfile::tempdir().unwrap();
        let navin = tmp.path().join(NAVIN_DIR);
        std::fs::create_dir_all(&navin).unwrap();
        std::fs::write(
            navin.join(CONFIG_FILE),
            "[proof]\nprofile = \"quick\"\n\n[evolve.resources]\nmax_memory_mb = 1024\n",
        )
        .unwrap();

        let config = EvolveConfig::load(tmp.path()).unwrap();
        assert_eq!(config.proof.profile, "quick");
        assert_eq!(config.evolve.resources.max_memory_mb, 1024);
        // Untouched sections keep their safe defaults.
        assert_eq!(config.evolve.mode, "safe");
        assert_eq!(config.evolve.resources.max_cpu_percent, 15);
    }

    #[test]
    fn invariants_are_parsed_with_a_default_timeout() {
        let tmp = tempfile::tempdir().unwrap();
        let navin = tmp.path().join(NAVIN_DIR);
        std::fs::create_dir_all(&navin).unwrap();
        std::fs::write(
            navin.join(CONFIG_FILE),
            "[[invariants]]\nname = \"orders\"\ncommand = \"python check.py\"\n\n\
             [[invariants]]\nname = \"payments\"\ncommand = \"sh pay.sh\"\ntimeout_secs = 30\n",
        )
        .unwrap();

        let config = EvolveConfig::load(tmp.path()).unwrap();
        assert_eq!(config.invariants.len(), 2);
        assert_eq!(config.invariants[0].name, "orders");
        assert_eq!(config.invariants[0].timeout_secs, 120);
        assert_eq!(config.invariants[1].timeout_secs, 30);
    }

    #[test]
    fn broken_file_is_an_error_not_permissive_defaults() {
        let tmp = tempfile::tempdir().unwrap();
        let navin = tmp.path().join(NAVIN_DIR);
        std::fs::create_dir_all(&navin).unwrap();
        std::fs::write(navin.join(CONFIG_FILE), "not [ valid toml").unwrap();
        assert!(EvolveConfig::load(tmp.path()).is_err());
    }
}
