# Runtime detections

Signals that a session is drifting, with where to look and the rule that
catches each one. Severity is what to do when it fires, not how often it
happens.

| Id | Signal | Where | Rule or check | Severity |
|---|---|---|---|---|
| RG-01 | Download piped to a shell | `exec` command | `commands.deny_remote_scripts` | deny |
| RG-02 | Privilege escalation (`sudo`, `su`, `doas`) | `exec` command | `commands.deny_sudo`, `deny_binaries` | deny |
| RG-03 | Destructive command on a root or shared path (`rm -rf /`, `mkfs`, `dd`, `chmod -R /`) | `exec` command | `commands.deny_patterns`, `deny_binaries` | deny |
| RG-04 | History rewrite or force push | `exec` command, `git` tool | `commands.deny_patterns` (`git push --force`, `git reset --hard`); the `git` tool itself asks before a force push | deny |
| RG-05 | Infrastructure change without a plan (`terraform apply -auto-approve`, `kubectl delete ns`) | `exec`, `infra` | `commands.deny_patterns`; prefer the `infra` tool, which enforces observe / prepare / execute | deny |
| RG-06 | Read of a credential file (`~/.ssh`, `~/.aws/credentials`, `.env`, `*.pem`) | any tool path argument | `paths.deny` | deny |
| RG-07 | Write outside the project | write tools | `paths.allow_write` | deny |
| RG-08 | Credential pasted into arguments | any tool | `secrets.deny_in_arguments` | deny |
| RG-09 | Contact with a host not on the list | URL or host flag in arguments | `network.allow_hosts` | deny |
| RG-10 | Unapproved MCP server or destructive MCP tool | `mcp_*` tool name | `mcp.allow_servers`, `mcp.deny_tools` | deny |
| RG-11 | Oversized MCP argument payload | `mcp_*` arguments | `mcp.max_arg_chars` | deny |
| RG-12 | Production named in a command | `exec`, `infra`, `mcp_*` | warn rule `production-mention` | warn, confirm the environment |
| RG-13 | Recursive delete | `manage_files` | warn rule `bulk-delete` | warn |
| RG-14 | Upload of a local file to a remote host | `exec` with `curl -F`, `-T`, `--data-binary` | warn rule `outbound-upload` | warn |
| RG-15 | Same call repeated N times (loop) | log | `audit_tool_log.py --loop-threshold` | finding; stop the loop, fix the cause |
| RG-16 | Burst of calls in one minute | log | `audit_tool_log.py --burst-threshold` | finding; look for a runaway subagent |
| RG-17 | Tool result containing instructions to the model | PostToolUse `tool_response` | `prompt-injection-defender`; a PostToolUse hook that appends a reminder when the response contains "ignore previous", "you must now", `<system>` | warn |
| RG-18 | Agent finishing with denied calls or loops in the session | Stop | Stop hook running `audit_tool_log.py` (see `examples/hooks.json`) | systemMessage |

## Tuning

- Start with the sample policy (deny list only, `allow_hosts: null`,
  `allow_servers: []`). Run one normal session, review the log, then turn on
  `allow_hosts`, `allow_binaries` and `allow_servers` from what the log
  shows. Allow lists built from a real session are tight and rarely wrong.
- A warn that fires on every call is noise; narrow its `tools` or its
  `pattern`, or promote it to a deny if it is always wrong.
- `loop-threshold` 3 fits a short task; 5 to 8 for a long build where
  `pytest` runs legitimately several times. The report shows the exact
  repeated arguments so the judgment is on evidence.
- Keep `secrets.deny_in_arguments` on. The one legitimate case (a key being
  written to a vault) goes through `request_credentials` or the connectors
  UI, never through a tool argument.

## What the hook cannot see

- Hosts resolved inside a script or an interpreter (`python -c "requests.get(...)"`
  with the URL built at runtime). Bound the binaries and the network at the
  sandbox or egress level for that.
- The content a tool returns: that is PostToolUse territory
  (`prompt-injection-defender`).
- Tools that run inside a subagent's isolated worktree with its own hooks
  disabled: give subagents `allowed_paths` and `allowed_commands` at spawn
  time as well.
