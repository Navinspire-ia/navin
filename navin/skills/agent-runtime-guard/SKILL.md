---
name: agent-runtime-guard
description: Control what an AI agent and its MCP servers may do while they run - a PreToolUse policy (denied commands, protected paths, approved hosts, approved MCP servers and tools, no credentials in arguments), a redacted log of every tool call, and a session audit that finds denied calls, loops and bursts. Use when the user asks to "lock down what the agent can do", "block dangerous commands", "control MCP tool calls", "audit what the agent did", "stop the agent from touching production", or deploys Navin, a subagent or an MCP server where a wrong call has a real cost. In French "encadrer les actions de l'agent", "bloquer les commandes dangereuses", "auditer les appels d'outils", "controler les serveurs MCP".
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Agent Runtime Guard

## Overview

An agent acts through tools: a shell, file writes, git, cloud CLIs, MCP
servers. Each call runs with the user's rights, so the question at runtime is
not "is the model well behaved" but "is this call inside what the task
allows". This skill installs that check: a policy evaluated before every tool
call through a Navin `PreToolUse` hook, a log of each decision with secrets
redacted, and an audit of the session that shows what was denied, which MCP
servers were used, and where the agent looped. The posture is deny what is
clearly wrong, warn on what needs a human look, allow the rest, and keep
evidence. The guard never edits a tool call; it blocks with a precise reason
so the model takes another route.

## When it applies

- Navin runs with `exec`, write tools or `infra` on a machine where a wrong
  command costs something (production credentials, shared repositories,
  customer data).
- One or more MCP servers are declared, especially remote ones or servers
  with destructive tools.
- Subagents run in parallel and their actions must stay inside a scope.
- A compliance owner asks what the agent did during a session (DORA art. 9
  logging, ISO 27001:2022 A.8.15, NIS2 art. 21 access control) or an
  incident review needs the sequence of tool calls.
- The user asks to restrict the agent to a folder, a toolchain or a list of
  APIs before letting it work unattended.

## Engines and sources

| Layer | Mechanism | Where it lives |
|---|---|---|
| Policy decision | `scripts/guard_pretooluse.py`, a `PreToolUse` hook reading the event JSON on stdin and answering with Navin hook JSON | `<workspace>/.navin/hooks.json` (project), `~/.navin/hooks.json` (personal), or a plugin `hooks/hooks.json` |
| Static scope | `enabledTools` and `toolTimeout` on each MCP server; `allowed-tools` in a command or subagent frontmatter; `allowed_paths`, `allowed_commands`, `isolate=true` at `spawn` | `~/.navin/config.json` `tools.mcpServers`, `<plugin>/mcp.json`, agent files |
| Infra operations | the `infra` tool with its observe / prepare / execute levels, which asks before critical operations and snapshots before a change | cloud, Kubernetes, Terraform commands |
| Evidence | `--log tool-calls.jsonl` written by the hook; `scripts/audit_tool_log.py` reads it | `<workspace>/.navin/guard/` |
| Content checks | `prompt-injection-defender` on tool results, `permission-guard` for the least-privilege reasoning, `audit-logger` for the human-readable record | companion skills |

The hook event for `PreToolUse` carries `hook_event_name`, `session_id`,
`cwd`, `tool_name` and `tool_input` (the real tool arguments: `command` for
exec, `path` and `content` for write_file, `edits[]` for edit_file). Exit 0
with `{"hookSpecificOutput": {"permissionDecision": "deny",
"permissionDecisionReason": "..."}}` blocks the call and returns the reason to
the model as the tool result; `{"systemMessage": "..."}` shows a line to the
user without blocking. The `hook-development` skill has the complete event
and output reference.

## Workflow

1. **Inventory the surface.** List the tools the task needs (read, grep,
   edit, exec with which binaries, git, infra, which MCP servers and which
   of their tools) and the targets it must never touch (production profiles,
   credential files, folders outside the project, hosts outside the approved
   APIs). Write both lists down; they become the policy.
2. **Vet each MCP server** before it is enabled, with
   `references/mcp-controls.md`: origin, transport and identity, the full
   tool list with its descriptions (tool poisoning hides there), the scope of
   the token behind it. Restrict with `enabledTools` and `toolTimeout` in the
   declaration so the unneeded tools do not exist at all.
3. **Write the policy.** Copy `assets/policy.example.json` to
   `<workspace>/.navin/guard/policy.json` and adjust it with
   `references/policy-schema.md`. Start from the sample deny rules; leave
   `network.allow_hosts` at `null`, `commands.allow_binaries` and
   `mcp.allow_servers` empty for the first session. Check every regex loads:

   ```bash
   python3 scripts/guard_pretooluse.py --policy assets/policy.example.json --event examples/event-mcp-call.json --json
   ```

4. **Dry-run the policy on real events.** The examples folder holds three
   events: a download piped to `sudo bash`, a harmless MCP search, a write of
   `.env` with a key inside. The expected decisions are deny
   (`commands.deny_remote_scripts`), allow, deny (`paths.deny`):

   ```bash
   python3 scripts/guard_pretooluse.py --policy assets/policy.example.json --event examples/event-remote-script.json
   python3 scripts/guard_pretooluse.py --policy assets/policy.example.json --event examples/event-write-env.json
   ```

   Add one event file per rule the user cares about and keep them next to
   the policy; they are the regression tests of the policy.
5. **Install the hook.** Copy the two scripts and the policy to
   `<workspace>/.navin/guard/`, then add the `PreToolUse` and `Stop` entries
   from `examples/hooks.json` to `<workspace>/.navin/hooks.json`. The
   matcher lists the tools worth checking (`exec`, the write tools, `infra`,
   `mcp_.*`); read-only tools are left out so the hook costs nothing there.
   A project `hooks.json` runs only once the user trusted it: say so and let
   the user accept the trust prompt. Validate the file with the hook schema
   checker of the `hook-development` skill when the format is in doubt.
