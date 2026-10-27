---
name: cicd-agent
description: Work with GitHub Actions, GitLab CI, and deployment pipelines - inspect runs, fix workflows, and gate releases. Use gh when available for Actions.
metadata: {"navin":{"emoji":"🚀","category":"devops"}}
---

# CI/CD Agent

## Overview

Make pipelines green and releases intentional.

## Engine

Drive these CLIs through the `infra` tool (providers: github,gitlab).

1. `infra action=discover provider=github` or `provider=gitlab` (gh/glab help trees).
2. Read-only run inspection through `infra action=run command="gh run view ..."`.
3. Reruns, workflow dispatch, variable/secret changes and deploy jobs: `infra action=check` then `infra action=run`; production deploy jobs need the user.
4. Golden paths across repos: `platform-engineer`.

- Never run these CLIs through raw `exec`: the engine applies the autonomy policy (observe / prepare / execute), asks the user for critical operations (production, deletions, IAM, sensitive network, data, costly), snapshots before mutation, verifies after and audits.
- Multi-domain missions start from `infra-orchestrator`.

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
