---
description: Create a Navin plugin end to end with a guided 8-phase workflow
argument-hint: [plugin description]
---

Guide the creation of a complete Navin plugin, from the idea to a validated,
documented package.

Initial request: $ARGUMENTS

Work through the eight phases in order. Keep the user informed in one line at
each phase change. Track the phases on the board when the plan has more than
three components.

## Phase 1 - Discovery

Goal: understand what the plugin is for.

1. If the request above is empty, ask what the plugin should do.
2. Restate the purpose, the target users and the problem in three lines.
3. Ask with ask_user only the questions that change the design (who uses it,
   project or shared, integrations needed). Skip what the request answers.

## Phase 2 - Component planning

Load `skill action=read name=plugin-structure`.

Decide which components the plugin needs and present a table:

| Component | Count | Purpose |
|---|---|---|
| Skills | | knowledge and procedures Navin loads on demand |
| Commands | | user-typed workflows (`/name`) |
| Agents | | autonomous roles called with spawn |
| Hooks | | rules enforced on every event |
| MCP servers | | external tools and services |
| Settings | | per-project options in `.navin/<plugin>.local.md` |

Ask for confirmation of the table with ask_user (recommended: the table as
proposed).

## Phase 3 - Detailed design

For each component, settle the open details before writing anything:

- skills: trigger phrases, references to bundle;
- commands: arguments, allowed tools;
- agents: triggers, tools, output format;
- hooks: events, matchers, block or warn;
- MCP: transport, authentication variables;
- settings: keys and defaults.

Ask only about real forks. Record the decisions in a short list.

## Phase 4 - Structure

1. Choose the location: a working folder in the workspace (recommended while
   building) or `~/.navin/plugins/<name>/` to use it at once.
2. Scaffold with the `scripts/scaffold_plugin.py` of the plugin-structure
   skill, passing only the components from phase 2.
3. Fill `plugin.json`: name, version 0.1.0, description, author.
4. Create `README.md` with a section per component.

## Phase 5 - Implementation

Load the matching skill before each kind and follow it:

- skills: `skill-development` (write SKILL.md, references, examples, scripts);
- commands: `command-development`;
- agents: `agent-development` (or spawn the `agent-creator` agent per agent);
- hooks: `hook-development` (hooks/hooks.json plus scripts, executable);
- MCP: `mcp-integration` (mcp.json with `${NAVIN_PLUGIN_ROOT}` and `${VAR}`);
- settings: `plugin-settings` (template, parser, settings command).

Use `${NAVIN_PLUGIN_ROOT}` for every path inside the plugin. Independent
components can be written by parallel subagents.

## Phase 6 - Validation

1. Spawn the `plugin-validator` agent on the plugin folder.
2. Spawn the `skill-reviewer` agent on each skill.
3. Fix every critical finding, then rerun the validator until it passes.

## Phase 7 - Testing

1. Install it for real: copy or link it under `~/.navin/plugins/<name>/`
   (or `/pack install <path>`), then `/pack enable <name>`.
2. Check each component loads: skills listed, `/commands` respond, agents
   appear in the subagent list, hooks fire (use `scripts/test-hook.sh` of
   hook-development), MCP tools appear.
3. Run each command once with and without arguments.

## Phase 8 - Documentation and summary

1. Complete the README: purpose, installation, each component with an
   example, required environment variables, settings keys.
2. Report to the user: the plugin path, the component table with status, the
   validator verdict, what was tested, and what the user must do (for example
   variables to export).

## Rules for every phase

- Track progress on the board and give one line per phase change.
- Load the skill of a component kind before writing it (list below).
- Delegate to the dedicated agents: `agent-creator` to write agents,
  `plugin-validator` for structure, `skill-reviewer` for skills.
- Apply the house rules: skill descriptions in the third person with quoted
  trigger phrases, imperative skill bodies, command bodies written as
  instructions to Navin (not to the user), `${NAVIN_PLUGIN_ROOT}` for every
  path, details moved to `references/`, HTTPS endpoints and credentials read
  from environment variables, never written in a file. No em or en dash.

**Stop and wait for the user** at these points:

1. after phase 1, to confirm the purpose;
2. after phase 2, to approve the component table;
3. after phase 3, before writing any file;
4. after phase 6, when findings remain, to choose fix or ship;
5. after phase 7, before the documentation pass.

**Skills by phase:** phase 2 `plugin-structure`; phase 5 `skill-development`,
`command-development`, `agent-development`, `hook-development`,
`mcp-integration`, `plugin-settings` as needed; phase 6 none (the agents load
their own).

**Done means** every component has a correct name, a strong trigger (skills
and agents), a working example in the README, a passing validator run and a
real load test in Navin.

## Example run

Request: `/create-plugin manage database migrations`

- Phase 1: the purpose is schema versioning; the user confirms they want to
  create, apply and roll back migrations.
- Phase 2 table: 1 skill (migration practices), 3 commands
  (`/create-migration`, `/run-migrations`, `/rollback`), 1 agent
  (`migration-validator`), 1 MCP server (database connection).
- Phase 3 questions: which engines (PostgreSQL, MySQL)? SQL files or
  code-based migrations? must the agent validate before applying? which MCP
  tools (query, execute, schema)?
- Phases 4 to 8: scaffold, write each component with its skill, validate,
  install under `~/.navin/plugins/db-migrations/`, test, document.

Start with phase 1.
