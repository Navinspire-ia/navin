---
name: secrets-manager
description: Handle secrets safely via env vars, Vault, AWS Secrets Manager, or encrypted stores — never paste keys into chat, commits, or logs. Use when configuring APIs, CI, or cloud credentials.
metadata: {"navin":{"emoji":"🔐","category":"security"}}
---

# Secrets Manager

## Overview

Secrets stay in secret stores or environment variables. Chat history and git are not vaults.

## Preferred patterns

1. Read from env (`os.environ` / shell env) already configured on the host
2. Reference Vault / AWS SM / 1Password CLI paths — fetch at runtime, do not cache in markdown
3. Use provider Settings in Navin WebUI for API keys when available
4. For CI: repository secrets / OIDC — not hardcoded values

## Workflow

1. Identify which secret is needed and which store holds it.
2. Confirm it is **not** already in the repo (`grep` for key-shaped strings).
3. Wire the tool/config to the env var name — do not print the value.
4. Redact any accidental exposure in your reply (`sk-***`, `AKIA***`).
5. If a secret leaked into chat or git, tell the user to **rotate** it.

## Forbidden

- Committing `.env` with real values
- Putting tokens in `SKILL.md` or memory files
- Echoing full secrets in tool arguments visible to the user when avoidable
