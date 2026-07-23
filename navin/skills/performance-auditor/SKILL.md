---
name: performance-auditor
description: Profile and optimize applications — hot paths, N+1 queries, blocking I/O, caching, bundle size, memory, startup time. Use for /turbo, "why is it slow?", or pre-launch performance passes.
metadata: {"navin":{"emoji":"⚡","category":"devops"}}
---

# Performance Auditor

## Overview

Find where time and memory actually go, prove it with measurements, and propose the highest-leverage optimizations. Rule number one: **measure before recommending** — no cargo-cult optimization.

## Hot spots by layer

| Layer | Typical issues |
|-------|----------------|
| Database | N+1 queries, missing indexes, `SELECT *`, unbounded result sets, no connection pooling |
| Backend | blocking I/O inside async code, sync file/network calls in hot paths, quadratic loops, chatty logging |
| Caching | recomputing pure results, missing HTTP cache headers, cache stampedes |
| Frontend | oversized bundles, unoptimized images, render waterfalls, missing memoization, layout thrashing |
| Memory | leaks (listeners, closures, caches without eviction), large object retention |
| Startup | eager imports, synchronous config fetches, unbounded migrations |

## Workflow

1. Establish the baseline: what is slow, by how much, and for whom? Get a number first (timer, profiler, `EXPLAIN ANALYZE`, Lighthouse, `time`).
2. Profile with what's available:
   - Python: `cProfile`, `py-spy`, `tracemalloc`
   - Node: `--cpu-prof`, `clinic`, Chrome DevTools
   - SQL: `EXPLAIN (ANALYZE, BUFFERS)`, slow query log
   - Web: Lighthouse, bundle analyzers, Web Vitals (LCP, INP, CLS)
3. Attribute cost: rank the top offenders by measured share of time/memory.
4. Propose optimizations sorted by **impact / effort ratio**, each with:
   - the measurement proving the problem,
   - the change,
   - the expected gain (estimate honestly),
   - the risk.
5. If asked to apply fixes: change one thing at a time and re-measure after each.

## Anti-patterns

- Optimizing without a baseline measurement
- Micro-optimizations while an N+1 query dominates
- Adding caches without an invalidation story
- Claiming precise speedups you did not measure
