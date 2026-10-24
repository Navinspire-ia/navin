---
name: plugin-structure
description: Lay out, scaffold, package and install a Navin plugin (plugin.json manifest, skills/, agents/, commands/, hooks/hooks.json, mcp.json, ${NAVIN_PLUGIN_ROOT}). Use when the user asks to "create a plugin", "scaffold a plugin", "package these skills as a plugin", "set up plugin.json", "how are plugins organized", "where does this component go", "install or share a plugin", or needs the discovery rules for plugin components.
---

# Plugin Structure

A Navin plugin is a folder that bundles reusable extensions: skills, subagents,
slash commands, lifecycle hooks and MCP servers. Every component is a plain file
that Navin discovers by convention, so a plugin needs no build step and no
registration code.

Read this skill before creating or reorganizing a plugin. For the content of
each component, load the dedicated skill afterwards: `skill-development`,
`agent-development`, `command-development`, `hook-development`,
`mcp-integration`, `plugin-settings`. The `/create-plugin` command runs the
whole guided workflow.

## Three levels, one format

The same component files work at three levels. Start at the project level while
iterating, then move the files into a plugin to share them.

| Level | Location | Visible from |
|---|---|---|
| Project | `<workspace>/.navin/` (`skills/`, `agents/`, `commands/`, `hooks.json`) | that workspace only |
| Personal | `~/.navin/` (`skills/`, `agents/`, `commands/`, `hooks.json`) | every workspace of this user |
| Plugin | `~/.navin/plugins/<name>/` once installed | every workspace, while enabled |

On a name clash the project wins over personal, and personal wins over plugins.
Built-in Navin commands (`/new`, `/forge`, ...) always win over a custom command.

## Directory layout

```
my-plugin/
├── plugin.json              # manifest: identity and metadata
├── README.md                # what it does, how to use it
├── skills/
│   └── <skill-name>/
│       ├── SKILL.md         # required in each skill folder
│       ├── references/      # detailed docs, read on demand
│       ├── examples/        # working samples
│       └── scripts/         # helper scripts
├── agents/
│   └── <agent-name>.md      # subagents, run with spawn(agent="<name>")
├── commands/
│   └── <command>.md         # slash commands /<command> and /<plugin>:<command>
├── hooks/
│   ├── hooks.json           # lifecycle hooks
│   └── scripts/             # scripts the hooks call
├── mcp.json                 # MCP servers: {"mcpServers": {...}}
└── scripts/                 # shared helpers
```

Rules that matter:

- Component folders sit at the plugin root, next to `plugin.json`. Never nest
  them inside another folder.
- Only create the folders the plugin uses. An empty `commands/` is noise.
- A plugin must ship at least one component (a skill, an agent, a command, a
  hooks file or an MCP server). Installation refuses an empty plugin.
- Use kebab-case for every file and folder name: `code-review.md`,
  `api-testing/`, `validate-input.sh`.

## The manifest

`plugin.json` sits at the plugin root. Only `name` matters for discovery; the
rest is metadata shown in the Plugins panel and in `/pack list`.

```json
{
  "name": "release-helper",
  "displayName": "Release Helper",
  "version": "1.0.0",
  "description": "Prepare, check and publish releases",
  "author": {"name": "Jane Doe", "email": "jane@example.com"},
  "homepage": "https://example.com/release-helper",
  "repository": "https://github.com/jane/release-helper",
  "license": "MIT",
  "keywords": ["release", "changelog"]
}
```

- `name`: lowercase, digits, `.`, `_` or `-`, starting with a letter or digit,
  at most 64 characters. It becomes the install folder, the namespace of the
  plugin commands (`/release-helper:prepare`) and the prefix of its MCP
  servers (`release-helper-<server>`). Without a manifest the folder name is
  used.
- `version`: semantic versioning (`MAJOR.MINOR.PATCH`).
- Optional component paths add folders on top of the defaults:
  `"commands": ["./extra-commands"]`, `"agents": "./team"`, and
  `"hooks": "./config/hooks.json"` (or an inline hooks object). Skills always
  live in `skills/` and MCP servers in `mcp.json`.

All fields: `references/manifest-reference.md`.

## Components at a glance

**Skills** (`skills/<name>/SKILL.md`): playbooks with a YAML frontmatter
(`name`, `description`). Navin lists skill names in its prompt, matches the
description against the task, and loads the body with the `skill` tool only when
needed. See `skill-development`.

**Agents** (`agents/<name>.md`): subagent definitions. Frontmatter `name`,
`description` (when to delegate, with examples), optional `tools`, `model`,
`color`; the body is the subagent's system prompt. Navin lists them in its
prompt and runs one with `spawn(agent="<name>", task="...")`. See
`agent-development`.

**Commands** (`commands/<name>.md`): prompts the user triggers with
`/<name> args`. Frontmatter `description`, `argument-hint`, `allowed-tools`,
`disable-model-invocation`; the body is an instruction to Navin with
`$ARGUMENTS`, `$1`, `@file` and `` !`shell` `` expansion. See
`command-development`.

