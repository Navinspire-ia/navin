//! Behavioural verification of candidates. No formal solver pretends to
//! prove arbitrary code equivalent; instead the differential verifier
//! replays identical request vectors against the baseline and the candidate
//! and demands byte-identical observable behaviour.

pub mod differential;
pub mod invariants;

pub use differential::{capture, compare, discover_vectors, DiffOutcome, Fingerprint};
pub use invariants::{run_invariants, InvariantOutcome};
