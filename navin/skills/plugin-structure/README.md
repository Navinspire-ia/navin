# Plugin Structure Skill

Guidance on how a Navin plugin is laid out, how its manifest works, and good
practice for organising it.

## Overview

The skill covers:
- The plugin pack layout (`~/.navin/plugins/<name>/`)
- The `plugin.json` manifest, at the pack root (no hidden manifest folder)
- Where components go (`skills/<skill>/SKILL.md`, `agents/`, `commands/`, `hooks/hooks.json`, `mcp.json`)
- How Navin discovers components automatically
- Portable paths with `${NAVIN_PLUGIN_ROOT}`
- Naming conventions
- Scaffolding and validating a pack with the bundled scripts

## Skill Structure

### SKILL.md

The core document covers:
- The directory layout
- The `plugin.json` fields
- Component organisation patterns
- Using `${NAVIN_PLUGIN_ROOT}`
- Naming conventions
- Auto-discovery
- Good practice
- Common patterns
- Troubleshooting

### References

For deeper questions:

- **references/manifest-reference.md**: every `plugin.json` field
  - Meaning and examples of each field
  - How paths are resolved
  - Validation rules
  - Minimal and complete manifests

- **references/component-patterns.md**: organisation patterns
  - Component lifecycle (discovery, activation)
  - Organising commands
  - Organising agents
  - Organising skills
  - Organising hooks
  - Organising scripts
  - Patterns across components
  - Scaling up cleanly

### Examples

Three complete plugins:

- **examples/minimal-plugin.md**: the smallest useful plugin
  - One command
  - A minimal manifest
  - When this pattern fits

- **examples/standard-plugin.md**: a well-built production plugin
  - Several components (commands, agents, skills, hooks)
  - A full manifest with metadata
  - A rich skill
  - Components working together

- **examples/advanced-plugin.md**: a large, enterprise-style plugin
  - Multi-level organisation
  - MCP servers (namespaced `<plugin>-<server>`)
  - Shared libraries
  - Configuration management
  - Security automation
  - Monitoring

### Scripts

- **scripts/scaffold_plugin.py**: creates a pack skeleton, never overwriting existing files
  ```bash
  python scripts/scaffold_plugin.py <target-dir> --name my-plugin \
      [--description "..."] [--author "Jane"] \
      [--components skills,agents,commands,hooks,mcp]
  ```
  `--components` defaults to `skills,commands`.

- **scripts/validate_plugin.py**: checks the manifest, skills, agents, commands,
  hooks and MCP config of a pack (or of a workspace `.navin` folder)
  ```bash
  python scripts/validate_plugin.py <plugin-dir> [--json]
  ```

## When This Skill Triggers

Navin loads the skill when the user:
- Asks to "create a plugin" or "scaffold a plugin"
- Wants to "understand plugin structure"
- Wants to "organize plugin components"
- Needs to "set up plugin.json"
- Asks how to use `${NAVIN_PLUGIN_ROOT}`
- Wants to "add commands/agents/skills/hooks"
- Needs help with auto-discovery
- Asks about plugin architecture or best practices

## Progressive Disclosure

Context is spent in three steps:

1. **SKILL.md**: core concepts and workflows
2. **References**: field references and patterns
3. **Examples**: complete working plugins

Navin reads references and examples only when the task needs them.

## Related Skills

Works well alongside:
- **hook-development**: writing plugin hooks
- **mcp-integration**: adding MCP servers
- **skill-development**, **agent-development**, **command-development**: the individual components
- **plugin-settings**: per-project configuration

Sharing a finished plugin is done with `/pack install <git-url|/local/path>`
(then `/pack list`, `/pack enable`, `/pack disable`, `/pack uninstall`).

## Maintenance

When updating the skill:
1. Keep SKILL.md lean, on the core concepts
2. Move detail into references/
3. Add examples/ for new common patterns
4. Bump the version in the plugin-dev `plugin.json` when the skill changes significantly
5. Keep the imperative form everywhere
