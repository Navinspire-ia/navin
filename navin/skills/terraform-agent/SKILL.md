---
name: terraform-agent
description: Author and validate Terraform / OpenTofu IaC with plan-before-apply discipline. Use for infrastructure changes and reviews.
metadata: {"navin":{"emoji":"🏗️","category":"devops","requires":{"bins":["terraform"]}}}
---

# Terraform Agent

## Overview

Infrastructure as code with plan-first safety. Never apply blindly to shared state.

## Workflow

1. Find root module / workspaces.
2. `terraform fmt` / `terraform validate` when available.
3. `terraform plan` (or `tofu plan`) and summarize resource deltas.
4. Highlight destroys and replacements.
5. Apply only after explicit approval; capture plan file when useful.

## Rules

- State backends and credentials stay in env/backends - not in chat.
- Prefer smallest module change that solves the request.
- Pair with `human-approval` before `apply`.
