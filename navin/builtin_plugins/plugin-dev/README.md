# plugin-dev: the Navin Plugin Development Toolkit

## Overview

plugin-dev bundles seven focused skills that help you build solid Navin plugins:

1. **Hook Development** - lifecycle hooks and event-driven automation
2. **MCP Integration** - wiring Model Context Protocol servers into a plugin
3. **Plugin Structure** - pack layout and the `plugin.json` manifest
4. **Plugin Settings** - per-project configuration in `.navin/<plugin>.local.md` files
5. **Command Development** - Markdown slash commands with frontmatter and arguments
6. **Agent Development** - subagents, with AI-assisted generation
7. **Skill Development** - skills with progressive disclosure and sharp triggers

Every skill is organised the same way (progressive disclosure): a short core
`SKILL.md`, deeper reference documents, working examples and, where useful,
helper scripts.

The pack also ships one guided command (`create-plugin`) and three agents:
`agent-creator`, `plugin-validator` and `skill-reviewer`. Everything is declared
in this folder's `plugin.json`.

## Guided Workflow Command

### /create-plugin

Also reachable with its namespaced form `/plugin-dev:create-plugin`.

**The 8 phases:**
1. **Discovery** - clarify what the plugin is for and who uses it
2. **Component Planning** - pick the skills, commands, agents, hooks and MCP servers it needs
3. **Detailed Design** - spell out each component and settle open questions
4. **Structure Creation** - create the folders and `plugin.json`
5. **Component Implementation** - build each component, using the agents where they help
6. **Validation** - run `plugin-validator` plus the per-component checkers
7. **Testing** - install the pack and exercise it inside Navin
8. **Documentation** - write the README and get the pack ready to share

**What it does for you:**
- Asks targeted questions at every phase
- Loads the matching skill automatically
- Delegates agent writing to `agent-creator`
- Runs the checkers (`validate_plugin.py`, `validate-agent.sh`, `validate-hook-schema.sh`, ...)
- Applies the same patterns plugin-dev itself is built with
- Walks you through testing and verification

**Usage:**
```bash
/create-plugin [optional description]

# Examples:
/create-plugin
/create-plugin A plugin for managing database migrations
```

Pick this workflow when you want a structured path from idea to finished plugin.

## Skills

### 1. Hook Development

**Trigger phrases:** "create a hook", "add a PreToolUse hook", "validate tool use", "${NAVIN_PLUGIN_ROOT}", "block dangerous commands"

**What it covers:**
- `command` hooks: shell scripts that receive the event as JSON on stdin
- The six events Navin dispatches: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop, SubagentStop
- Blocking: exit code 2 (reason on stderr) or a JSON decision on stdout
- Output formats and the `hooks.json` schema
- Security practices and input validation
- `${NAVIN_PLUGIN_ROOT}` and `NAVIN_PROJECT_DIR` for portable paths

**Navin difference:** only `"type": "command"` hooks run. Prompt-based (LLM)
hooks and the SessionEnd, PreCompact and Notification events are not
supported; a hook declared with another type is skipped, and the schema
validator warns about it.

**Resources:**
- Core SKILL.md
- 3 example hook scripts (validate-write, validate-bash, load-context)
- 3 reference docs: patterns, migration, advanced
- 3 helper scripts: validate-hook-schema.sh, test-hook.sh, hook-linter.sh

**Use when:** you need event-driven automation, operation checks or policy enforcement.

### 2. MCP Integration

**Trigger phrases:** "add MCP server", "integrate MCP", "configure mcp.json", "Model Context Protocol", "stdio/SSE/HTTP server", "connect external service"

**What it covers:**
- Declaring servers in the pack's `mcp.json` (or inline in `plugin.json`)
- The three transports: `stdio` (local process), `sse` and `streamableHttp` (remote)
- Variable expansion (`${NAVIN_PLUGIN_ROOT}`, `${VAR}` from the environment)
- Server namespacing (`<plugin>-<server>`) and tool names (`mcp_<server>_<tool>`)
- Authentication: tokens and headers from environment variables
- Integration patterns and performance (`enabledTools`, `toolTimeout`)

