---
name: security-containers
description: Audit Docker, Kubernetes and OpenShift. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Containers (Navin Security)

## Overview

Audit images, Dockerfiles, Compose files and Kubernetes manifests.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: docker, kubernetes.
- Families: infrastructure.

## Controls covered

| Control | Level | Check |
|---|---|---|
| C4.05 | L1 | Containers: root user, images, docker.sock, capabilities |
| C4.06 | L1 | Kubernetes: RBAC, NetworkPolicy, securityContext, secrets |

## Engines

- Registered scanners (use `navin.security.scanners`, results arrive as normalized findings): trivy.
- Other engines named by the catalog: checkov, kube_bench, navin_rules. Use one only when it is installed.
- A scanner that is not installed or did not run means the check was not done, not that it passed.

## Method

1. Dockerfile: non-root `USER`, pinned base image, no secrets in build args or layers, minimal packages.
2. Compose and runtime: no `privileged`, no `docker.sock` mount, dropped capabilities, read-only root where possible.
3. Kubernetes: RBAC without wildcards, NetworkPolicy present, `securityContext` set, no host namespaces, secrets not in plain env.
4. Use the registered scanners for image and manifest checks; confirm each hit against the manifest actually deployed.
5. Fix as a diff on the manifest, with the setting and its safe value.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Scanning an image tag that is not the one deployed.
- Applying a manifest to a cluster to test it.
- Treating a clean scan as proof of security: say which controls were not assessed.
