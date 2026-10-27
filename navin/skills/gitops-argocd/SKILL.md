---
name: gitops-argocd
description: Run GitOps deployments with Argo CD and Flux - inspect applications, diff desired vs live state, deploy through git commits and PRs, sync and verify rollouts.
metadata: {"navin":{"emoji":"🚀","category":"devops"}}
---

# GitOps / ArgoCD

## Overview

Deploy through git, not through kubectl. The git repository is the source of truth; ArgoCD reconciles it. Prefer the ArgoCD MCP server (`argocd` preset) or the `argocd` CLI when available; fall back to reading Application CRs with kubectl.

## Engine

Drive these CLIs through the `infra` tool (providers: argocd,flux).

1. `infra action=discover provider=argocd` and/or `provider=flux`.
2. `infra action=check command="argocd app sync ..."` / `"flux reconcile ..."`.
3. Diff first (`argocd app diff`, `flux diff kustomization`) via `infra action=run`, then the sync/reconcile step, then `infra action=verify` (Synced + Healthy / Ready=True).

- Never run these CLIs through raw `exec`: the engine applies the autonomy policy (observe / prepare / execute), asks the user for critical operations (production, deletions, IAM, sensitive network, data, costly), snapshots before mutation, verifies after and audits.
- Multi-domain missions start from `infra-orchestrator`.

## Flux

```bash
flux check; flux get sources all -A; flux get kustomizations -A; flux get helmreleases -A
flux diff kustomization <k> --path ./clusters/<env>
flux reconcile kustomization <k> --with-source
flux suspend kustomization <k>; flux resume kustomization <k>
flux logs --level=error --since=1h
```

Same rule as Argo CD: change git, open a PR, let Flux reconcile; `flux suspend` is the emergency brake and must be resumed and recorded.

## Common commands

```bash
argocd app list
argocd app get <app>
argocd app diff <app>
argocd app history <app>
argocd app sync <app> --dry-run
kubectl get applications.argoproj.io -n argocd
kubectl describe application <app> -n argocd
```

## Workflow

1. Locate the Application: repo URL, path, target revision, destination cluster/namespace, sync policy.
2. To change what is deployed: edit the manifests/kustomize/Helm values in the git repo, commit on a branch, open a PR/MR, and let review + ArgoCD do the rest. Do not patch live resources.
3. After merge: watch sync status and health (`argocd app get`, `app wait`), then verify the workload itself (rollout status, logs).
4. For drift: `argocd app diff` first, then decide - sync (git wins) or port the live change back into git.

## Rules

- `argocd app sync` on prod apps, rollbacks, and `app delete` require `human-approval`.
- Never disable auto-sync or prune without saying so in the report.
- Never commit secrets to the GitOps repo; use sealed-secrets/external-secrets references.
