---
name: sbom-attestation
description: Generate, score, diff, sign and verify software bills of materials and build attestations. Produces CycloneDX and SPDX SBOMs with syft, checks them against the NTIA minimum elements, scans them with grype or trivy, signs images and attestations with Cosign (key or keyless with Fulcio and Rekor), verifies SLSA v1.0 provenance and GitHub artifact attestations by digest, and drafts admission policies (Sigstore policy-controller, Kyverno verifyImages) and VEX statements. Use when the user asks to "generate an SBOM", "sign this image with cosign", "verify the provenance", "check SBOM quality", "compare two SBOMs", "set up keyless signing", or must prove supply-chain integrity for an audit (NTIA, CISA, EU CRA, SLSA). In French "generer un SBOM", "signer l'image avec cosign", "verifier la provenance", "attester le build".
metadata: {"navin":{"emoji":"📦","category":"security"}}
---

# SBOM and Attestation

## Overview

Produce and assess the evidence that tells what is inside a software artifact and how it was built: the SBOM (CycloneDX or SPDX), the Cosign signature, the in-toto attestations (SLSA provenance, SBOM, vulnerability scan, VEX) and the transparency-log entries behind them. The default posture is read-only and evidence-based: generate SBOMs into the workspace, verify signatures and attestations, score and diff documents, and report with the exact commands and outputs. Signing an artifact, pushing an attestation to a registry, or enabling an admission policy changes a target and happens only after the user approves the exact command and the identity it uses.

## When it applies

- A release pipeline must emit an SBOM, sign the image and attach provenance.
- An auditor, a customer or a regulator (US EO 14028 / NTIA, EU CRA, DORA ICT third-party risk) asks for the SBOM of a product and proof of its origin.
- A deployment must be gated on signature and provenance (policy-controller, Kyverno).
- Two builds must be compared: what changed between the SBOM of version N and N+1.
- A vulnerability must be scoped: which deployed artifacts contain the affected component, and which are not affected (VEX).
- The user asks which SLSA level a pipeline really reaches.

Out of scope: dependency vulnerability triage itself (`security-supply-chain`, `dependency-auditor`), secret scanning (`secrets-scanner`), container hardening (`security-containers`).

## Engines and sources

Install any missing CLI with the `toolbelt` skill. Registry, cloud KMS and Kubernetes commands run through the `infra` tool (level `observe` for `crane digest`, `cosign verify`, `kubectl get clusterimagepolicy`; `prepare` for a dry run; `execute` for `cosign sign`, `cosign attest`, `kubectl apply`). GitHub operations (attestations, workflow files, release assets) go through the `connectors` tool when the GitHub connector exists, else `gh`.

| Need | Command |
|---|---|
| Resolve the digest | `crane digest registry.example.com/app:1.4.2` or `skopeo inspect --format '{{.Digest}}' docker://registry.example.com/app:1.4.2` |
| SBOM of an image | `syft registry.example.com/app@sha256:<digest> --scope all-layers -o cyclonedx-json=sbom.cdx.json -o spdx-json=sbom.spdx.json` |
| SBOM of a source tree | `syft dir:. -o cyclonedx-json=sbom.cdx.json` (or `cdxgen -o sbom.cdx.json` for lockfile-aware builds) |
| Validate the schema | `cyclonedx validate --input-file sbom.cdx.json --input-format json --input-version v1_5 --fail-on-errors`; `pyspdxtools -i sbom.spdx.json` |
| Convert, merge, diff | `cyclonedx convert --input-file sbom.cdx.json --output-format xml`; `cyclonedx merge --input-files a.cdx.json b.cdx.json --output-file merged.cdx.json`; `cyclonedx diff old.cdx.json new.cdx.json --component-versions` |
| Scan the SBOM | `grype sbom:./sbom.cdx.json -o json --file grype.json`; `trivy sbom sbom.cdx.json --format json --output trivy.json`; `trivy sbom --vex vex.openvex.json sbom.cdx.json` |
| Sign | `cosign sign --key cosign.key <image@digest>` (key from `COSIGN_KEY` / KMS URI) or `cosign sign --yes <image@digest>` (keyless in CI) |
| Verify | `cosign verify --certificate-identity <workflow identity> --certificate-oidc-issuer <issuer> <image@digest>` or `cosign verify --key cosign.pub <image@digest>` |
| Attest | `cosign attest --predicate provenance.json --type slsaprovenance1 <image@digest>`; `cosign attest --predicate sbom.cdx.json --type cyclonedx <image@digest>` |
| Verify attestation | `cosign verify-attestation --type slsaprovenance1 --certificate-identity <id> --certificate-oidc-issuer <issuer> <image@digest> \| jq -r .payload \| base64 -d \| jq .` |
| Attach or fetch an SBOM | `cosign attach sbom --sbom sbom.cdx.json --type cyclonedx <image@digest>`; `cosign download sbom <image@digest>` |
| Inventory of artifacts | `cosign tree <image@digest>` |
| Transparency log | `rekor-cli search --sha sha256:<digest>`; `rekor-cli get --uuid <uuid> --format json` |
| GitHub attestations | `gh attestation verify oci://registry.example.com/app@sha256:<digest> --owner example-org --format json`; produced by `actions/attest-build-provenance@v2` and `actions/attest-sbom@v2` |
| SLSA builder check | `slsa-verifier verify-image <image@digest> --source-uri github.com/<org>/<repo> --source-tag v1.4.2` |
| VEX | `vexctl create --product pkg:oci/app@sha256:<digest> --vuln CVE-2024-1234 --status not_affected --justification vulnerable_code_not_in_execute_path` |
| Admission | `kubectl get clusterimagepolicy`, `kubectl get policyreport -A` (Kyverno) |

