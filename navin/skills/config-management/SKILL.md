---
name: config-management
description: Manage configuration as code - Ansible playbooks and roles, SSH fleets, cloud-init, Helm values, Kustomize overlays - always check/diff before apply. Use for server configuration, fleet changes and environment overlays.
metadata: {"navin":{"emoji":"⚙️","category":"devops"}}
---

# Config Management

## Overview

Desired state lives in git; you converge machines and manifests to it idempotently and you always show the diff before applying.

## When to use

- Configure or patch a fleet with Ansible, bootstrap VMs with cloud-init.
- Maintain Helm values and Kustomize overlays per environment.
- Drift detection between git and live systems.

## Engine

1. `infra action=discover provider=ansible` (modules via `ansible-doc -l -j`), `provider=helm`, `provider=kubernetes`.
2. `infra action=check command="ansible-playbook ..."` - the engine classifies scope and risk.
3. `infra action=plan` with a check step (`--check --diff`) before the apply step, `infra action=run`, `infra action=verify`.

## Workflow

1. Read the inventory and target group; confirm it is the intended environment.
2. Lint (`ansible-lint`, `yamllint`, `kustomize build`), syntax check.
3. Dry-run with diff on a canary host or one overlay.
4. Apply in batches (`serial:`), stop on first failure (`max_fail_percentage: 0`).
5. Re-run in check mode: zero changes means converged.

## Command families

```bash
ansible-inventory -i <inv> --graph; ansible -i <inv> <group> -m ping
ansible-playbook -i <inv> site.yml --syntax-check
ansible-playbook -i <inv> site.yml --check --diff --limit <canary>
ansible-playbook -i <inv> site.yml --limit <group>
ansible-lint; ansible-doc <module>
ssh -o BatchMode=yes <host> 'systemctl is-active <svc>'
cloud-init status --long; cloud-init schema --system
helm template <rel> <chart> -f values-prod.yaml | kubectl diff -f -
kustomize build overlays/prod | kubectl diff -f -
```

## Safety rules

- Playbooks against production groups, `shell`/`command` modules with destructive commands, user/SSH key and firewall roles are critical: user validation.
- Never apply without a check/diff run shown to the user first.
- Secrets via Ansible Vault / SOPS / external secret stores, never plain in vars.

## Verification and rollback

- Verify: second check run reports changed=0, service health checks pass.
- Rollback: revert the commit and re-apply, previous Helm revision, restore of backed-up config files (`backup: yes`).

## Deliverable

Hosts/overlays targeted, diff shown, apply results per host, convergence evidence, rollback.
