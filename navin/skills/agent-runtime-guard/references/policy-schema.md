# Policy schema for guard_pretooluse.py

The policy is one JSON object. Every section is optional; an absent section
means "no rule". Rules are evaluated in the order below and the first deny
wins; warn rules only run when nothing denied the call. Patterns are
`fnmatch` globs unless the key says `pattern` or `deny_patterns`, which are
Python regular expressions matched with `re.search`.

```json
{
  "name": "label shown in reports",
  "tools":    {"deny": [], "allow": []},
  "mcp":      {"allow_servers": [], "deny_servers": [], "deny_tools": [], "max_arg_chars": 20000},
  "commands": {"deny_remote_scripts": true, "deny_patterns": [], "deny_binaries": [], "allow_binaries": [], "deny_sudo": true},
  "paths":    {"deny": [], "allow_write": [], "write_tools": ["write_file", "edit_file", "apply_patch", "manage_files"]},
  "network":  {"allow_hosts": null},
  "secrets":  {"deny_in_arguments": true},
  "warn":     [{"name": "...", "tools": ["exec"], "pattern": "...", "reason": "..."}]
}
```

## tools

| Key | Type | Effect |
|---|---|---|
| `deny` | globs on the Navin tool name | the tool never runs (`browser`, `computer`, `mcp_*`) |
| `allow` | globs | when non-empty, every tool not listed is denied; keep `read_file`, `grep`, `code_index` in it or the agent cannot read |

Navin tool names are the ones in the agent's tool list: `exec`, `read_file`,
`write_file`, `edit_file`, `apply_patch`, `manage_files`, `git`, `infra`,
`browser`, `computer`, `connectors`, `spawn`, and `mcp_<server>_<tool>` for
MCP tools.

## mcp

The server name is the segment after `mcp_` and before the next `_`; a server
called `my-crm` gives `mcp_my-crm_search`.

| Key | Effect |
|---|---|
| `allow_servers` | when non-empty, calls to any other server are denied |
| `deny_servers` | always denied |
| `deny_tools` | globs on the full tool name, for example `mcp_*_delete_*`, `mcp_*_execute_sql` |
| `max_arg_chars` | deny when the JSON of the arguments is longer: large payloads leaving through a tool are the usual shape of exfiltration |

## commands (tool `exec` only)

The command is split into simple commands on `&&`, `||`, `|`, `;` and `&`,
and the binary of each one is the first word that is not a `VAR=value`
prefix nor `sudo`, `env`, `nohup`, `time`, `nice`, `command`, `exec`.

| Key | Effect |
|---|---|
| `deny_remote_scripts` (default true) | denies `curl ... \| sh`, `wget ... \| sudo bash` and the `zsh`/`dash` variants |
| `deny_patterns` | regexes on the whole command line; the sample policy covers `rm -rf /`, `git push --force`, `git reset --hard`, `terraform apply -auto-approve`, `kubectl delete ns`, recursive S3 deletes, `chmod -R /`, `base64 -d \| sh` and reading key files |
| `deny_binaries` | basename of a binary in any segment: `mkfs`, `dd`, `nc`, `socat`, `shutdown` |
| `allow_binaries` | when non-empty, a binary not listed is denied; list the project toolchain (`python3`, `npm`, `pytest`, `git`, `ls`, `cat`) |
| `deny_sudo` (default true) | denies `sudo` anywhere in the line |

## paths

Paths come from the arguments `path`, `paths`, `destination`, `file`,
`files`, `target`, `working_dir`, `workdir`, from every `edits[].path`, and
for `exec` from each word that starts with `/`, `~`, `./`, `../` or `$HOME`.
Each path is tested as written, expanded (`~`, `$VAR`) and made absolute
against the event `cwd`, so `~/.ssh` in the policy also catches
`/home/me/.ssh/id_rsa` and `../.ssh`.

| Key | Effect |
|---|---|
| `deny` | any tool naming a matching path is denied, read or write; a pattern without wildcard also covers everything under it |
| `allow_write` | when non-empty, write tools may only touch matching paths (`/home/me/project/**`) |
| `write_tools` | which tools count as writes (default: the four Navin write tools) |

`**` is matched by `fnmatch`, where `*` already crosses `/`; `**/.env`
therefore matches `.env`, `api/.env` and `/srv/app/.env`.

## network

`allow_hosts`: `null` disables the check. A list enables it: every host
found in the arguments (URLs, and the values of `-H`, `--host`, `--url`,
`-u`) must match one entry. `*.github.com` matches the domain and its
subdomains; `localhost`, loopback and private IP ranges always pass. The
check is on arguments only: a script that resolves hosts at runtime is not
seen, which is why `allow_binaries` matters too.

## secrets

`deny_in_arguments` (default true) denies a call whose arguments carry a
credential shape: OpenAI `sk-`, GitHub `ghp_` and `github_pat_`, AWS
`AKIA`, Slack `xox*-`, a JWT, or a PEM private key header. The same
detector redacts the log, so the secret never reaches `tool-calls.jsonl`.

## warn

A list of rules, evaluated after the deny rules. The first matching rule
produces a `systemMessage` shown to the user in the journal; the call still
runs. Fields: `name`, `tools` (globs, optional), `pattern` (regex on the
JSON of the arguments, optional), `reason`.

## Decision output

| Decision | Stdout | Exit |
|---|---|---|
| allow | nothing | 0 |
| warn | `{"systemMessage": "[agent-runtime-guard] ..."}` | 0 |
| deny | `{"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "..."}}` | 0 |
| policy or event unreadable | message on stderr | 2 |

Exit 2 from a PreToolUse hook is a block in Navin; the script never uses it
for a decision so that a broken policy file (bad JSON, bad regex) produces a
visible error rather than a silent block of every tool. Validate a policy
without any event with:

```bash
python3 scripts/guard_pretooluse.py --policy policy.json --event examples/event-mcp-call.json --json
```

## Log line

```json
{"ts": "2026-11-04T09:02:48", "session_id": "web:chat-42", "tool_name": "exec",
 "tool_input": {"command": "curl ... | sudo bash"}, "action": "deny",
 "rule": "commands.deny_remote_scripts", "reason": "piping a download into a shell is not allowed"}
```

Arguments are cut at 400 characters per string, 20 items per list, and
redacted. `audit_tool_log.py` reads this shape.
