# Command Development Skill

Guidance for writing Navin slash commands: the file format, frontmatter
options, dynamic arguments and good practice.

## Overview

The skill teaches:
- How a slash command file is structured
- The YAML frontmatter fields Navin reads
- Dynamic arguments (`$ARGUMENTS`, `$1`, `$2`, ..., `${1:-default}`)
- File inclusion with `@path`
- Inline shell output with `` !`cmd` ``
- Organisation and namespacing
- Good practice for command authors
- Plugin features (`${NAVIN_PLUGIN_ROOT}`, plugin patterns)
- Working with other plugin components (agents, skills, hooks)
- Validation and error handling

## Skill Structure

### SKILL.md

The core document covers:

**Fundamentals:**
- What a command is and where it lives
- The file format (Markdown, optional frontmatter)
- The frontmatter fields
- Dynamic arguments (`$ARGUMENTS` and positional ones)
- File inclusion (`@` syntax)
- Shell output (`` !` `` syntax)
- Organisation patterns
- Good practice and common patterns
- Troubleshooting

**Plugin-specific:**
- The `${NAVIN_PLUGIN_ROOT}` variable
- How plugin commands are discovered and organised
- Plugin patterns (configuration, template, multi-script)
- Working with plugin agents, skills and hooks
- Validation patterns (arguments, files, resources, errors)

### References

In-depth documents:

- **references/frontmatter-reference.md**: every frontmatter field
  - Type, default and meaning of each field
  - When to use it
  - Examples and good practice
  - Validation and common mistakes

- **references/plugin-features-reference.md**: what plugins add to commands
  - Discovery and organisation of plugin commands
  - Using `${NAVIN_PLUGIN_ROOT}`
  - Configuration, template and multi-script patterns
  - Integration with plugin agents, skills and hooks
  - Validation and error handling

- **references/interactive-commands.md**: commands that ask the user questions
- **references/advanced-workflows.md**: multi-step command sequences
- **references/testing-strategies.md**: how to test commands
- **references/documentation-patterns.md**: documenting commands well
- **references/marketplace-considerations.md**: sharing and distributing commands

### Examples

Ready-made commands:

- **examples/simple-commands.md**: 10 complete commands
  - Code review
  - Testing
  - Deployment
  - Documentation generation
  - Git helpers
  - Analysis and research

- **examples/plugin-commands.md**: 10 plugin commands
  - Simple commands backed by scripts
  - Multi-script workflows
  - Template-based generation
  - Configuration-driven deployment
  - Agent and skill integration
  - Multi-component workflows
  - Commands that validate their input
  - Environment-aware commands

## When This Skill Triggers

Navin loads the skill when the user:
- Asks to "create a slash command" or "add a command"
- Wants to "write a custom command"
- Needs to "define command arguments"
- Asks about "command frontmatter" or its YAML
- Wants to "organize commands" or namespace them
- Builds a command that includes files
- Asks about "bash execution in commands"
- Wants command-writing best practices

## Progressive Disclosure

Three levels:

1. **SKILL.md**: core concepts, common patterns, overview of plugin features
2. **References**: the detailed specifications
   - frontmatter-reference.md
   - plugin-features-reference.md
   - interactive-commands.md
   - advanced-workflows.md
   - testing-strategies.md
   - documentation-patterns.md
   - marketplace-considerations.md
3. **Examples**: complete working commands
   - simple-commands.md
   - plugin-commands.md

Navin opens references and examples only when the task calls for them.

## Command Basics Quick Reference

### File Format

```markdown
---
description: Brief description
argument-hint: [arg1] [arg2]
allowed-tools: read_file, exec
---

Command prompt content with:
- Arguments: $1, $2, or $ARGUMENTS
- Files: @path/to/file
- Shell: !`command here`
```

### Locations

First match wins, in this order (built-in router commands such as `/pack`
always win before any file):

- **Project**: `<workspace>/.navin/commands/` (shared with the team)
- **Personal**: `~/.navin/commands/` (your own)
- **Plugin**: `<plugin>/commands/` (also reachable as `/<plugin>:<name>`)
- **Builtin**: `navin/builtin_commands/` (shipped with Navin)

Sub-folders are scanned recursively.

### Key Features

**Dynamic arguments:**
- `$ARGUMENTS` - everything after the command name, as one string
- `$1` .. `$9` - positional arguments
- `${1:-default}` - positional argument with a fallback

**File inclusion:**
- `@path/to/file` - the file's content is inlined

**Shell output:**
- `` !`command` `` - runs the command and inlines its output, only when
  `allowed-tools` grants `exec`

## Frontmatter Fields Quick Reference

| Field | Purpose | Default |
|-------|---------|---------|
| `description` | shown in the command list and the prompt index (keep it under 80 characters) | none (listed as "no description") |
| `argument-hint` | shown next to the name, e.g. `<file> [mode]` | none |
| `allowed-tools` | limits the tools available during the turn; needed for `` !`cmd` `` | all tools |
| `model` | accepted for compatibility, the session model is used | session model |
| `disable-model-invocation` | accepted for compatibility, Markdown commands only run when typed | - |

## Common Patterns

### Simple Review Command

```markdown
---
description: Review code for issues
---

Review this code for quality and potential bugs.
```

### Command with Arguments

```markdown
---
description: Deploy to environment
argument-hint: [environment] [version]
---

Deploy to $1 environment using version $2
```

### Command with File Reference

```markdown
---
description: Document file
argument-hint: [file-path]
---

Generate documentation for @$1
```

### Command with Shell Output

```markdown
---
description: Show Git status
allowed-tools: exec
---

Current status: !`git status`
Recent commits: !`git log --oneline -5`
```

## Development Workflow

1. **Design:**
   - Fix the purpose and scope
   - List the arguments
   - List the tools it needs

2. **Create the file:**
   - Pick the location
   - Name the `.md` file after the command
   - Write a first prompt

3. **Add frontmatter:**
   - Start with `description` only
   - Add fields when needed (`allowed-tools`, ...)
   - Document arguments in `argument-hint`

4. **Test:**
   - Run `/command-name`
   - Check the arguments expand
   - Check shell output
   - Check file inclusion

5. **Polish:**
   - Make the prompt clearer
   - Cover edge cases
   - Add examples as comments
   - Document requirements

## Best Practices Summary

1. **One job**: each command has a single clear purpose
2. **Clear description**: it is what users see in the command list
3. **Document arguments**: always set `argument-hint`
4. **Least tools**: the tightest `allowed-tools` that works
5. **Test everything**: every feature the command uses
6. **Comment**: explain the tricky parts
7. **Handle errors**: missing arguments, missing files

## Status

**Done:**
- Plugin command patterns (`${NAVIN_PLUGIN_ROOT}`, discovery, organisation)
- Integration patterns (agents, skills, hooks)
- Validation patterns (input, files, resources, error handling)
- Advanced workflows (multi-step sequences)
- Testing strategies
- Documentation patterns
- Distribution considerations (packs shared with `/pack install`)

## Maintenance

When updating the skill:
1. Keep SKILL.md on the fundamentals
2. Push detailed specifications into references/
3. Add examples/ for new use cases
4. Update the field table when Navin reads a new field (check `navin/agent/custom_commands.py`)
5. Keep the imperative form throughout
6. Re-test the examples against the current Navin

## Version History

**v1.0.0**:
- Command fundamentals and frontmatter reference
- Simple and plugin command examples
- Plugin-specific patterns and the full reference set