**Navin difference:** there is no WebSocket transport.

**Resources:**
- Core SKILL.md
- 3 example configurations (stdio, SSE, HTTP)
- 3 reference docs: server-types, authentication, tool-usage

**Use when:** you plug external services, APIs, databases or tools into a plugin.

### 3. Plugin Structure

**Trigger phrases:** "plugin structure", "plugin.json manifest", "auto-discovery", "component organization", "plugin directory layout"

**What it covers:**
- The pack layout (`~/.navin/plugins/<name>/` with `plugin.json` at the root) and auto-discovery
- Every `plugin.json` field
- Where components go: `skills/<skill>/SKILL.md`, `agents/`, `commands/`, `hooks/hooks.json`, `mcp.json`
- `${NAVIN_PLUGIN_ROOT}` everywhere a path is needed
- Naming conventions and good practice
- Minimal, standard and advanced plugin patterns
- Helper scripts: `scaffold_plugin.py` and `validate_plugin.py`

**Resources:**
- Core SKILL.md
- 3 example layouts (minimal, standard, advanced)
- 2 reference docs: component-patterns, manifest-reference
- 2 helper scripts: scaffold_plugin.py, validate_plugin.py

**Use when:** you start a plugin, reorganise its components or edit its manifest.

### 4. Plugin Settings

**Trigger phrases:** "plugin settings", "store plugin configuration", ".local.md files", "plugin state files", "read YAML frontmatter", "per-project plugin settings"

**What it covers:**
- The `<workspace>/.navin/<plugin>.local.md` configuration pattern
- YAML frontmatter for values, Markdown body for free text
- Parsing from bash (sed, awk, grep recipes)
- Hooks that are only active when a flag is set (quick exit otherwise)
- Worked real-world examples
- Atomic updates and validation
- Gitignore rules and file lifecycle

**Resources:**
- Core SKILL.md
- 3 examples (read-settings hook, create-settings command, sample settings)
- 2 reference docs: parsing-techniques, real-world-examples
- 2 helper scripts: validate-settings.sh, parse-frontmatter.sh

**Use when:** a plugin needs options, per-project state or user preferences.

### 5. Command Development

**Trigger phrases:** "create a slash command", "add a command", "command frontmatter", "define command arguments", "organize commands"

**What it covers:**
- Markdown command files and their structure
- Frontmatter fields (`description`, `argument-hint`, `allowed-tools`, ...)
- Arguments (`$ARGUMENTS`, `$1`..`$9`, `${1:-default}`) and `@path` file inclusion
- Inline shell output with `` !`cmd` `` (only when `allowed-tools` grants exec)
- Organisation, lookup order and the `/<plugin>:<name>` namespace
- Best practices

**Resources:**
- Core SKILL.md
- 7 reference docs and 2 example collections
- Organisation patterns

**Use when:** you write slash commands, define their arguments or organise a plugin's commands.

### 6. Agent Development

**Trigger phrases:** "create an agent", "add an agent", "write a subagent", "agent frontmatter", "when to use description", "agent examples", "autonomous agent"

**What it covers:**
- Agent files: YAML frontmatter plus a system prompt body
- Frontmatter fields (name, description, model, color, tools)
- Descriptions with `<example>` blocks so delegation triggers reliably
- System prompt patterns (analysis, generation, validation, orchestration)
- AI-assisted generation with Navin's agent-generation prompt and the `agent-creator` agent
- Validation rules and good practice
- Complete, ready-to-use agent examples

**Resources:**
- Core SKILL.md
- 2 examples: agent-creation-prompt (AI-assisted workflow), complete-agent-examples
- 3 reference docs: agent-generation-prompt, system-prompt-design, triggering-examples
- 1 helper script: validate-agent.sh

