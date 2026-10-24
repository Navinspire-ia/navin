# How plugin components work together

Each component type answers a different question:

| Question | Component |
|---|---|
| "What does Navin need to know to do X well?" | Skill |
| "Who should do this self-contained job?" | Agent |
| "What does the user type to start it?" | Command |
| "What must always (or never) happen?" | Hook |
| "Which external system holds the data or actions?" | MCP server |
| "How does each project tune the behaviour?" | Settings file (`.navin/<plugin>.local.md`) |

## Pattern 1: command -> agent -> skill

The user starts a workflow with a command; the command delegates the heavy part
to an agent; the agent follows a skill.

```
commands/review.md      "Spawn the code-reviewer agent on the current diff..."
agents/code-reviewer.md "Load the review-checklist skill, then ..."
skills/review-checklist/SKILL.md
```

Why: the command stays short and readable, the agent runs with its own focused
prompt and context window, and the checklist is reusable by other commands.

## Pattern 2: hook guarding a workflow

A `PreToolUse` hook enforces what the skill only recommends.

```
skills/safe-migrations/SKILL.md   explains the rules
hooks/hooks.json                  PreToolUse matcher "exec" -> check-migration.sh
hooks/scripts/check-migration.sh  exit 2 on "DROP TABLE" without a backup flag
```

Why: a skill can be skipped or misread; a hook cannot.

## Pattern 3: settings-driven behaviour

Hooks and commands read `.navin/<plugin>.local.md` to decide what to do in this
project (enabled flag, thresholds, target environment). See `plugin-settings`.

```
commands/configure.md          asks the user, writes .navin/release-helper.local.md
hooks/scripts/gate.sh          exits 0 at once when the file is absent or disabled
```

## Pattern 4: MCP behind an agent

The MCP server exposes raw actions (`mcp_tracker-api_create_issue`); an agent
knows when and how to use them; a command gives the user the entry point.

```
mcp.json                     {"mcpServers": {"api": {...}}}
agents/triage.md             tools: ["mcp_tracker-api_*", "read_file"]
commands/triage.md           "Spawn the triage agent for: $ARGUMENTS"
```

## Pattern 5: context at session start

A `SessionStart` hook prints project facts (stack, conventions, open
incidents); Navin adds that text to the first message of each new chat.

## Choosing between a skill and an agent

- Knowledge that improves many tasks: skill.
- A job with a clear input and a report as output, worth an isolated context:
  agent.
- Both: the agent's prompt tells it to load the skill.

## Choosing between a command and a skill

- The user decides when it starts and passes arguments: command.
- Navin should apply it whenever the task matches: skill.
- A command can load skills ("Load the `release-checklist` skill first").

## Keeping components independent

- A command should still make sense if the agent it mentions is missing:
  give a fallback ("If the release-checker agent is unavailable, run the
  checks yourself").
- Hooks must exit fast and silently when they do not apply.
- Never rely on the order in which two hooks for the same event run: they run
  in parallel.