Tokens and keys (`COSIGN_PASSWORD`, `COSIGN_KEY`, `GITHUB_TOKEN`, KMS credentials) come from environment variables or the secret manager and are never printed or written into a report. Large outputs (a syft SBOM of a base image easily exceeds 5 MB) are written to a file and queried with the `grep` tool or `jq` before any part is read.

## Workflow

1. **Pin the subject.** Resolve the tag to a digest with `crane digest` and use `repo@sha256:...` in every later command. Record the digest in the report; a tag can move and makes every signature meaningless as evidence.
2. **Collect what exists.** Run `cosign tree <image@digest>` and `cosign download attestation` to list signatures, SBOMs and attestations already attached. Decode each DSSE payload and note its `predicateType`. For GitHub-built artifacts also run `gh attestation verify --format json`. Decide here whether the task is "verify existing" or "produce missing".
3. **Generate the SBOM when missing or stale.** Prefer a build-time SBOM from the pipeline (CISA type `build`); an analyzed SBOM from `syft <image>` is the fallback and is labelled as such. Produce CycloneDX JSON as the primary format; add SPDX JSON when the consumer asks for it (US federal buyers often do). Set `metadata.component` (CycloneDX) or the `DESCRIBES` relationship (SPDX) to the artifact and its digest.
4. **Validate and score.** Validate the schema, then run `python3 scripts/sbom_quality.py sbom.cdx.json --min-score 80`. Read `references/ntia-minimum-elements.md` to map each gap to the exact field to fill. Components without version or purl usually come from vendored or copied code; name the fix (declare the component in the manifest, add a `cyclonedx` plugin to the build) rather than editing the SBOM by hand.
5. **Compare with the previous release** when one exists: `python3 scripts/sbom_diff.py previous.cdx.json sbom.cdx.json --fail-on-removed`. Removed components and license changes (a permissive to copyleft switch, a `NOASSERTION` appearing) are the findings to escalate; version bumps are context for the vulnerability scan.
6. **Scan the SBOM, not the image again.** `grype sbom:./sbom.cdx.json` or `trivy sbom`. Apply an existing VEX document (`--vex`) so that known `not_affected` statements suppress noise. Record which scanner, which database date (`grype db status`).
7. **Verify provenance claims.** Follow `references/slsa-provenance.md`: subject digest, signing identity and issuer, `runDetails.builder.id`, `externalParameters.workflow.{repository,ref,path}`, `resolvedDependencies[].digest.gitCommit` against `git rev-parse <tag>^{commit}`, Rekor entry. State the SLSA Build level actually demonstrated (L1 provenance exists, L2 signed by the hosted platform and verified, L3 isolated trusted builder), not the one the pipeline claims.
8. **Sign and attest (write step, needs approval).** Present the exact `cosign sign` / `cosign attest` commands with the identity that will appear in the certificate; run them through `infra level=execute` only after approval. Keyless in CI needs `permissions: id-token: write`; a key-based flow needs the key in KMS or a CI secret, never in the repository. Attach the SBOM as an attestation (`--type cyclonedx`) rather than `cosign attach sbom`, which is unsigned.
9. **Gate deployments.** Draft a `ClusterImagePolicy` or a Kyverno `verifyImages` rule from `references/cosign-cheatsheet.md` with the identity regexp limited to the repository and workflow path. Start in audit mode (`policy.sigstore.dev/include` on one namespace, Kyverno `validationFailureAction: Audit`), read the policy reports for one release cycle, then propose `Enforce`.
10. **Write VEX for not-affected findings** with `vexctl` (OpenVEX) or a CSAF VEX document when the consumer requires CSAF; each statement carries `status`, `justification` (one of the five OpenVEX values) and `impact_statement`, and is attested with `cosign attest --type openvex`.
11. **Deliver** the report described below, with every verification output kept as a file in the workspace. Use `studio-html-report` when the user wants an HTML report.

## Rules

