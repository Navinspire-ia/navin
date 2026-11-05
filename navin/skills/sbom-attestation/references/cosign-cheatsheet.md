# Cosign cheatsheet (Sigstore)

All commands act on an image **digest** (`repo@sha256:...`), never on a tag. Resolve the digest first:

```bash
crane digest registry.example.com/payments-api:1.4.2
# or
skopeo inspect --format '{{.Digest}}' docker://registry.example.com/payments-api:1.4.2
IMAGE=registry.example.com/payments-api@sha256:<digest>
```

Cosign 2.x: keyless mode is the default; `COSIGN_EXPERIMENTAL=1` is no longer needed. The signature, SBOM and attestations are stored in the same registry as OCI artifacts tagged `sha256-<digest>.sig`, `.sbom` and `.att`, and every keyless signature is recorded in the Rekor transparency log (`https://rekor.sigstore.dev`).

## Key material

| Task | Command | Notes |
|---|---|---|
| Generate a key pair | `cosign generate-key-pair` | writes `cosign.key` (encrypted, password from `COSIGN_PASSWORD`) and `cosign.pub` |
| KMS-backed key | `cosign generate-key-pair --kms awskms:///alias/cosign` | also `gcpkms://`, `azurekms://`, `hashivault://` |
| Kubernetes secret | `cosign generate-key-pair k8s://<ns>/<secret>` | used by policy-controller |
| Public key from KMS | `cosign public-key --key awskms:///alias/cosign > cosign.pub` | the only thing to distribute |

Never print `cosign.key` or `COSIGN_PASSWORD`. Read them from the environment or a secret manager.

## Sign and verify with a key

```bash
cosign sign --key cosign.key "$IMAGE"
cosign sign --key awskms:///alias/cosign "$IMAGE"
cosign verify --key cosign.pub "$IMAGE" | jq .
```

Add `-a env=prod -a git_sha=$GITHUB_SHA` to attach annotations; verify them with `-a key=value`, which fails when the annotation is absent or different.

## Keyless (Fulcio certificate + Rekor entry)

```bash
# Interactive: opens the browser on the OIDC provider
cosign sign "$IMAGE"
# CI: the workflow identity token is used automatically (GitHub Actions needs permissions: id-token: write)
cosign sign --yes "$IMAGE"
```

Verify by pinning the identity and its issuer. Both flags are mandatory in Cosign 2.x:

```bash
cosign verify \
  --certificate-identity "https://github.com/example-org/payments-api/.github/workflows/release.yml@refs/tags/v1.4.2" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  "$IMAGE"
```

Use `--certificate-identity-regexp '^https://github.com/example-org/payments-api/\.github/workflows/release\.yml@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$'` when the tag changes at every release. A regexp that matches any repository of the organisation accepts a fork of a fork; keep the repository and the workflow path literal.

Issuers:

| Platform | `--certificate-oidc-issuer` |
|---|---|
| GitHub Actions | `https://token.actions.githubusercontent.com` |
| GitLab CI | `https://gitlab.com` (or the self-hosted URL) |
| Google | `https://accounts.google.com` |
| Microsoft | `https://login.microsoftonline.com` |
| Private Sigstore | the Dex URL of that deployment |

Extra GitHub claims checked with `--certificate-github-workflow-repository`, `--certificate-github-workflow-ref`, `--certificate-github-workflow-sha`.

`cosign verify` output is a JSON array; the `optional` object carries `Issuer`, `Subject` and the GitHub claims. Keep that output as evidence.

## Attestations (in-toto)

```bash
# SLSA provenance
cosign attest --predicate provenance.json --type slsaprovenance1 "$IMAGE"
cosign verify-attestation --type slsaprovenance1 \
  --certificate-identity "<identity>" --certificate-oidc-issuer "<issuer>" "$IMAGE" \
  | jq -r '.payload' | base64 -d | jq .

# SBOM as attestation (preferred over cosign attach sbom)
cosign attest --predicate sbom.cdx.json --type cyclonedx "$IMAGE"
cosign verify-attestation --type cyclonedx --certificate-identity "<identity>" --certificate-oidc-issuer "<issuer>" "$IMAGE"

# Vulnerability scan result
cosign attest --predicate grype.json --type vuln "$IMAGE"

# OpenVEX document
cosign attest --predicate vex.openvex.json --type openvex "$IMAGE"
```

`--type` values and the `predicateType` URIs they map to:

