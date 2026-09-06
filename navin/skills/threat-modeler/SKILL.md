---
name: threat-modeler
description: Map the attack surface and build a STRIDE threat model - trust boundaries, data flows, entry points, assets, and prioritized threats with mitigations. Use for /threatmap, architecture security reviews, or "what could go wrong?" planning.
metadata: {"navin":{"emoji":"🗺️","category":"security"}}
---

# Threat Modeler

## Overview

Reason about security at the design level, before (or alongside) code-level scanning. The output is a structured threat model: what we protect, who might attack, how, and what stops them. This is analysis, not exploitation - it feeds the other security skills.

## Method

1. **Decompose the system** - identify assets (data, credentials, funds, availability), entry points (HTTP routes, queues, file uploads, CLI, webhooks, LLM tool calls), and external dependencies.
2. **Draw trust boundaries** - where data crosses from untrusted to trusted (client→server, internet→internal, tenant→tenant, user→agent-tool). Every boundary is a review point.
3. **Trace data flows** - for each entry point, follow user-controlled data to where it is stored, executed, or reflected.
4. **Apply STRIDE** per element:
   - **S**poofing - identity/authentication weaknesses
   - **T**ampering - integrity of data in transit/at rest
   - **R**epudiation - missing audit trails
   - **I**nformation disclosure - leaks, verbose errors, over-broad responses
   - **D**enial of service - unbounded work, missing rate limits
   - **E**levation of privilege - authz gaps, IDOR, sandbox escape
5. **Rate** each threat by likelihood × impact (or DREAD) and attach a concrete mitigation and the owning component.

## Deliverable

- An asset & entry-point inventory.
- A trust-boundary map (describe or render a Mermaid data-flow diagram).
- A ranked threat table: `ID | STRIDE | element | threat | likelihood | impact | mitigation`.
- The top attack paths worth deeper testing - hand these to `/probe` or `/redteam`.

## Notes

- For multi-tenant SaaS, treat tenant isolation as a first-class boundary (row-level scoping, per-tenant keys).
- For LLM agents, model prompt injection, tool-permission scope, and data exfiltration via tools as explicit threats.
- Keep it grounded in this system's real components - no generic checklists.
