---
name: toolbelt
description: Verify and install missing CLI tools on demand - scanners, linters, runtimes - without root, using uvx, npx, or static binaries in ~/.local/bin. Use at the start of any audit, scan, or build when a needed tool may be absent.
metadata: {"navin":{"emoji":"🔩","category":"devops"}}
---

# Toolbelt

## Overview

Real tools beat pattern sweeps. Never abandon an audit or build just because a scanner or runtime is missing: check availability first, install what you can without root, and only then fall back to manual analysis. Always say which tools you used and which were unavailable.

## Resolution order

For each required tool, stop at the first step that works:

1. **Already on PATH** - `command -v <tool>`. Use it.
2. **Python CLI** - run ephemerally with `uvx <tool>` (preferred, zero install) or `pipx run <tool>`; for repeated use, `uv tool install <tool>` or `python3 -m pip install --user <tool>`.
3. **Node CLI** - `npx --yes <tool>` when `node` is present.
4. **Static binary** - download the official release for the current OS/arch into `~/.local/bin`, `chmod +x`, and ensure `~/.local/bin` is on PATH. Works without root for Go-built tools (gitleaks, osv-scanner, trivy, hadolint, shellcheck) and even for Node itself (official tarball unpacked in `$HOME`).
5. **Degrade gracefully** - do the manual equivalent (grep sweeps, lockfile reading, config review) and state clearly which tool was missing and what it would have added.

## Domain map

| Need | First choice | Fallbacks |
|------|--------------|-----------|
| Secrets scan | `gitleaks` (static binary) | `uvx detect-secrets`, pattern sweep + `git log -p` |
| Python SAST/lint | `uvx bandit`, `uvx ruff` | manual sink review |
| Multi-language SAST | `uvx semgrep` | targeted grep per sink class |
| Python deps CVEs | `uvx pip-audit` | read lockfile, check versions |
| JS/TS deps CVEs | `npm audit` (needs npm only) | `osv-scanner` binary, lockfile review |
| Multi-ecosystem CVEs | `osv-scanner` (static binary) | per-ecosystem native scanner |
| Containers/IaC | `trivy` (static binary), `uvx checkov` | manual Dockerfile/manifest review |
| Dockerfile lint | `hadolint` (static binary) | manual best-practices check |
| JS lint/types | `npx --yes eslint`, `npx --yes tsc --noEmit` | read tsconfig + spot checks |

## Rules

1. No `sudo`, no `apt install` in restricted or containerized environments - everything goes to `$HOME` (`~/.local/bin`, `uv tool`, `pip --user`).
2. Respect the exec sandbox and allowlist; if an install is denied, fall back instead of retrying.
3. Time-box bootstrapping: a couple of minutes at most. The mission is the audit/build, not the tooling.
4. Pin nothing blindly: prefer latest stable releases from official sources only (GitHub releases, PyPI, npm registry).
5. Report at the end: tools used, tools installed (and where), tools unavailable and the fallback applied.

## Anti-patterns

- Failing the whole task because one scanner is missing
- Spending the entire turn compiling or troubleshooting an install
- Piping `curl` into a shell, or fetching binaries from unofficial mirrors
- Installing system-wide or modifying the base image at runtime
- Silently skipping a check without disclosing the missing tool
