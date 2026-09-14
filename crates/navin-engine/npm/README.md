# navin-engine

**Your agent writes the patch. This proves it.**

This package is a thin launcher for [`navin-engine`](https://github.com/navinspire-ai/navin-engine),
a Rust binary that boots your app in a throwaway shadow workspace, attacks it
(load, kill/recovery, malformed input, connection floods, network chaos),
measures it, applies a candidate patch, measures again, and reports what
actually changed. It never edits your workspace: accepted changes land on
their own git branch, or in a reviewable patch bundle when there is no git.

It measures HTTP services on localhost (several routes, POST bodies and auth
headers via `.navin/evolve.toml`) and port-less workers or CLIs
(`[target] kind = "worker"`). Built inside the Navin desktop app, extracted
so any project - Cursor, Claude Code, Codex, plain CI - can embed it.

The point of the npm package is that an MCP config becomes one line, with no
Rust toolchain to install:

```json
{
  "mcpServers": {
    "navin-engine": {
      "command": "npx",
      "args": ["-y", "navin-engine", "mcp"]
    }
  }
}
```

On first run the launcher downloads the release binary for your platform,
verifies its SHA-256 checksum, and caches it under
`~/.cache/navin-engine/<version>` (`%LOCALAPPDATA%` on Windows). Later runs
start instantly. Set `NAVIN_ENGINE_BIN` to use your own build instead, and
`NAVIN_ENGINE_HOME` to move the cache.

Full documentation, the tool reference and the per-environment wiring guide
(Cursor, Claude Code, Codex, Gemini CLI, OpenCode, Antigravity, Windsurf) are
in the [repository](https://github.com/navinspire-ai/navin-engine).

MIT licensed.
