# Plugin features for commands

## ${NAVIN_PLUGIN_ROOT}

Absolute path of the installed plugin folder. Navin substitutes it:

- in the command body: `Read ${NAVIN_PLUGIN_ROOT}/docs/style.md`
- in `@` references: `@${NAVIN_PLUGIN_ROOT}/templates/pr.md`
- in the environment of `` !`cmd` `` snippets: `` !`bash "$NAVIN_PLUGIN_ROOT/scripts/stats.sh"` ``

Use it for every file the plugin ships; the install location differs per user
and per machine.

## Discovery

- Default folder: `<plugin>/commands/`, scanned recursively for `*.md`.
- Extra folders: `"commands": ["extra-commands"]` (or a single string) in
  `plugin.json`; relative paths only, no `..`.
- Only enabled plugins are scanned (`/pack enable <name>`).

## Names and clashes

- `/<name>` resolves to the first match: project, personal, plugin, shipped.
- `/<plugin>:<name>` always targets that plugin, even when a project command
  shadows the short name.
- Prefix names when a clash is likely: `docs-build` rather than `build`.

## Working with the other components

| Component | How a command uses it |
|---|---|
| Skill | "Read the skill `<name>`" in the body; Navin loads it with the skill tool |
| Agent | "Use spawn with agent=\"<name>\""; plugin agents are discovered like project agents |
| Hook | hooks run on the tool calls the command makes; nothing to wire |
| MCP server | name its tools (`mcp_<plugin>-<server>_<tool>`) in `allowed-tools` when restricting |
| Settings | inline `@.navin/<plugin>.local.md` to read per-project configuration |

## Scripts

Ship helper scripts in `<plugin>/scripts/` and call them through the shell
tool from the instructions, or through `!` snippets for read-only output.
Make them executable and start them with a shebang.
