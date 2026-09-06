---
name: secrets-scanner
description: Hunt for leaked credentials - hardcoded API keys, tokens, private keys, passwords and connection strings in code, config, logs and git history. Use for /unmask, pre-commit secret sweeps, or "did we leak a key?" questions.
metadata: {"navin":{"emoji":"🔑","category":"security"}}
---

# Secrets Scanner

## Overview

Find secrets that should never be in the repository, ranked by how exploitable the leak is. A committed live production key is critical; a placeholder in an example file is informational. Every finding cites file, line, and the secret **type** (never echo the full secret value - show a masked prefix only).

## What to detect

| Class | Signals |
|-------|---------|
| Cloud keys | `AKIA…` (AWS), `AIza…` (Google), Azure connection strings, GCP service-account JSON |
| Tokens | GitHub `ghp_`/`gho_`, Slack `xox…`, Stripe `sk_live_`, JWT secrets, bearer tokens |
| Private keys | `-----BEGIN (RSA|EC|OPENSSH|PGP) PRIVATE KEY-----` |
| Passwords | `password=`, `passwd`, basic-auth in URLs, DB connection strings with creds |
| Generic | high-entropy strings assigned to `secret`/`token`/`apikey`/`key` names |

## Workflow

1. Call `security_scan(kind=secrets)` first for structured baseline findings.
2. Scan the working tree deeply: source, config, `.env*`, CI files, Dockerfiles, notebooks, and infra manifests.
3. Then scan **git history** (`git log -p`, `git rev-list`) - a secret removed in a later commit is still exposed and must be rotated.
4. Prefer real tooling when present (`gitleaks`, `trufflehog`, `detect-secrets`); use pattern sweeps for what they miss.
5. Triage false positives: test fixtures, obvious placeholders (`xxx`, `changeme`, `example`), and public keys are not leaks - mark them Info.
6. For each real finding, mask the value (`sk_live_51H…` → `sk_live_51H•••`), and give the response: **rotate the credential**, remove from history if committed, and move to a secret manager / env var.
7. Recommend prevention: `.gitignore` rules, pre-commit secret scanning, and a secrets baseline.

## Anti-patterns

- Printing the full secret in the report (mask it)
- Flagging placeholders and test fixtures as critical leaks
- Assuming deletion from the current tree is enough (history still leaks; rotation is mandatory)
- Rotating or editing anything without an explicit request