**Hooks** (`hooks/hooks.json`): shell commands Navin runs on lifecycle events
(`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`,
`SubagentStop`). They can block a tool call, add context or keep the agent
working. See `hook-development`.

**MCP servers** (`mcp.json`): external tool servers (stdio, SSE or streamable
HTTP). Navin merges them into its tool config as `<plugin>-<server>` when the
plugin is enabled. See `mcp-integration`.

## Portable paths: `${NAVIN_PLUGIN_ROOT}`

A plugin is copied to `~/.navin/plugins/<name>/` on install, so never hard-code
an absolute path. Use `${NAVIN_PLUGIN_ROOT}`, which Navin replaces with the
installed plugin folder:

- in hook commands: `"command": "bash ${NAVIN_PLUGIN_ROOT}/hooks/scripts/check.sh"`
  (also exported as an environment variable to the hook process);
- in command bodies: `` !`python3 ${NAVIN_PLUGIN_ROOT}/scripts/report.py` `` or
  `@${NAVIN_PLUGIN_ROOT}/templates/checklist.md`;
- in `mcp.json`: `"args": ["${NAVIN_PLUGIN_ROOT}/server/index.js"]`.

A hook entry that stays valid wherever the pack is installed:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "write_file|edit_file|apply_patch",
        "hooks": [
          {
            "type": "command",
            "command": "bash ${NAVIN_PLUGIN_ROOT}/hooks/scripts/validate-write.sh",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

A local stdio MCP server shipped inside the pack:

```json
{
  "mcpServers": {
    "tracker": {
      "command": "node",
      "args": ["${NAVIN_PLUGIN_ROOT}/server/index.js"],
      "env": {"TRACKER_TOKEN": "${TRACKER_TOKEN}"}
    }
  }
}
```

A command that runs a bundled script and inlines a bundled template:

```markdown
---
description: Summarize release readiness
argument-hint: [version]
---

Release status for $1:
!`python3 ${NAVIN_PLUGIN_ROOT}/scripts/report.py --version $1`

Apply the checklist in @${NAVIN_PLUGIN_ROOT}/templates/checklist.md and list
every unchecked item.
```

Inside a script, read the variable from the environment instead of computing
the path relative to the current directory:

```bash
#!/usr/bin/env bash
root="${NAVIN_PLUGIN_ROOT:?not run from a Navin plugin}"
python3 "$root/scripts/lib/check.py" "$@"
```

Never hardcode `/home/<user>/...` or `~/.navin/plugins/<name>/...`: the install
folder differs between machines, and a project-level copy has no plugin folder
at all.

Inside skills, refer to bundled files by their path relative to the skill
folder (`references/<file>.md`, `scripts/<file>.py`); Navin resolves them from
the skill location it reports.

## Discovery and reload

- Skills, agents, commands and hooks are re-read from disk while Navin runs:
  an edit applies to the next message, no restart needed.
- MCP servers are merged into the tool config on install, enable and disable
  (`/pack install|enable|disable`) and connect on the next turn.
- Disabled plugins contribute nothing. Removing a plugin removes its MCP
  servers from the config.
- Project-level hooks (`.navin/hooks.json`) run only after the user trusts the
  file: `python -m navin.agent.user_hooks trust <workspace>`. Plugin and
  personal hooks need no extra step: installing the plugin is the consent.

## Auto-discovery rules

Navin finds components by location and extension, never by a registry:

- `skills/`: every direct subfolder holding a `SKILL.md`. A folder without
  `SKILL.md` is ignored, so drafts can sit beside real skills.
- `agents/`: every `.md` file, recursively. A file whose body is empty after
  the frontmatter is skipped; `README.md` files are skipped.
- `commands/`: every `.md` file, recursively; the subfolder becomes a label,
  not part of the command name.
- `hooks/hooks.json`, or the path/object in the manifest `hooks` field.
- `mcp.json` at the root: `{"mcpServers": {...}}` (a bare object of servers is
  also accepted).
- Extra folders listed in the manifest `commands` / `agents` fields are read
  in addition to the defaults, never instead of them.

A manifest that adds folders on top of the defaults:

```json
{
  "name": "team-tools",
  "version": "0.3.0",
  "commands": ["./extra-commands", "./ops/commands"],
  "agents": "./team",
  "hooks": "./config/hooks.json"
}
```

The smallest valid manifest is one field; the folder name is used when the
file is absent altogether:

```json
{"name": "quick-notes"}
```

## Scaffold and validate: exact usage

```bash
# Skeleton with a manifest, README and the chosen component folders
python3 <this skill>/scripts/scaffold_plugin.py ./release-helper \
  --name release-helper \
  --description "Prepare, check and publish releases" \
  --author "Jane Doe" \
  --components skills,agents,commands,hooks,mcp

# Default components when --components is omitted: skills,commands
python3 <this skill>/scripts/scaffold_plugin.py ./quick-notes --name quick-notes

# Check structure, manifest, frontmatter and JSON; exit 1 on any critical issue
python3 <this skill>/scripts/validate_plugin.py ./release-helper

# Same report as JSON, for a script or CI step
python3 <this skill>/scripts/validate_plugin.py ./release-helper --json
```

`validate_plugin.py` takes exactly one path; it prints component counts, then
`[CRITICAL]`, `[WARNING]` and `[OK]` lines, and ends with `Result: PASS` or
`Result: FAIL (<n> critical)`.

## Workflow: build, validate, package, install

1. Scaffold: `python3 <this skill>/scripts/scaffold_plugin.py <dir> --name <name>
   --components skills,agents,commands,hooks,mcp` (only the ones you need).
2. Write the components, loading the matching skill for each kind.
3. Validate: `python3 <this skill>/scripts/validate_plugin.py <dir>`. Fix every
   critical issue; review the warnings. The `plugin-validator` subagent runs
   the same checks and reviews the content.
4. Try it before installing: copy the components into the workspace
   `.navin/` folders (project level) and exercise them in a chat.
5. Install: `/pack install <path-or-git-url>` or the Plugins panel (folder,
   git URL, zip/tgz archive, npm package). Check with `/pack list`.
6. Share: push the folder to a git repository; others install it with
   `/pack install https://...`.

## Naming conventions

| Component | File | Becomes |
|---|---|---|
| Skill | `skills/api-testing/SKILL.md` | skill `api-testing` |
| Agent | `agents/code-reviewer.md` | `spawn(agent="code-reviewer")` |
| Command | `commands/run-tests.md` | `/run-tests` and `/<plugin>:run-tests` |
| Command in a subfolder | `commands/ci/deploy.md` | `/deploy`, labelled `ci` |
| Hook script | `hooks/scripts/validate-write.sh` | called from `hooks.json` |
| MCP server | `mcp.json` key `tracker` | server `<plugin>-tracker`, tools `mcp_<plugin>-tracker_<tool>` |

Prefer verbs for commands (`review-pr`), roles for agents (`test-generator`),
topics for skills (`error-handling`).

## Layouts by size

Minimal (a single command, nothing else):

```
quick-notes/
├── plugin.json
└── commands/
    └── note.md
```

Standard (skills, commands and one guard hook):

```
test-kit/
├── plugin.json
├── README.md
├── skills/
│   ├── test-planning/
│   │   ├── SKILL.md
│   │   └── references/<topic>.md
│   └── flaky-tests/
│       └── SKILL.md
├── commands/
│   ├── plan-tests.md
│   └── fix-flaky.md
└── hooks/
    ├── hooks.json
    └── scripts/
        └── require-tests.sh
```

Advanced (every component kind, shared helpers, an MCP server):

```
release-helper/
├── plugin.json
├── README.md
├── skills/
│   ├── changelog/
│   │   ├── SKILL.md
│   │   ├── references/<topic>.md
│   │   └── scripts/<helper>.py
│   └── versioning/
│       └── SKILL.md
├── agents/
│   ├── release-checker.md
│   └── review/
│       └── notes-reviewer.md
├── commands/
│   ├── prepare.md
│   └── ci/
│       └── publish.md
├── hooks/
│   ├── hooks.json
│   └── scripts/
│       └── validate-write.sh
├── mcp.json
├── scripts/
│   └── report.py
└── templates/
    └── checklist.md
```

Per-project settings for a pack go in a `.navin/<plugin>.local.md` file in the
workspace, not inside the plugin folder; see `plugin-settings`.

## Common patterns

- **Minimal**: one command, or one skill. `examples/minimal-plugin.md`.
- **Standard**: a few skills, commands and one validation hook.
  `examples/standard-plugin.md`.
- **Full**: skills + agents + commands + hooks + MCP + settings file.
  `examples/advanced-plugin.md`.
- **Skill pack**: only `skills/`. A folder with a single `SKILL.md` at its
  root is wrapped into `skills/<folder>/` automatically on install.

How components cooperate (command -> agent -> skill, hooks guarding a command,
MCP behind an agent): `references/component-patterns.md`.

## Troubleshooting

- **"plugin has no components"**: the component folders are not at the root,
  or no `SKILL.md` / `.md` / `hooks.json` / `mcp.json` was found.
- **Command not found**: the file is not in `commands/`, the name clashes with a
  built-in command, or the plugin is disabled (`/pack list`).
- **Agent ignored**: missing `description`, or the body is empty.
- **Hook never runs**: event name misspelled (they are case-sensitive),
  matcher does not match the Navin tool name, script not executable, or the
  project hooks file is not trusted.
- **MCP tools missing**: `mcp.json` must hold an object; check the server
  command runs by hand; run `/pack disable` then `/pack enable` to resync.

## Resources

- `references/manifest-reference.md` - every manifest field, validation rules.
- `references/component-patterns.md` - how components work together.
- `examples/minimal-plugin.md`, `examples/standard-plugin.md`,
  `examples/advanced-plugin.md` - complete trees with file contents.
- `scripts/scaffold_plugin.py` - create a plugin skeleton.
- `scripts/validate_plugin.py` - check a plugin (structure, frontmatter, JSON).
