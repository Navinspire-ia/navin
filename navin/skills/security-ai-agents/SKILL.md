---
name: security-ai-agents
description: Audit prompts, RAG, tools, MCP and exfiltration. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Ai Agents (Navin Security)

## Overview

Audit LLM applications and agents: prompts, tools, retrieval, MCP servers and data flows.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: ai, mcp.
- Families: ai_agents.

## Controls covered

| Control | Level | Check |
|---|---|---|
| C6.01 | L2 | Direct and indirect prompt injection |
| C6.02 | L1 | Data/instruction separation for external content |
| C6.03 | L1 | Tool permissions and confirmation of sensitive actions |
| C6.04 | L3 | Excessive agency: limits, human approval, budget |
| C6.05 | L3 | RAG poisoning and corpus integrity |
| C6.06 | L2 | Exfiltration through tools, URLs, images, markdown |
| C6.07 | L1 | MCP servers: provenance, permissions, isolation, versions |
| C6.08 | L1 | Secrets and sensitive data in prompts and logs |
| C6.09 | L2 | Model endpoints: authentication, quotas, abuse |
| C6.10 | L1 | Sandbox for generated code execution |

## Engines

- Registered scanners (use `navin.security.scanners`, results arrive as normalized findings): gitleaks.
- Other engines named by the catalog: garak, navin_ai, navin_rules, promptfoo. Use one only when it is installed.
- A scanner that is not installed or did not run means the check was not done, not that it passed.

## Method

1. Map what untrusted text reaches the model: user input, web pages, files, tool output, retrieved documents.
2. Instructions and data stay separated; external content is quoted as data and never granted authority.
3. Tools: least privilege, confirmation for sensitive actions, argument validation, limits on rate, spend and steps.
4. Exfiltration paths: links, images and markdown that carry data out, tool calls to attacker-chosen URLs.
5. RAG: who can write to the corpus, provenance of documents, poisoning detection.
6. MCP servers: provenance, pinned versions, scoped permissions, isolation.
7. Secrets and personal data in prompts, traces and logs; generated code runs in a sandbox.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Testing prompt injection with payloads that act on real accounts.
- Treating the system prompt as a security boundary.
- Treating a clean scan as proof of security: say which controls were not assessed.