**Use when:** you design subagents, shape their behaviour or generate them with AI help.

### 7. Skill Development

**Trigger phrases:** "create a skill", "add a skill to plugin", "write a new skill", "improve skill description", "organize skill content"

**What it covers:**
- `SKILL.md` with YAML frontmatter
- Progressive disclosure (metadata, then SKILL.md, then bundled resources)
- Strong descriptions built on concrete trigger phrases
- Writing style (imperative form, third-person descriptions)
- Bundled resources (`references/`, `examples/`, `scripts/`)
- The skill creation workflow
- The `validate_skill.py` linter, and the `skill-reviewer` agent for quality reviews

**Resources:**
- Core SKILL.md
- Reference: skill-anatomy
- Helper script: validate_skill.py
- Templates: the plugin-dev skills themselves

**Use when:** you write a new skill or raise the quality of an existing one.

## Installation

plugin-dev ships with Navin as a builtin plugin: nothing to install. Its
skills, agents and the `create-plugin` command are available out of the box.

To install a plugin you are developing, use the `/pack` command:

```bash
/pack install /path/to/my-plugin      # or a git URL
/pack list
/pack enable my-plugin
/pack disable my-plugin
/pack uninstall my-plugin
```

Installed packs live in `~/.navin/plugins/<name>/`.

## Quick Start

### Creating Your First Plugin

1. **Plan the structure:**
   - Ask: "What's the best directory structure for a plugin with commands and MCP integration?"
   - The plugin-structure skill answers

2. **Add MCP integration (if needed):**
   - Ask: "How do I add an MCP server for database access?"
   - The mcp-integration skill supplies examples and patterns

3. **Add hooks (if needed):**
   - Ask: "Create a PreToolUse hook that validates file writes"
   - The hook-development skill provides working scripts and checkers

## Development Workflow

plugin-dev covers the whole life of a plugin:

```
+----------------------+
|  Design Structure    |  -> plugin-structure skill
|  (manifest, layout)  |     scaffold_plugin.py
+----------+-----------+
           |
+----------v-----------+
|  Add Components      |
|  (commands, agents,  |  -> every skill helps
|   skills, hooks)     |
+----------+-----------+
           |
+----------v-----------+
|  Integrate Services  |  -> mcp-integration skill
|  (MCP servers)       |
+----------+-----------+
           |
+----------v-----------+
|  Add Automation      |  -> hook-development skill
|  (hooks, validation) |     + helper scripts
+----------+-----------+
           |
+----------v-----------+
|  Test & Validate     |  -> validate_plugin.py, plugin-validator agent
|                      |     validate-hook-schema.sh
+----------------------+     test-hook.sh, hook-linter.sh
```

## Features

### Progressive Disclosure

Each skill discloses itself in three levels:
1. **Metadata** (always loaded): a short description full of trigger phrases
2. **Core SKILL.md** (when triggered): the essential reference
3. **References/Examples** (on demand): detailed guides, patterns and working code

Navin's context stays small, and the depth is there when the task needs it.

### Utility Scripts

Ready-to-use checkers ship with the skills:

```bash
# Scaffold and validate a whole plugin
python navin/skills/plugin-structure/scripts/scaffold_plugin.py ./my-plugin --name my-plugin --components skills,agents,commands,hooks,mcp
python navin/skills/plugin-structure/scripts/validate_plugin.py ./my-plugin [--json]

# Lint a skill folder
python navin/skills/skill-development/scripts/validate_skill.py ./my-plugin/skills/my-skill [--json]

# Validate an agent file
bash navin/skills/agent-development/scripts/validate-agent.sh ./my-plugin/agents/my-agent.md

# Validate hooks.json structure
bash validate-hook-schema.sh hooks/hooks.json

# Run a hook with a realistic event before shipping it
bash test-hook.sh PreToolUse my-hook.sh '{"command": "rm -rf /"}'

# Lint hook scripts
bash hook-linter.sh my-hook.sh
```

