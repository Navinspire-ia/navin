---
name: openshift-operator
description: Operate Red Hat OpenShift with oc - projects, routes, SecurityContextConstraints, builds and image streams, operators through OLM, cluster operators and upgrades with oc adm. Use for any OpenShift or OKD cluster task.
metadata: {"navin":{"emoji":"🎩","category":"devops"}}
---

# OpenShift Operator

## Overview

OpenShift is Kubernetes plus opinions: restricted SCCs, Routes, OLM-managed operators and a cluster version operator. Respect those opinions rather than fighting them.

## When to use

- Deploy or debug apps on OpenShift/OKD, fix SCC denials, expose Routes.
- Install and upgrade operators via OLM.
- Cluster health, upgrades, node maintenance with `oc adm`.

## Engine

1. `infra action=status` (oc context, production flag), `infra action=discover provider=openshift context=<ctx>` (api-resources including CRDs).
2. `infra action=graph op=impact node=<project>` before cluster-level changes.
3. `infra action=check` every `oc` command; `infra action=plan` for upgrades and operator installs; `infra action=run`, `infra action=verify`.

## Workflow

1. `oc whoami --show-server`, project, cluster version and cluster operators health.
2. Workloads: pods/events, SCC assigned (`openshift.io/scc` annotation), Routes and TLS termination.
3. Operators: Subscription channel, InstallPlan approval Manual for prod, CSV phase.
4. Upgrades: `oc adm upgrade` to see paths, check `Upgradeable` conditions, MachineConfigPool progress.

## Command families

```bash
oc whoami; oc whoami --show-server; oc project <p>; oc get projects
oc get clusterversion; oc get co; oc adm upgrade
oc get pods -o wide; oc get events --sort-by=.lastTimestamp; oc logs deploy/<d>
oc get route; oc create route edge <name> --service=<svc> --hostname=<host>
oc adm policy who-can use scc privileged; oc get pod <p> -o jsonpath='{.metadata.annotations.openshift\.io/scc}'
oc get subscriptions,csv,installplan -n <ns>; oc get packagemanifests -n openshift-marketplace
oc get mcp; oc adm cordon <node>; oc adm drain <node> --ignore-daemonsets --delete-emptydir-data
oc adm top nodes; oc adm must-gather
oc start-build <bc> --follow; oc get is
```

## Safety rules

- Granting `anyuid`/`privileged` SCC, cluster upgrades, approving InstallPlans, drains and MachineConfig changes are critical: user validation.
- Fix SCC denials by making the image run as non-root before granting SCCs.
- Production operators use Manual InstallPlan approval.

## Verification and rollback

- Verify: cluster operators Available=True Degraded=False, MCPs Updated, Route answers over TLS, CSV Succeeded.
- Rollback: `oc rollout undo`, previous CSV via subscription pinning, remove an SCC binding; cluster upgrades cannot be rolled back - plan them.

## Deliverable

Cluster/operator health summary, changes with run ids, verification evidence, rollback.
