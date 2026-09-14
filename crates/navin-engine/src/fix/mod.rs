//! Fix engine: the "Fix -> Prove" stage. Given a diagnosed finding and one
//! or more candidate patches, it applies each candidate in an isolated
//! shadow, re-proves it against the same fault battery, and promotes a
//! candidate only if it measurably helped without regressing. An accepted
//! candidate becomes a proposal under `.navin/fixes/`; the workspace is
//! never modified here (that is a separate, explicit promotion step).

pub mod diff;
pub mod engine;
pub mod gate;
pub mod generator;
pub mod model;
pub mod patch;

pub use engine::{run_fix, FixContext};
pub use gate::GateConfig;
pub use generator::{BridgeGenerator, FixGenerator, ProvidedPatchGenerator};
pub use model::{Decision, FixCandidate, FixPatch, FixReport};