### Working Examples

Each skill comes with working material:
- **Hook Development**: 3 hook scripts (bash guard, write guard, context loader)
- **MCP Integration**: 3 server configurations (stdio, SSE, HTTP)
- **Plugin Structure**: 3 layouts (minimal, standard, advanced)
- **Plugin Settings**: 3 examples (read-settings hook, create-settings command, sample settings)
- **Command Development**: two collections of complete commands (review, test, deploy, docs, plugin workflows, ...)
- **Agent Development**: complete agents plus the AI-assisted generation template

## Documentation Standards

All skills share the same conventions:
- Third-person descriptions ("Use when the user asks to ...")
- Concrete trigger phrases so the skill loads reliably
- Imperative form in the body
- Grounded in what Navin actually implements (verified against its code)
- Security first

## Total Content

- **Core skills**: 7 SKILL.md files
- **Reference docs**: in-depth guides per skill
- **Examples**: hook scripts, MCP configs, plugin layouts, settings files, commands and agents
- **Utilities**: 9 scripts (plugin scaffold/validate, skill lint, agent check, 3 hook tools, 2 settings tools)
- **Agents**: agent-creator, plugin-validator, skill-reviewer
- **Command**: create-plugin

## Use Cases

### Building a Database Plugin

```
1. "What's the structure for a plugin with MCP integration?"
   -> plugin-structure skill gives the layout

2. "How do I configure an stdio MCP server for PostgreSQL?"
   -> mcp-integration skill shows the configuration

3. "Add a Stop hook to ensure connections close properly"
   -> hook-development skill supplies the pattern
```

### Creating a Validation Plugin

```
1. "Create hooks that validate all file writes for security"
   -> hook-development skill with its examples

2. "Test my hooks before deploying"
   -> run validate-hook-schema.sh and test-hook.sh

3. "Organize my hooks and configuration files"
   -> plugin-structure skill shows good practice
```

### Integrating External Services

```
1. "Add Asana MCP server with OAuth"
   -> mcp-integration skill covers remote (sse / streamableHttp) servers

2. "Use Asana tools in my commands"
   -> mcp-integration tool-usage reference

3. "Structure my plugin with commands and MCP"
   -> plugin-structure skill provides the patterns
```

## Best Practices

Every skill insists on:

**Security First**
- Validate input in hooks
- HTTPS for remote MCP servers (plain HTTP only for localhost)
- Credentials from environment variables
- Least privilege (`allowed-tools`, agent `tools`)

**Portability**
- `${NAVIN_PLUGIN_ROOT}` for every path inside the pack
- Relative paths only
- Environment variable substitution

**Testing**
- Validate configurations before shipping
- Feed hooks sample events with `test-hook.sh`
- Run `validate_plugin.py` or ask the `plugin-validator` agent

**Documentation**
- A clear README
- Documented environment variables
- Usage examples

## Precedence

When names collide, project extensions in `<workspace>/.navin/` (commands,
agents) win over personal ones in `~/.navin/`, which win over plugin packs;
Navin's builtins come last. Hooks are the exception: they do not shadow each
other, every matching hook from personal, plugin and (trusted) project
`hooks.json` files runs.

## Contributing

plugin-dev lives in the Navin repository under `navin/builtin_plugins/plugin-dev/`
(manifest) and `navin/skills/`, `navin/builtin_agents/`, `navin/builtin_commands/`
(components). To improve it:

1. Edit the relevant skill, agent or command
2. Run `validate_skill.py` on changed skills and `tests/test_plugin_dev_skills.py`
3. Try the result in a Navin session
4. Open a pull request

## Version

1.0.0 - seven skills, three agents and the guided create-plugin command

## License

Ships with Navin under its license (AGPL-3.0-only).

---

**Note:** the goal is better plugins with less effort. The skills load by
themselves when your question matches them, so the guidance arrives exactly
when you need it.
