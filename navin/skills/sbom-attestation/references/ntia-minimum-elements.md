# NTIA minimum elements, mapped to CycloneDX and SPDX fields

Source: NTIA, "The Minimum Elements For a Software Bill of Materials (SBOM)", July 2021 (US Executive Order 14028). Three groups: data fields, automation support, practices and processes. CISA's 2024 "Framing Software Component Transparency" (3rd edition) extends the list (component hash, license, tool name, generation context) as baseline attributes.

## Data fields

| NTIA element | Meaning | CycloneDX 1.4 to 1.6 (JSON) | SPDX 2.2 / 2.3 (JSON) | `sbom_quality.py` check |
|---|---|---|---|---|
| Supplier name | the entity that creates, defines and identifies the component | `components[].supplier.name` (fallback `components[].publisher`); `metadata.component.supplier` for the root | `packages[].supplier` (`Organization: X` or `Person: X`; `NOASSERTION` counts as missing); `packages[].originator` is the upstream author, not the supplier | `supplier` coverage |
| Component name | the designation assigned by the supplier | `components[].name` (with `components[].group` for the namespace) | `packages[].name` | `component_name` coverage |
| Version of the component | identifier of a change from a previous version | `components[].version` | `packages[].versionInfo` | `version` coverage; `missing.version` list |
| Other unique identifiers | identifiers usable to look the component up in databases | `components[].purl`, `components[].cpe`, `components[].swid.tagId`, `components[].hashes[]` | `packages[].externalRefs[]` with `referenceType` `purl`, `cpe23Type`, `cpe22Type`, `swid`; `packages[].checksums[]` | `unique_identifiers` coverage (purl or cpe); `missing.purl` list |
| Dependency relationship | the relation "X is included in Y" | `dependencies[]` with `ref` and `dependsOn[]`; nested `components[].components[]` for assemblies | `relationships[]` with `relationshipType` `DEPENDS_ON`, `DEPENDENCY_OF`, `CONTAINS`, plus `DESCRIBES` from the document to the root package | `dependency_relationship` 100 when at least one edge exists |
| Author of SBOM data | who produced the SBOM (may differ from the supplier) | `metadata.authors[].name`; `metadata.tools.components[].name` (1.5+) or `metadata.tools[].name` (1.4) as fallback; `metadata.manufacture` (1.4/1.5) or `metadata.manufacturer` (1.6) | `creationInfo.creators[]` prefixed `Organization:`, `Person:` or `Tool:` | `sbom_author` 100 when any author or tool is present |
| Timestamp | when the SBOM was assembled | `metadata.timestamp` (RFC 3339) | `creationInfo.created` (`YYYY-MM-DDThh:mm:ssZ`) | `timestamp` 100 when present |

Additional baseline attributes (CISA 2024, not scored in `ntia_score` but reported):

| Attribute | CycloneDX | SPDX |
|---|---|---|
| License | `components[].licenses[].license.id` (SPDX id), `.license.name`, or `.expression` | `packages[].licenseConcluded`, `packages[].licenseDeclared` (`NOASSERTION` = missing) |
| Component hash | `components[].hashes[]` (`alg`: SHA-256, SHA-512 ...) | `packages[].checksums[]` (`algorithm`, `checksumValue`) |
| Generation context (CISA SBOM type) | `metadata.lifecycles[].phase` (1.5+): `design`, `pre-build`, `build`, `post-build`, `operations`, `discovery`, `decommission` | `creationInfo.comment` or `document comment`; no dedicated field in 2.x |
| Root component | `metadata.component` | package pointed to by the `DESCRIBES` relationship |
| Copyright | `components[].copyright` | `packages[].copyrightText` |
| External references | `components[].externalReferences[]` (`type`: `vcs`, `website`, `distribution`, `issue-tracker`) | `packages[].externalRefs[]` (`referenceCategory` `OTHER`), `homepage`, `downloadLocation` |

## Automation support

The SBOM must be machine readable in one of three formats: SPDX, CycloneDX, SWID. Validate the file against its schema before scoring it:

```bash
cyclonedx validate --input-file sbom.cdx.json --input-format json --input-version v1_5 --fail-on-errors
pyspdxtools -i sbom.spdx.json                       # python spdx-tools; exits 1 on validation errors
java -jar tools-java-<ver>-jar-with-dependencies.jar Verify sbom.spdx.json   # Java SPDX tools
sbomqs score sbom.cdx.json                          # interlynk sbomqs: NTIA + BSI + quality score
ntia-checker --file sbom.spdx.json --output json    # spdx/ntia-conformance-checker (SPDX only)
```

## Practices and processes

| Practice | What to check in the pipeline |
|---|---|
| Frequency | a new SBOM is produced at every build and at every change of a component; the SBOM is attached to the exact digest it describes |
| Depth | all top-level components and their transitive dependencies; `syft --scope all-layers` for images, lockfile cataloging for source |
| Known unknowns | when the graph is incomplete, say so: CycloneDX `compositions[].aggregate` (`complete`, `incomplete`, `incomplete_first_party_only`, `unknown`); SPDX `NOASSERTION` values |
| Distribution and delivery | SBOM available to the consumer with the artifact: OCI attestation (`cosign attest --type cyclonedx`), GitHub release asset, or vendor portal |
| Access control | the SBOM may be confidential; the access rule is documented and the consumer can still verify the artifact |
| Accommodation of mistakes | tolerance for omissions and errors while the practice matures; track the score trend, do not block on day one |

## CISA SBOM types

| Type | Produced from | Typical tool | Caveat |
|---|---|---|---|
| Design | the intended architecture, before code | manual, architecture repository | no versions |
| Source | source tree and lockfiles | `syft dir:.`, `cdxgen`, `cyclonedx-npm`, `cyclonedx-python` | dev dependencies may be included |
| Build | the build process (what was compiled in) | `syft` in the build job, Maven / Gradle CycloneDX plugins, `go version -m` | the authoritative SBOM for attestation |
| Analyzed | post-build inspection of the binary or image | `syft <image>`, `trivy image --format cyclonedx` | heuristic; may miss statically linked or vendored code |
| Deployed | the installed system, configuration included | `syft` on the host, package manager inventory | includes components outside the artifact |
| Runtime | what is actually loaded or executed | eBPF or agent based (Sysdig, Deepfactor) | only loaded components; absence is not proof |

## EU Cyber Resilience Act expectations

Regulation (EU) 2024/2847, Annex I Part II point 1: manufacturers must "identify and document vulnerabilities and components contained in products with digital elements, including by drawing up a software bill of materials in a commonly used and machine-readable format covering at the very least the top-level dependencies of the products". The SBOM is part of the technical documentation (Annex VII) and must be available to market surveillance authorities on request; it does not have to be published. Obligations apply from 11 December 2027 (vulnerability reporting from 11 September 2026). Germany's BSI TR-03183-2 is the most detailed national profile (required fields per component, including hash and license, and the SBOM's own URI).