- Sign and verify by digest, never by tag. A `cosign verify` on a tag resolves the digest at verification time, so the report must still print the digest that was checked.
- `--certificate-identity` and `--certificate-oidc-issuer` are mandatory for keyless verification in Cosign 2.x; a regexp that matches any repository of the organisation is a finding, not a configuration.
- `--insecure-ignore-tlog`, `--insecure-ignore-sct` and `--allow-insecure-registry` invalidate the verification as evidence; a report that depends on them says so in the first line.
- `COSIGN_EXPERIMENTAL=1` is obsolete since Cosign 2.0; remove it from pipelines instead of documenting it.
- For `slsa-github-generator` builds, the signing identity is the generator's reusable workflow, not the project workflow. A verification pinned to the project workflow fails for the right reason.
- An SBOM from `syft <image>` after the build is an **analyzed** SBOM: it misses statically linked Go or Rust code without build info and treats vendored copies as unknown. State the CISA SBOM type in every report.
- CycloneDX `metadata.tools` is an object with `components[]` since 1.5 and an array of tool objects in 1.4; `sbom_quality.py` reads both. CycloneDX 1.6 renamed `metadata.manufacture` to `metadata.manufacturer`.
- SPDX `supplier: NOASSERTION` and `licenseDeclared: NOASSERTION` count as missing; `originator` is not the supplier.
- `cosign attach sbom` stores the SBOM unsigned; `cosign download sbom` returns whatever is in the registry. Trust only SBOMs retrieved with `cosign verify-attestation --type cyclonedx` or `--type spdxjson`.
- Fulcio certificates are valid for 10 minutes; verification relies on the Rekor signed entry timestamp. Private GitHub repositories use GitHub's Sigstore instance, so `rekor-cli search` on the public log returns nothing and that is expected.
- NTIA minimum elements are a floor: `sbom_quality.py` scores seven elements equally; CISA 2024 baseline attributes (license, hash, generation context) are reported separately and are what BSI TR-03183-2 and most customers ask for.
- EU CRA (Regulation 2024/2847, Annex I Part II point 1) requires an SBOM "covering at the very least the top-level dependencies" in a machine-readable format as part of the technical documentation; it does not require publication. Do not promise more than the text.
- VEX statements are claims by the product owner and need an owner and a date; `not_affected` without one of the OpenVEX justifications is rejected by `vexctl` and by `trivy --vex`.
- Never write secrets, decoded private keys or OIDC tokens into the report, even redacted; cite the variable name only.

## Deliverable

A Markdown report (HTML with `studio-html-report` on request) with these sections and tables:

**Subject**: image or file, digest, registry, build run URL, CISA SBOM type.

**Inventory of attached artifacts** (from `cosign tree`):

| Kind | predicateType or type | Signer identity | Issuer | Rekor log index | Verified |
|---|---|---|---|---|---|

**SBOM quality** (from `sbom_quality.py`):

| NTIA element | Coverage % | Gap count | Fix |
|---|---|---|---|

**SBOM diff versus previous release** (from `sbom_diff.py`):

| Change | Component | Old | New | Risk note |
|---|---|---|---|---|

**Provenance verification**:

| Claim | Expected | Observed | Status | Evidence file |
|---|---|---|---|---|

Rows: subject digest, signing identity, OIDC issuer, builder id, build type, source repository and ref, source commit, Rekor inclusion, demonstrated SLSA level.

**Vulnerabilities in the SBOM** (grype or trivy, VEX applied):

| CVE | Component | Version | Severity | Fixed in | VEX status |
|---|---|---|---|---|---|

**Actions** (write operations proposed, none executed without approval):

| Action | Command | Identity or key | Approval |
|---|---|---|---|

Evidence files kept in the workspace: `sbom.cdx.json`, `sbom.spdx.json`, `cosign-verify.json`, `provenance.json` (decoded), `rekor-entry.json`, `grype.json`, the Markdown output of both scripts.

## Resources

- `scripts/sbom_quality.py` - score a CycloneDX or SPDX JSON SBOM against the NTIA minimum elements, list components missing version, purl, license, supplier, duplicates, dependency graph presence; `--json`, `--min-score` (exit 1 below). Test run: `python3 scripts/sbom_quality.py examples/payments-api-v1.cdx.json --min-score 70`.
- `scripts/sbom_diff.py` - diff two SBOMs of either format: added, removed, version-changed components, license changes; `--json`, `--fail-on-removed`, `--fail-on-change`. Test run: `python3 scripts/sbom_diff.py examples/payments-api-v1.cdx.json examples/payments-api-v2.spdx.json --fail-on-removed` (exits 1 because one component was removed).
- `examples/payments-api-v1.cdx.json` - CycloneDX 1.5 sample with a component missing version and purl, a duplicate, and a dependency graph; use it to see the quality report shape.
- `examples/payments-api-v2.spdx.json` - SPDX 2.3 sample of the next release; use it with the first file to see a cross-format diff.
- `references/cosign-cheatsheet.md` - key and keyless signing, verify flags per OIDC issuer, attestation types and predicateType URIs, Rekor queries, blob signing, policy-controller and Kyverno policies. Open it at steps 2, 8 and 9.
- `references/slsa-provenance.md` - SLSA v1.0 Build levels, in-toto Statement fields, provenance predicate field by field, how to verify each claim, GitHub artifact attestations, evidence per level. Open it at step 7.
- `references/ntia-minimum-elements.md` - each NTIA element with its CycloneDX and SPDX field names, CISA baseline attributes, validators, CISA SBOM types, EU CRA expectations. Open it at steps 3 and 4.
