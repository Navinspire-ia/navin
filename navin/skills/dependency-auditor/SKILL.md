---
name: dependency-auditor
description: Audit the software supply chain - vulnerable and outdated dependencies, CVEs, lockfile integrity, license risks, typosquatting, and SBOM generation. Use for /lineage, dependency reviews, or "are our packages safe?" questions.
metadata: {"navin":{"emoji":"📦","category":"security"}}
---

# Dependency Auditor

## Overview

Assess third-party risk across the whole dependency tree. Findings must name the exact package, the resolved version in the lockfile, the CVE or advisory ID when known, and the fixed version to upgrade to. Read-only by default: recommend bumps, never apply them unasked.

## What to inspect

1. **Manifest vs lockfile** - every ecosystem present: `package.json`/`package-lock.json`/`bun.lockb`, `pyproject.toml`/`uv.lock`/`requirements.txt`/`poetry.lock`, `Cargo.toml`/`Cargo.lock`, `go.mod`/`go.sum`, `pom.xml`, `Gemfile.lock`. Audit the **resolved** version, not the declared range.
2. **Known vulnerabilities** - run the native scanner when available: `npm audit --json`, `pip-audit`, `osv-scanner`, `cargo audit`, `govulncheck`, `trivy fs`. Cross-check transitive dependencies, not just direct ones.
3. **Freshness** - abandoned/unmaintained packages, majors behind, deprecated releases.
4. **Integrity & provenance** - missing lockfile, unpinned versions, git/URL/tarball deps, mismatched hashes, install scripts (`postinstall`) that run arbitrary code.
5. **Typosquatting & confusion** - names close to popular packages, internal names resolvable from public registries (dependency confusion).
6. **Licenses** - copyleft (GPL/AGPL) in a proprietary product, missing or incompatible licenses.

## Workflow

1. Inventory every ecosystem and locate all manifests + lockfiles (use the metagraph/project-metadata when present instead of blind grep).
2. Prefer real tooling first; fall back to matching lockfile versions against the OSV database only for the packages actually resolved in the tree.
3. For each finding: `[SEVERITY] package@version` - advisory/CVE, transitive path (`a → b → vulnerable`), impact, and the minimal safe version.
4. Separate **directly fixable** (bump a direct dep) from **blocked** (needs an upstream fix or a transitive override).
5. Produce a prioritized upgrade plan and, if asked, generate an SBOM (CycloneDX/SPDX) file in the workspace.

## Anti-patterns

- Reporting an advisory for a version range that the lockfile does not actually resolve to
- Auditing direct dependencies only and ignoring transitive ones
- Recommending a blind `npm audit fix --force` that breaks the build
- Bumping versions during the audit without an explicit request