| `--type` | predicateType |
|---|---|
| `slsaprovenance` | `https://slsa.dev/provenance/v0.2` |
| `slsaprovenance02` | `https://slsa.dev/provenance/v0.2` |
| `slsaprovenance1` | `https://slsa.dev/provenance/v1` |
| `spdx` | `https://spdx.dev/Document` |
| `spdxjson` | `https://spdx.dev/Document` |
| `cyclonedx` | `https://cyclonedx.org/bom` |
| `vuln` | `https://cosign.sigstore.dev/attestation/vuln/v1` |
| `openvex` | `https://openvex.dev/ns` |
| `custom` | `https://cosign.sigstore.dev/attestation/v1` |
| any URI | used as is |

The payload printed by `verify-attestation` is a DSSE envelope: `payloadType` is `application/vnd.in-toto+json`, `payload` is the base64 in-toto Statement. Decode it before reading `predicate`.

Apply a CUE or Rego policy at verification time: `cosign verify-attestation --type slsaprovenance1 --policy policy.cue ...`.

## SBOM attached as OCI artifact (legacy path)

```bash
cosign attach sbom --sbom sbom.cdx.json --type cyclonedx "$IMAGE"
cosign download sbom "$IMAGE" > sbom.cdx.json
```

`cosign attach sbom` does not sign anything: sign the `.sbom` artifact separately with `cosign sign --attachment sbom "$IMAGE"` or, better, use `cosign attest --type cyclonedx`.

## Inspect what is attached

```bash
cosign tree "$IMAGE"               # signatures, attestations, SBOMs linked to the digest
cosign download signature "$IMAGE"  # raw signature payloads
cosign download attestation "$IMAGE" | jq -r '.payload' | base64 -d | jq '.predicateType'
```

## Rekor transparency log

```bash
rekor-cli search --sha sha256:<digest>          # UUIDs of entries for that artifact
rekor-cli get --uuid <uuid> --format json        # the entry: integrated time, log index, body
rekor-cli get --log-index <n>
# REST
curl -s https://rekor.sigstore.dev/api/v1/log/entries/<uuid> | jq .
```

`cosign verify` already checks the Rekor inclusion proof and the signed entry timestamp (SET). `--offline` requires a bundle (`cosign sign --bundle out.bundle`, `cosign verify --bundle out.bundle`); `--insecure-ignore-tlog` disables the log check and must appear in no verification used as evidence.

## Blobs and files

```bash
cosign sign-blob --bundle release.tar.gz.bundle release.tar.gz
cosign verify-blob --bundle release.tar.gz.bundle \
  --certificate-identity "<identity>" --certificate-oidc-issuer "<issuer>" release.tar.gz
```

## Admission control

Sigstore policy-controller `ClusterImagePolicy` (namespace opt-in with label `policy.sigstore.dev/include: "true"`):

```yaml
apiVersion: policy.sigstore.dev/v1beta1
kind: ClusterImagePolicy
metadata:
  name: payments-signed-and-provenance
spec:
  images:
    - glob: "registry.example.com/payments-*"
  authorities:
    - keyless:
        url: https://fulcio.sigstore.dev
        identities:
          - issuer: https://token.actions.githubusercontent.com
            subjectRegExp: "^https://github.com/example-org/payments-api/.github/workflows/release.yml@refs/tags/v.*$"
      ctlog:
        url: https://rekor.sigstore.dev
      attestations:
        - name: provenance
          predicateType: slsaprovenance1
          policy:
            type: cue
            data: |
              predicateType: "https://slsa.dev/provenance/v1"
              predicate: runDetails: builder: id: =~"^https://github.com/slsa-framework/slsa-github-generator/"
```

Kyverno `verifyImages` equivalent:

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: verify-payments-images
spec:
  validationFailureAction: Enforce
  webhookTimeoutSeconds: 30
  rules:
    - name: keyless-signature
      match:
        any:
          - resources:
              kinds: [Pod]
      verifyImages:
        - imageReferences: ["registry.example.com/payments-*"]
          mutateDigest: true
          verifyDigest: true
          required: true
          attestors:
            - entries:
                - keyless:
                    subject: "https://github.com/example-org/payments-api/.github/workflows/release.yml@refs/tags/*"
                    issuer: "https://token.actions.githubusercontent.com"
                    rekor:
                      url: https://rekor.sigstore.dev
          attestations:
            - type: https://slsa.dev/provenance/v1
              conditions:
                - all:
                    - key: "{{ runDetails.builder.id }}"
                      operator: Equals
                      value: "https://github.com/slsa-framework/slsa-github-generator/.github/workflows/generator_container_slsa3.yml@refs/tags/v2.0.0"
```

`mutateDigest: true` rewrites the tag into a digest in the Pod spec so the verified image is the one that runs. Start in `Audit` mode and read the `PolicyReport` objects before switching to `Enforce`.
