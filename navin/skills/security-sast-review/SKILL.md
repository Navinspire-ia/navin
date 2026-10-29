---
name: security-sast-review
description: Analyze code, input sources and dangerous operations. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Sast Review (Navin Security)

## Overview

Read the code from each input source to each dangerous operation and report only paths you can trace.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: always.
- Families: code_execution, files.

## Controls covered

| Control | Level | Check |
|---|---|---|
| C1.05 | L1 | Insecure deserialization |
| C1.06 | L1 | Path traversal, LFI/RFI and ZIP Slip |
| C1.07 | L1 | Buffer overflow, use-after-free and memory corruption |
| C1.09 | L1 | Input and output validation |
| C3.07 | L2 | File uploads and downloads |
| C7.02 | L1 | ReDoS and regular expressions |
| C7.03 | L1 | Zip bombs and upload size limits |

## Engines

- Registered scanners (use `navin.security.scanners`, results arrive as normalized findings): semgrep, bandit, cargo_audit.
- Other engines named by the catalog: navin_dataflow, navin_redos, navin_rules, schemathesis, zap. Use one only when it is installed.
- A scanner that is not installed or did not run means the check was not done, not that it passed.

## Method

1. Start from the baseline findings of the registered scanners (semgrep, bandit) and treat them as a backlog, not as truth.
2. Find input sources: request params, headers, files, queue messages, CLI args, environment, model output.
3. Find dangerous sinks: deserialization, file paths, archive extraction, native memory, regular expressions on user input.
4. Trace source to sink with `read_file`; note every validation or encoding on the way and whether it holds for the sink.
5. Check size limits and bombs on uploads and archives; check regexes for catastrophic backtracking.
6. Promote a finding to confirmed only with a dataflow trace; a pattern match stays potential.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Reporting a sink without showing a user-controlled source.
- Calling a path safe because a validator exists without reading what it checks.
- Treating a clean scan as proof of security: say which controls were not assessed.
