---
name: docker-operator
description: Build, run, inspect, and diagnose Docker containers and Compose stacks safely. Use when debugging images, ports, volumes, or container logs.
metadata: {"navin":{"emoji":"🐳","category":"devops","requires":{"bins":["docker"]}}}
---

# Docker Operator

## Overview

Operate Docker with least surprise: inspect before restart, prefer Compose files in-repo, avoid destructive prune unless approved.

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
