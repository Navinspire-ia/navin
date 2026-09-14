# Wiring the engine into your AI coding environment

`navin-engine mcp` speaks the Model Context Protocol on stdin/stdout. Any host
that can launch a local MCP server can use it, and the entry is always the
same command:

```
navin-engine mcp
```

Every snippet below assumes the binary is on your `PATH`. If it is not, either
use its absolute path, or let npm fetch it by replacing `"command":
"navin-engine"` and `"args": ["mcp"]` with `"command": "npx"` and `"args":
["-y", "navin-engine", "mcp"]`.

Two things are worth setting everywhere: a **generous tool timeout**, because
a proof or a benchmark takes minutes, and the **project root**, which defaults
to the directory the host launched the server in. Pass a path explicitly
(`navin-engine mcp /path/to/project`) if your host starts servers somewhere
else, or send `path` as a tool argument per call.

## Cursor

`.cursor/mcp.json` in the project, or `~/.cursor/mcp.json` for every project:

```json
{
  "mcpServers": {
    "navin-engine": {
      "command": "navin-engine",
      "args": ["mcp"]
    }
  }
}
```

## Claude Code

```bash
claude mcp add navin-engine -- navin-engine mcp
```

Or commit `.mcp.json` so the whole team gets it, with an explicit ceiling for
long tool calls:

```json
{
  "mcpServers": {
    "navin-engine": {
      "command": "navin-engine",
      "args": ["mcp"],
      "timeout": 1800000
    }
  }
}
```

The per-server `timeout` is milliseconds and is a hard wall-clock limit per
tool call - progress notifications do not extend it, so give a deep proof
room. Startup is governed separately by `MCP_TIMEOUT`; the engine starts
instantly, so the default is fine.

## Codex (CLI, IDE extension, ChatGPT desktop)

```bash
codex mcp add navin-engine -- navin-engine mcp
```

Or `~/.codex/config.toml` (project-scoped: `.codex/config.toml`, trusted
projects only). Note the underscore in `mcp_servers`, and raise
`tool_timeout_sec`: it defaults to 60 seconds, which is shorter than a proof.

```toml
[mcp_servers.navin-engine]
command = "navin-engine"
args = ["mcp"]
tool_timeout_sec = 1800
```

## Gemini CLI

`~/.gemini/settings.json`, or `.gemini/settings.json` in the project:

```json
{
  "mcpServers": {
    "navin-engine": {
      "command": "navin-engine",
      "args": ["mcp"],
      "timeout": 1800000
    }
  }
}
```

`timeout` is milliseconds per request and defaults to ten minutes, which is
enough for `quick` and `standard` profiles but not always for `deep`.

## OpenCode

`opencode.json` at the project root. The command is an array, environment
variables go in `environment`, and the root key is `mcp`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "navin-engine": {
        "type": "local",
        "command": ["navin-engine", "mcp"],
        "timeout": 1800000
      }
    }
  }
}
```

On OpenCode 1.x the server sits directly under `mcp` rather than under
`mcp.servers`; drop the extra nesting if your version rejects it.

## Antigravity

Open the agent side panel, `...` -> **MCP Servers** -> **Manage MCP Servers**
-> **View raw config**, which is the reliable way to reach the file since its
location has moved between versions (`~/.gemini/antigravity/mcp_config.json`
globally, `.agents/mcp_config.json` per workspace). Then:

```json
{
  "mcpServers": {
    "navin-engine": {
      "command": "navin-engine",
      "args": ["mcp"]
    }
  }
}
```

Antigravity recommends keeping the total number of enabled tools under fifty;
this server adds nine.

## Windsurf

`~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "navin-engine": {
      "command": "navin-engine",
      "args": ["mcp"]
    }
  }
}
```

## Anything else

Hosts that read an `mcpServers` block (Zed, Continue, JetBrains AI, Copilot
agent mode, and most newcomers) take the same two fields: `command` set to
`navin-engine` and `args` set to `["mcp"]`. If a host cannot launch MCP
servers at all, it can still shell out:

```bash
navin-engine diagnose --profile quick
navin-engine optimize --candidates variants.json
```

Both print JSON on stdout and logs on stderr, which is exactly what an agent
with a terminal tool needs. The wire-level reference for the MCP side is in
[mcp.md](mcp.md).

## In CI

The engine is a single binary and needs no model, so a pipeline can gate a
merge on measured robustness:

```yaml
- run: navin-engine proof --profile standard > proof.json
- run: |
    test "$(jq -r .verdict proof.json)" != "fail"
```

## Troubleshooting

**The host shows no tools.** Run `navin-engine mcp` by hand: it should sit
silently waiting for JSON on stdin. If the binary is not on the host's `PATH`
(GUI apps often have a narrower `PATH` than your shell), use an absolute
path in `command`.

**A tool call returns "cannot find where the app answers".** The engine
booted every start command it could find and none opened a port. Run
`navin-engine inspect` to see what was detected, and pass `start` explicitly
for that call.

**Everything times out.** Raise the host's per-call timeout as above, or use
`profile: "quick"` while iterating and keep `deep` for the final check.

**Logs.** The engine writes tracing output to stderr, which your host
captures. `NAVIN_ENGINE_LOG=debug` turns up the volume; stdout stays a clean
protocol channel no matter what.
