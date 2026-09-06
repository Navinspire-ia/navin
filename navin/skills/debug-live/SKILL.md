---
name: debug-live
description: Live debugging workflow - reproduce first, DebugMCP breakpoints, conversational state questions, isolated repair branch, before/after tests, repair report. Use for /debug and hard production bugs.
metadata: {"navin":{"emoji":"🪲","category":"devops"}}
---

# Debug Live

## Pipeline

```
Erreur → reproduce → mcp_status → breakpoint → inspect stack/vars → hypotheses
  → fix on isolated branch → tests → before/after → repair report
```

## Tools

- `exec` - repro script and test suite
- `debug_repair(action=mcp_status)` - probe DebugMCP at `http://127.0.0.1:3001/mcp`
- `debug_repair(action=start_branch|status|report)` - isolation + HTML report
- MCP preset **debugmcp** (Settings → MCP): real DAP tools when the extension is up
  - `add_breakpoint`, `start_debugging`, `list_variable_names` → `get_variables_values`
  - `evaluate_expression`, `step_*`, `continue_execution`, `pause_execution`
- ChatDBG-style: treat user questions about live state as first-class
  (`why is user None here?`) - answer with REAL evidence from stack/vars/evaluate

## Rules

1. Never edit before a failing repro exists.
2. Call `mcp_status` before guessing - use live breakpoints when reachable.
3. Symptom ≠ root cause - keep asking until the earliest wrong state is clear.
4. Fetch variables by name only (least privilege) - do not dump entire scopes.
5. Put fixes on `debug_repair(start_branch)` unless the user forbids git.
6. Same command must fail before and pass after.
7. End with `debug_repair(report)` including `findings` + `latent_bugs` when
   known; File Preview opens automatically on `debug-report-*.html`.
