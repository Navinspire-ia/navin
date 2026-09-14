//! Promote & Certify: the final "Evolve -> Certify" stage. It takes an
//! accepted fix proposal, issues a proof certificate, and - only within the
//! bounds of `.navin/evolve.toml` - lands the change on a dedicated git
//! branch (optionally fast-forward merged), recording everything so it can
//! be rolled back with one command. Safe mode never merges automatically.

pub mod certify;
pub mod engine;
pub mod git;
pub mod identity;
pub mod model;
pub mod policy;
pub mod publish;

pub use engine::{list, merge, promote, rollback};
pub use publish::publish;
pub use model::{Certificate, PromotionOutcome, PromotionRecord};
pub use policy::{decide, PolicyDecision};
