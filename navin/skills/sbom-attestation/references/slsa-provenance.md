# SLSA v1.0 provenance: levels, predicate, verification

SLSA (Supply-chain Levels for Software Artifacts) v1.0 defines one track, **Build**, with three levels. Each level is a claim about how the artifact was produced; the provenance attestation is the evidence for the claim.

## Build levels

| Level | Requirement | What it protects against | Typical implementation |
|---|---|---|---|
| L0 | none | - | ad hoc build on a laptop |
| L1 | provenance exists: the build platform generates a provenance document that describes how the artifact was built (builder, source, parameters) | mistakes, documentation gaps | `actions/attest-build-provenance`, `slsa-github-generator`, GitLab `artifacts:reports` + `glab` |
| L2 | provenance is signed by the hosted build platform; the consumer verifies the signature; the build runs on hosted infrastructure, not a developer machine | tampering after the build, forged provenance | GitHub-hosted runners + Sigstore keyless, Google Cloud Build provenance, GitLab runner attestations |
| L3 | the build platform prevents runs from influencing one another and keeps the signing key out of reach of the user-defined build steps (isolated, ephemeral builders; secret material not accessible to the job) | compromised build steps, cross-build secret theft | `slsa-framework/slsa-github-generator` reusable workflows, Tekton Chains with isolated signing, Google Cloud Build |

L3 cannot be claimed with a plain `cosign attest` executed inside the job that also runs the build: the job could forge the predicate. Only a trusted builder running in a separate context (a reusable workflow whose identity is the signer) gives L3.

## in-toto Statement envelope

Every attestation is an in-toto Statement wrapped in a DSSE envelope:

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [
    {"name": "registry.example.com/payments-api", "digest": {"sha256": "9f2b1c0e..."}}
  ],
  "predicateType": "https://slsa.dev/provenance/v1",
  "predicate": { }
}
```

| Field | Rule |
|---|---|
| `_type` | `https://in-toto.io/Statement/v1` (v0.1 used `https://in-toto.io/Statement/v0.1`) |
| `subject[].digest.sha256` | must equal the digest of the artifact being verified; a tag is never a subject |
| `subject[].name` | informational; the digest is authoritative |
| `predicateType` | URI selecting the predicate schema |
| `predicate` | schema defined by `predicateType` |

## SLSA v1.0 provenance predicate

```json
{
  "buildDefinition": {
    "buildType": "https://actions.github.io/buildtypes/workflow/v1",
    "externalParameters": {
      "workflow": {
        "ref": "refs/tags/v1.4.2",
        "repository": "https://github.com/example-org/payments-api",
        "path": ".github/workflows/release.yml"
      }
    },
    "internalParameters": {"github": {"event_name": "push", "repository_id": "123456", "repository_owner_id": "7890"}},
    "resolvedDependencies": [
      {"uri": "git+https://github.com/example-org/payments-api@refs/tags/v1.4.2", "digest": {"gitCommit": "4c1d..."}}
    ]
  },
  "runDetails": {
    "builder": {"id": "https://github.com/actions/runner/github-hosted"},
    "metadata": {
      "invocationId": "https://github.com/example-org/payments-api/actions/runs/987654/attempts/1",
      "startedOn": "2026-03-09T09:58:12Z",
      "finishedOn": "2026-03-09T10:02:00Z"
    },
    "byproducts": []
  }
}
```

| Field | Meaning | How to check it |
|---|---|---|
| `buildDefinition.buildType` | URI of the build process template; defines the meaning of the parameters | compare against the allow-list of accepted build types; an unknown type cannot be evaluated |
| `buildDefinition.externalParameters` | every input controlled by the user of the build platform (workflow path, ref, inputs) | `repository` and `path` must be the expected source and workflow; `ref` must be the released tag or protected branch |
| `buildDefinition.internalParameters` | values set by the platform, not by the user | `repository_id` and `repository_owner_id` detect a renamed or re-created repository |
| `buildDefinition.resolvedDependencies` | artifacts fetched during the build: source commit, base images, action versions | the `gitCommit` digest must equal the tag's commit (`git rev-parse v1.4.2^{commit}`) |
| `runDetails.builder.id` | URI of the trusted builder; the signer identity must belong to this builder | pin exactly; this is what gives L2/L3 |
| `runDetails.builder.version` | builder version map | optional; useful to prove the fixed version after a builder CVE |
| `runDetails.metadata.invocationId` | link to the run | open the run; the job must have produced the subject digest |
| `runDetails.metadata.startedOn` / `finishedOn` | RFC 3339 timestamps | the image push time in the registry must fall after `startedOn` |
| `runDetails.byproducts` | logs, intermediate artifacts | optional |

