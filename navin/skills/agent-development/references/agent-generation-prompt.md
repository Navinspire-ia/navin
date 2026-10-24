# Turning a request into an agent file

Follow this procedure when the user describes an agent they want ("an agent
that reviews SQL migrations"). The `agent-creator` subagent applies it.

## 1. Extract the intent

From the request and the conversation, write down:

- **Job**: what the agent produces (a review, files, a fix, a report).
- **Trigger**: when it should run (explicit request, after some kind of work,
  on some file types).
- **Inputs**: what the task message will give it (paths, a diff, a URL).
- **Constraints**: read-only or not, tools it needs, project conventions
  (look for `AGENTS.md`, `.navin/` rules, existing agents to stay consistent).

If the job is unclear, ask the user one focused question before writing.

## 2. Design the persona

One sentence of identity with real expertise: "You are a database reliability
engineer who reviews schema migrations for safety on large tables."

## 3. Write the system prompt

Use the structure from `system-prompt-design.md`: responsibilities, process
(with tools), quality standards, output format, edge cases. Add the
project's conventions when they matter.

## 4. Choose the identifier

Lowercase, hyphens, 3 to 50 characters, a role: `migration-reviewer`. Check
that no existing agent uses it (look in `.navin/agents/`, `~/.navin/agents/`
and the agents listed in the system prompt).

## 5. Write the description

"Use this agent when ..." plus quoted trigger phrases, then 2 to 4 examples
(see `triggering-examples.md`), including at least one proactive example if
the agent should run after the main agent's own work.

## 6. Pick tools, model, color

- Tools: the minimum. Read-only agents never get write tools.
- Model: `inherit`.
- Color: blue/cyan for analysis, green for generation, yellow for validation,
  red for security, magenta for creative work.

## 7. Write and validate

Save the file where the user wants it (project `.navin/agents/`, personal
`~/.navin/agents/`, or a plugin's `agents/`), run `validate-agent.sh`, and
show the user how to call it.

## Output of this procedure

```markdown
## Agent created: <name>
- File: <path>
- Triggers: <one line>
- Tools: <list or "inherits all">

Use it: ask for "<example phrase>" or call spawn(agent="<name>", task="...").
```
