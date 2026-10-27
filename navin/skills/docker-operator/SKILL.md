---
name: docker-operator
description: Build, run, inspect, and diagnose containers safely - Docker and Compose, Podman, containerd (nerdctl, crictl). Use when debugging images, ports, volumes, or container logs.
metadata: {"navin":{"emoji":"🐳","category":"devops"}}
---

# Docker Operator

## Overview

Operate Docker with least surprise: inspect before restart, prefer Compose files in-repo, avoid destructive prune unless approved.

## Engine

Drive these CLIs through the `infra` tool (providers: docker,podman).

1. `infra action=discover provider=docker` or `provider=podman`.
2. `infra action=check command="docker ..."` (prune, rm -f, volume rm come back critical).
3. `infra action=run`, `infra action=verify` (container healthy, port answers).

- Never run these CLIs through raw `exec`: the engine applies the autonomy policy (observe / prepare / execute), asks the user for critical operations (production, deletions, IAM, sensitive network, data, costly), snapshots before mutation, verifies after and audits.
- Multi-domain missions start from `infra-orchestrator`.

## Podman and containerd

```bash
podman ps -a; podman pod ps; podman logs <c>; podman generate systemd --new --name <c>
podman machine list; podman-compose up -d; podman system df
nerdctl ps -a; nerdctl --namespace k8s.io images
crictl ps -a; crictl pods; crictl logs <id>; crictl inspect <id>
ctr -n k8s.io containers list
```

On Kubernetes nodes use `crictl` (read) rather than docker; never remove containers managed by the kubelet.

## Common commands

```bash
docker ps -a
docker logs --tail 200 <container>
docker compose ps
docker compose logs -f --tail=100
docker image ls
docker inspect <id>
```

## Workflow

1. Confirm context (compose file path, service name).
2. Inspect running state and recent logs.
3. Reproduce the failure with the smallest command.
4. Change config/code → rebuild only what is needed.
5. Verify healthchecks / HTTP readiness.

## Rules

- `docker system prune` / volume deletes need `human-approval`.
- Do not publish privileged containers unless required and approved.
- Never embed secrets in image layers; use env/secrets.
