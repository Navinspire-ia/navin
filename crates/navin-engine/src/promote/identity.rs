//! Engine signing identity. Each workspace gets an Ed25519 keypair on
//! first use, stored under `.navin/evolve/identity.ed25519` (owner-only on
//! Unix). Certificates are signed with it so a third party holding the
//! public key can check that the evidence was produced by this engine and
//! not edited afterwards.

use anyhow::{anyhow, Context, Result};
use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};
use std::path::{Path, PathBuf};

use super::model::Certificate;

pub fn identity_path(project_root: &Path) -> PathBuf {
    crate::engine_dir(project_root).join("identity.ed25519")
}

/// Load the workspace signing key, creating it on first use.
pub fn load_or_create(project_root: &Path) -> Result<SigningKey> {
    let path = identity_path(project_root);
    if path.is_file() {
        let text = std::fs::read_to_string(&path)
            .with_context(|| format!("cannot read {}", path.display()))?;
        let bytes = decode_hex(text.trim())
            .with_context(|| format!("{} is not valid hex", path.display()))?;
        let seed: [u8; 32] = bytes
            .as_slice()
            .try_into()
            .map_err(|_| anyhow!("{} must hold exactly 32 bytes", path.display()))?;
        return Ok(SigningKey::from_bytes(&seed));
    }

    let mut seed = [0u8; 32];
    getrandom::getrandom(&mut seed).map_err(|e| anyhow!("no system entropy: {e}"))?;
    let key = SigningKey::from_bytes(&seed);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("cannot create {}", parent.display()))?;
    }
    std::fs::write(&path, encode_hex(&seed))
        .with_context(|| format!("cannot write {}", path.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }
    Ok(key)
}

/// The exact bytes a certificate signature covers: every payload field in
/// a fixed order, newline-separated. Changing any field breaks the
/// signature; the signature and public key themselves are excluded.
pub fn signing_message(cert: &Certificate) -> Vec<u8> {
    format!(
        "{}\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n{:?}\n{}\n{}\n{}",
        cert.schema,
        cert.engine_version,
        cert.finding,
        cert.candidate_id,
        cert.family,
        cert.commit_before,
        cert.score_before,
        cert.score_after,
        cert.verdict_after,
        cert.resolved_target,
        cert.issued_at,
        cert.checksum,
    )
    .into_bytes()
}

/// Sign `cert` in place with the workspace identity.
pub fn sign(project_root: &Path, cert: &mut Certificate) -> Result<()> {
    let key = load_or_create(project_root)?;
    let signature = key.sign(&signing_message(cert));
    cert.signature = encode_hex(&signature.to_bytes());
    cert.public_key = encode_hex(key.verifying_key().as_bytes());
    Ok(())
}

/// Check the Ed25519 signature embedded in a certificate. Unsigned or
/// malformed certificates simply fail the check; this never panics.
pub fn verify(cert: &Certificate) -> bool {
    let Ok(pk_bytes) = decode_hex(&cert.public_key) else {
        return false;
    };
    let Ok(pk_arr) = <[u8; 32]>::try_from(pk_bytes.as_slice()) else {
        return false;
    };
    let Ok(verifying_key) = VerifyingKey::from_bytes(&pk_arr) else {
        return false;
    };
    let Ok(sig_bytes) = decode_hex(&cert.signature) else {
        return false;
    };
    let Ok(sig_arr) = <[u8; 64]>::try_from(sig_bytes.as_slice()) else {
        return false;
    };
    let signature = Signature::from_bytes(&sig_arr);
    verifying_key.verify(&signing_message(cert), &signature).is_ok()
}

pub fn encode_hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

pub fn decode_hex(text: &str) -> Result<Vec<u8>> {
    anyhow::ensure!(text.len().is_multiple_of(2), "odd-length hex string");
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&text[i..i + 2], 16).context("invalid hex digit"))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::proof::Verdict;
    use crate::promote::model::{compute_checksum, CERTIFICATE_SCHEMA};

    fn cert() -> Certificate {
        let mut c = Certificate {
            schema: CERTIFICATE_SCHEMA.to_owned(),
            engine_version: "0.1.0".to_owned(),
            finding: "crash.load".to_owned(),
            candidate_id: "cand-1".to_owned(),
            family: "reliability".to_owned(),
            commit_before: "abc".to_owned(),
            score_before: 50,
            score_after: 100,
            verdict_after: Verdict::Pass,
            resolved_target: true,
            issued_at: "epoch:1".to_owned(),
            checksum: String::new(),
            signature: String::new(),
            public_key: String::new(),
        };
        c.checksum = compute_checksum(&c);
        c
    }

    #[test]
    fn hex_round_trip() {
        let bytes = [0u8, 1, 15, 16, 255];
        assert_eq!(decode_hex(&encode_hex(&bytes)).unwrap(), bytes);
        assert!(decode_hex("abc").is_err());
        assert!(decode_hex("zz").is_err());
    }

    #[test]
    fn sign_then_verify_round_trip() {
        let tmp = tempfile::tempdir().unwrap();
        let mut c = cert();
        sign(tmp.path(), &mut c).unwrap();
        assert!(verify(&c));
    }

    #[test]
    fn tampering_breaks_the_signature() {
        let tmp = tempfile::tempdir().unwrap();
        let mut c = cert();
        sign(tmp.path(), &mut c).unwrap();
        c.score_after = 10; // edited after signing
        assert!(!verify(&c));
    }

    #[test]
    fn unsigned_certificates_fail_verification() {
        assert!(!verify(&cert()));
    }

    #[test]
    fn the_identity_is_stable_across_loads() {
        let tmp = tempfile::tempdir().unwrap();
        let first = load_or_create(tmp.path()).unwrap();
        let second = load_or_create(tmp.path()).unwrap();
        assert_eq!(first.verifying_key(), second.verifying_key());
    }
}
