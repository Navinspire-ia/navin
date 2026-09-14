//! The promotion policy gate. It maps `.navin/evolve.toml` plus the
//! candidate's family and its certificate onto a decision: block, create a
//! branch for manual review, or merge into the active branch. Safe mode is
//! the default and never merges.

use crate::policy::config::{AllowedFamilies, EvolveConfig};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PolicyDecision {
    /// Do not touch the workspace at all.
    Blocked(String),
    /// Create the branch + commit, but do not merge.
    BranchOnly(String),
    /// Create the branch + commit and merge it into the active branch.
    Merge(String),
}

pub fn decide(config: &EvolveConfig, family: &str, certificate_valid: bool) -> PolicyDecision {
    if !config.evolve.enabled {
        return PolicyDecision::Blocked(
            "evolve is disabled in policy ([evolve] enabled = false)".to_owned(),
        );
    }
    if !family_allowed(&config.evolve.allowed, family) {
        return PolicyDecision::Blocked(format!(
            "family `{family}` is not allowed by policy ([evolve.allowed])"
        ));
    }
    if !certificate_valid {
        return PolicyDecision::Blocked(
            "the fix certificate is not valid (proof did not pass or did not improve)".to_owned(),
        );
    }

    match config.evolve.mode.as_str() {
        "autonomous" => PolicyDecision::Merge("autonomous mode: merging automatically".to_owned()),
        "trusted" => {
            if config.evolve.promotion.auto_merge {
                PolicyDecision::Merge("trusted mode with auto_merge enabled".to_owned())
            } else {
                PolicyDecision::BranchOnly(
                    "trusted mode without auto_merge: branch left for review".to_owned(),
                )
            }
        }
        // "safe" and anything unrecognised default to the safest behaviour.
        _ => PolicyDecision::BranchOnly(
            "safe mode: branch created for manual review, not merged".to_owned(),
        ),
    }
}

fn family_allowed(allowed: &AllowedFamilies, family: &str) -> bool {
    match family {
        "performance" => allowed.performance,
        "memory" => allowed.memory,
        "database" => allowed.database,
        "reliability" => allowed.reliability,
        "concurrency" => allowed.concurrency,
        "security" => allowed.security,
        "dependencies" => allowed.dependencies,
        // Unknown families are never allowed by default.
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn enabled(mode: &str, auto_merge: bool) -> EvolveConfig {
        let mut c = EvolveConfig::default();
        c.evolve.enabled = true;
        c.evolve.mode = mode.to_owned();
        c.evolve.promotion.auto_merge = auto_merge;
        c
    }

    #[test]
    fn disabled_policy_blocks_everything() {
        let mut c = EvolveConfig::default();
        c.evolve.enabled = false;
        assert!(matches!(decide(&c, "reliability", true), PolicyDecision::Blocked(_)));
    }

    #[test]
    fn the_default_policy_is_branch_only() {
        // Enabled out of the box, but safe mode never merges.
        let c = EvolveConfig::default();
        assert!(matches!(decide(&c, "reliability", true), PolicyDecision::BranchOnly(_)));
    }

    #[test]
    fn safe_mode_is_branch_only_even_when_valid() {
        let c = enabled("safe", true);
        assert!(matches!(decide(&c, "reliability", true), PolicyDecision::BranchOnly(_)));
    }

    #[test]
    fn trusted_needs_auto_merge_to_merge() {
        assert!(matches!(
            decide(&enabled("trusted", false), "reliability", true),
            PolicyDecision::BranchOnly(_)
        ));
        assert!(matches!(
            decide(&enabled("trusted", true), "reliability", true),
            PolicyDecision::Merge(_)
        ));
    }

    #[test]
    fn autonomous_merges_allowed_families() {
        assert!(matches!(
            decide(&enabled("autonomous", false), "reliability", true),
            PolicyDecision::Merge(_)
        ));
    }

    #[test]
    fn disallowed_family_is_blocked() {
        // security is off by default even when enabled.
        assert!(matches!(
            decide(&enabled("autonomous", true), "security", true),
            PolicyDecision::Blocked(_)
        ));
    }

    #[test]
    fn invalid_certificate_is_blocked() {
        assert!(matches!(
            decide(&enabled("autonomous", true), "reliability", false),
            PolicyDecision::Blocked(_)
        ));
    }
}
