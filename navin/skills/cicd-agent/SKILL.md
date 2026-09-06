---
name: cicd-agent
description: Work with GitHub Actions, GitLab CI, and deployment pipelines - inspect runs, fix workflows, and gate releases. Use gh when available for Actions.
metadata: {"navin":{"emoji":"🚀","category":"devops"}}
---

# CI/CD Agent

## Overview

Make pipelines green and releases intentional.

## Workflow

1. Locate workflow files (`.github/workflows`, `.gitlab-ci.yml`, etc.).
2. Inspect latest failures (`gh run list` / `gh run view --log-failed` if `gh` exists).
3. Reproduce locally when practical.
4. Patch CI YAML or tests; keep secrets in CI secret stores.
5. Re-run and confirm green before merge/deploy advice.

## Rules

- Production deploy jobs need `human-approval`.
- Do not embed cloud keys in YAML.
- Prefer caching and constrained permissions (`permissions:` in GHA).
