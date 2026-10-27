---
name: resilience-tester
description: Test that a system holds - network checks, API tests, performance and load (k6, hey, vegeta), security scans, availability probes, disaster recovery drills and chaos experiments (pod/node/AZ failure). Use before a launch, after an architecture change or to validate RTO/RPO.
metadata: {"navin":{"emoji":"🧪","category":"devops"}}
---

# Resilience Tester

## Overview

You turn reliability claims into measured results: a hypothesis, a bounded experiment, an abort condition and numbers.

## When to use

- Load or stress test before launch, capacity planning.
- Validate failover (Multi-AZ, replicas, autoscaling) and DR (restore, region switch).
- Chaos experiments on non-production, or on production with explicit approval.

## Engine

1. `infra action=graph op=build` and `op=impact node=<target>` to bound the blast radius.
2. `infra action=check command="k6 run ..."` - load against production and fault injection come back critical.
3. `infra action=plan` with steps: baseline -> experiment -> observe -> restore; `infra action=run`, `infra action=verify`, `infra action=recover` if the abort condition triggers.

## Workflow

1. **Hypothesis** - "p95 < 300 ms at 500 rps", "losing one AZ keeps error rate < 1 percent", "restore in < 30 min".
2. **Baseline** - current SLIs from observability.
3. **Experiment** - smallest scope first, ramp up; abort conditions defined (error rate, latency, budget burn).
4. **Observe** - metrics, logs, traces during the run.
5. **Restore** - return to steady state, verify.
6. **Report** - pass/fail per hypothesis, bottlenecks, fixes.

## Command families

```bash
k6 run --vus 50 --duration 5m script.js; k6 run --out json=result.json script.js
hey -z 60s -c 50 https://<host>/api; echo 'GET https://<host>/' | vegeta attack -rate=200 -duration=60s | vegeta report
curl -s -o /dev/null -w '%{http_code} %{time_total}\n' https://<host>/health
nuclei -u https://<host> -severity high,critical; zap-baseline.py -t https://<host>; trivy image <img>
kubectl delete pod -l app=<app> --wait=false  # pod-kill experiment
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
aws fis start-experiment --experiment-template-id <t>
aws rds reboot-db-instance --db-instance-identifier <id> --force-failover
kubectl apply -f chaos-mesh-experiment.yaml; litmusctl get chaos-experiments
```

## Safety rules

- Any load or fault injection on production, DB failover, node/AZ failure and security scans against systems you do not own are critical: user validation and written authorization.
- Always define the abort condition and the stop command before starting.
- Respect rate limits of third-party APIs; never load-test what you do not operate.

## Verification and rollback

- Verify steady state returned: SLIs back to baseline, all replicas Ready, no lingering experiments.
- Rollback: stop the experiment (`aws fis stop-experiment`, delete the chaos resource), uncordon nodes, scale back.

## Deliverable

Hypotheses with measured results, graphs/numbers (rps, p50/p95/p99, errors), RTO/RPO measured, findings and fixes, run ids.