v0.2 predicate (`https://slsa.dev/provenance/v0.2`) uses `builder.id`, `buildType`, `invocation.configSource.{uri,digest,entryPoint}`, `invocation.parameters`, `metadata.buildInvocationId`, `metadata.completeness`, `materials[]`. `slsa-github-generator` v1.x still emits v0.2; v2.x emits v1.0.

## Verifying each claim

1. **Subject matches**: `subject[0].digest.sha256` equals the digest fetched with `crane digest`. Cosign checks this automatically; `gh attestation verify` does too.
2. **Signature and identity**: `cosign verify-attestation --type slsaprovenance1 --certificate-identity <builder workflow identity> --certificate-oidc-issuer <issuer>`. For `slsa-github-generator`, the identity is `https://github.com/slsa-framework/slsa-github-generator/.github/workflows/generator_container_slsa3.yml@refs/tags/vX.Y.Z`, not the calling repository.
3. **Builder id**: equals the identity accepted in step 2. A predicate whose `builder.id` differs from the signing identity is a forgery attempt.
4. **Source**: `externalParameters.workflow.repository` is the expected repository, `ref` is the released tag, `resolvedDependencies[].digest.gitCommit` equals the commit of that tag.
5. **Build type** is on the allow-list.
6. **Transparency**: the entry exists in Rekor (`rekor-cli search --sha sha256:<digest>`) with `integratedTime` consistent with `finishedOn`.
7. **Freshness**: the certificate was valid at signing time (Fulcio certificates live 10 minutes; Cosign validates against the Rekor SET, not the current clock).

Use `slsa-verifier` when the builder is `slsa-github-generator`:

```bash
slsa-verifier verify-image "$IMAGE" \
  --source-uri github.com/example-org/payments-api \
  --source-tag v1.4.2 \
  --builder-id https://github.com/slsa-framework/slsa-github-generator/.github/workflows/generator_container_slsa3.yml
```

## GitHub artifact attestations

Produced in a workflow with `permissions: {id-token: write, attestations: write, contents: read}`:

```yaml
- uses: actions/attest-build-provenance@v2
  with:
    subject-name: registry.example.com/payments-api
    subject-digest: ${{ steps.build.outputs.digest }}
    push-to-registry: true
- uses: actions/attest-sbom@v2
  with:
    subject-name: registry.example.com/payments-api
    subject-digest: ${{ steps.build.outputs.digest }}
    sbom-path: sbom.cdx.json
    push-to-registry: true
```

Verification:

```bash
gh attestation verify oci://registry.example.com/payments-api@sha256:<digest> --owner example-org
gh attestation verify release.tar.gz --repo example-org/payments-api --signer-workflow example-org/payments-api/.github/workflows/release.yml
gh attestation verify release.tar.gz --owner example-org --predicate-type https://spdx.dev/Document --format json
```

`gh attestation verify` checks the Sigstore bundle stored by GitHub (public repositories: public Sigstore instance and Rekor; private repositories: GitHub's own Sigstore instance, no public Rekor entry). `--owner` accepts any repository of the organisation; `--repo` is stricter. Output `--format json` is the evidence to keep.

## What each level requires as evidence in the report

| Claimed level | Evidence to attach |
|---|---|
| L1 | the decoded provenance predicate with `buildType`, `externalParameters`, `builder.id` |
| L2 | L1 plus the `cosign verify-attestation` (or `gh attestation verify --format json`) output showing identity, issuer, Rekor log index |
| L3 | L2 plus the proof that the builder is isolated: `builder.id` is a reusable trusted builder (`slsa-github-generator`, Cloud Build, Tekton Chains) and the signing identity is that builder, not the project workflow |