6. **Run one normal session and read the log.** After the first real task:

   ```bash
   python3 scripts/audit_tool_log.py examples/tool-calls.sample.jsonl
   ```

   on the real `tool-calls.jsonl`. The report lists calls per tool,
   decisions, denied calls with their rule, MCP servers used, repeated
   identical calls, bursts, hosts contacted and paths written. Exit 1 means
   there is at least one denied call, loop or burst to look at.
7. **Tighten from evidence.** Turn the hosts table into
   `network.allow_hosts`, the binaries seen into `commands.allow_binaries`,
   the servers seen into `mcp.allow_servers`. Allow lists written from a
   real session are precise; allow lists written from imagination block the
   agent on its second task. Promote a warn that is always wrong into a
   deny; narrow a warn that fires on every call.
8. **Scope subagents too.** Hooks apply to the main agent and its
   subagents in the shared tree, but an isolated subagent has its own
   checkout: pass `allowed_paths`, `allowed_commands` and `isolate=true` at
   `spawn` so the same limits hold there.
9. **Deliver the evidence.** Give the policy, the event tests and the audit
   report (section below). For an incident, the JSONL log is the timeline:
   each line has the timestamp, session, tool, trimmed arguments and the
   decision.

## Rules

- Deny through the JSON decision, never through exit 2 from the guard
  script. A policy file with a bad regex must produce a visible error on
  stderr, not a silent block of every tool; the script exits 2 only when the
  policy or the event cannot be read, and Navin logs that as a hook error.
- A `permissionDecision` of `allow` does not bypass Navin's own permission
  checks; the guard only adds restrictions on top of them.
- Hooks cannot rewrite `tool_input`. When the agent needs an alternative,
  the deny reason names it ("use an environment variable or the vault").
- Match paths after expansion: `~/.ssh` in the policy must also catch
  `/home/me/.ssh/id_rsa` and `../.ssh`; the script does this against the
  event `cwd`.
- The host check sees arguments only. A URL built inside a Python one-liner
  is invisible to it; bound the binaries (`allow_binaries`) and rely on
  sandbox or egress rules for the rest, and say so in the report.
- Secrets never reach the log: the same detector that denies a credential
  in arguments redacts it before the line is written. Keep
  `secrets.deny_in_arguments` on; a key that must be stored goes through
  `request_credentials` or the connectors UI, not through a tool argument.
- Keep `mcp.deny_tools` for destructive shapes (`delete`, `drop`,
  `execute_sql`, `run_shell`) even when `enabledTools` already hides them:
  the declaration can change, the hook stays.
- `git` has its own safety (the `git` tool asks before a force push); the
  policy still denies `git push --force` and `git reset --hard` through
  `exec`, which is where a shortcut would go.
- Three identical calls in a row is the default loop threshold; raise it to
  5 to 8 on long builds where `pytest` legitimately runs several times. The
  report shows the exact repeated arguments, so the call is made on evidence.
- Do not log the full content of file writes: strings are cut at 400
  characters. The path and the decision are the evidence; the content is in
  git.
- A session audit that found nothing is still a result: report the counts,
  the servers used and the hosts contacted, so the reviewer sees what was
  covered.

## Deliverable

1. **Policy pack** in `<workspace>/.navin/guard/`: `policy.json`, the two
   scripts, the event tests, and the `hooks.json` entries. State which allow
   lists are active and which are still open (`allow_hosts: null`).
2. **Dry-run table**, one row per event test:

   | Event | Tool | Expected | Got | Rule |
   |---|---|---|---|---|

3. **Session audit** (the Markdown printed by `audit_tool_log.py`): calls
   per tool, decisions, denied calls (When, Tool, Rule, Reason), MCP servers
   used, loops, bursts, hosts, paths written. Add one line of judgment per
   denied call and per loop: expected behaviour, policy too strict, or a
   real attempt to leave the scope.
4. **Gaps**: what the guard cannot see in this setup (runtime-built hosts,
   isolated subagents without spawn limits, tool results) and the control
   that covers each gap, from `references/detections.md`.

Produce the HTML version with `studio-html-report` when the user wants a
report to share.

## Resources

- `scripts/guard_pretooluse.py` - the PreToolUse hook: evaluates one call
  against the policy, prints the Navin decision, appends a redacted line to
  the log. Also the dry-run tool (`--event`, `--json`).
- `scripts/audit_tool_log.py` - the session audit over the JSONL log:
  per-tool counts, denied and warned calls, MCP servers, loops, bursts,
  hosts, written paths; exit 1 when there is something to look at.
- `assets/policy.example.json` - the starting policy: deny lists for
  commands, paths, MCP tools and credentials, three warn rules, allow lists
  left open.
- `examples/hooks.json` - the `PreToolUse` and `Stop` entries to add to
  `.navin/hooks.json`.
- `examples/event-remote-script.json`, `examples/event-mcp-call.json`,
  `examples/event-write-env.json` - event tests for the dry run (expected:
  deny, allow, deny).
- `examples/tool-calls.sample.jsonl` - a sample log with two denials, one
  warning and a loop, to see the audit output before a real session.
- `references/policy-schema.md` - every policy key, its type, matching
  rules and evaluation order; the decision output and the log line format.
- `references/mcp-controls.md` - vetting checklist for an MCP server, the
  Navin declaration limits, runtime risks (tool poisoning, rug pull,
  confused deputy) and their controls.
- `references/detections.md` - the catalogue of runtime signals RG-01 to
  RG-18 with the rule that catches each, tuning advice, and what the hook
  cannot see.
