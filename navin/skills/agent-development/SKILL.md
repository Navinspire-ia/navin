---
name: agent-development
description: Design and write Navin subagents (agents/<name>.md with name, description and examples, tools, model, color, and a system prompt body), and test that Navin delegates to them. Use when the user asks to "create an agent", "add an agent to my plugin", "write a subagent", "agent frontmatter", "when-to-use description", "agent examples", "restrict agent tools", "autonomous agent", or needs agent prompt and triggering best practices.
---

# Agent Development

A subagent is a specialist Navin starts with `spawn(agent="<name>", task="...")`.
It runs in its own context window with its own system prompt, works
autonomously, and returns a report. Use one when a job is self-contained,
benefits from a focused prompt, or would flood the main conversation with
intermediate output.

The `agent-creator` subagent writes an agent from a plain description, using
this skill. `scripts/validate-agent.sh` checks a file.

## File format

```markdown
---
name: test-generator
description: Use this agent when code was written or changed without tests, or when the user asks to "add tests", "cover this module", "write unit tests". Examples:

<example>
Context: The main agent just implemented a parser.
user: "Great, now make sure it is tested."
assistant: "I'll use the test-generator agent to write tests for the parser."
<commentary>New code without tests and an explicit request: delegate.</commentary>
</example>

<example>
user: "Coverage on billing/ is terrible."
assistant: "I'll run the test-generator agent on billing/."
</example>
tools: ["read_file", "grep", "find_files", "write_file", "exec"]
model: inherit
color: green
---

You are a test engineer who writes focused, deterministic tests.

**Responsibilities:**
1. ...

**Process:**
1. ...

**Output format:**
...
```

The frontmatter configures the agent; everything after the closing `---` is its
system prompt, layered on top of Navin's standard subagent prompt (tool rules,
workspace, verification discipline).

## Frontmatter fields

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Identifier used in `spawn(agent=...)`. 3 to 50 characters, lowercase letters, digits, hyphens, starting and ending with a letter or digit. Defaults to the file name. |
| `description` | yes | When Navin should delegate, with 2 to 4 `<example>` blocks. Shown in the prompt (examples stripped) and decisive for routing. |
| `tools` | no | Allowlist of tool names. Omit to inherit everything the parent turn allows. Restriction only narrows: it never grants a tool the parent cannot use. |
| `model` | no | `inherit` (default). The subagent runs on the session's model routing. |
| `color` | no | Display hint for the UI: `blue`, `cyan`, `green`, `yellow`, `magenta`, `red`. |

### Fields Navin does not read

Navin honours `name`, `description`, `tools`, `disallowedTools`, `model`,
`permissionMode` and `color`. `disallowedTools` removes tools even when `tools`
is absent. `model` names a configured model preset; `inherit`, an empty value
or a name with no matching preset keeps the parent's model. `permissionMode`
takes `default` (approval questions go to the user of the chat that started
the agent), `plan` (read-only tools only), `acceptEdits` (file edits approved
without asking) or `bypassPermissions` (nothing asked, unless an organization
policy forbids it); an unknown value keeps `default`. Other keys some agent
formats use (`maxTurns`, `skills`, `hooks`) are ignored without error;
enforce such limits through `tools` and the system prompt instead. A file whose
body is empty is skipped entirely. Agent files in TOML (`name`, `description`,
`developer_instructions`) are also read, without `tools`, `model` or `color`.

To run one delegation on another model, pass a configured preset at spawn
time rather than in the file: `spawn(agent="<name>", task="...",
model_preset="<preset>")`.

### Naming

Good: `code-reviewer`, `api-docs-writer`, `migration-planner`.
Rejected: `ag` (too short), `-helper` (leading hyphen), `My_Agent`
(uppercase, underscore). Name the role, not the topic: `schema-reviewer`
rather than `database`.

### Description

The description is the routing rule. Write:

1. One sentence on the trigger: "Use this agent when ...".
2. Quoted phrases users type.
3. 2 to 4 examples covering explicit requests and proactive use (the main
   agent decides to delegate after its own work). Each example: optional
   `Context:`, a `user:` line, an `assistant:` line that names the agent, and
   an optional `<commentary>` explaining why.

Detailed patterns and anti-patterns: `references/triggering-examples.md`.

The four example formats worth covering:

```markdown
<example>
Context: Explicit request.
user: "Review the auth module for security issues."
assistant: "I'll use the security-reviewer agent on auth/."
<commentary>The user names the job the agent exists for.</commentary>
</example>

<example>
Context: Proactive use after the main agent wrote code.
user: "Add a password reset endpoint."
assistant: "The endpoint is in place. I'll run the security-reviewer agent on it before we continue."
<commentary>New code touching credentials: review without being asked.</commentary>
</example>

<example>
Context: Implicit need.
user: "Is it safe to ship this login change?"
assistant: "I'll use the security-reviewer agent to check it."
<commentary>No keyword match, but the intent is a security review.</commentary>
</example>

<example>
Context: Several steps in one request.
user: "Fix the token bug, then make sure nothing else is exposed."
assistant: "Token bug fixed. Now I'll use the security-reviewer agent for the rest."
<commentary>Delegate only the review part, after the fix.</commentary>
</example>
```

Each `assistant:` line names the agent; that is what teaches the routing.
Avoid examples that only differ by wording, and never show the agent being
used for a task it should refuse.

### Tools

Navin tool names: `read_file`, `write_file`, `edit_file`, `apply_patch`,
`exec`, `grep`, `find_files`, `list_dir`, `code_index`, `lsp`, `web_search`,
`web_fetch`, `browser`, `skill`, `board`, `verify`, `test_run`,
`mcp_<server>_<tool>`. Common aliases are accepted and mapped: `Read`,
`Write`, `Edit`, `MultiEdit`, `Bash`, `Grep`, `Glob`, `LS`, `WebFetch`,
`WebSearch`, `Skill`, `TodoWrite`.

Grant the least that does the job:

| Agent kind | Tools |
|---|---|
| Reviewer, auditor, analyst | `read_file`, `grep`, `find_files`, `code_index` |
| Writer of new files | add `write_file` |
| Fixer | add `edit_file`, `apply_patch`, `verify` |
| Runner of builds or tests | add `exec`, `test_run` |
| Researcher | `web_search`, `web_fetch` |

A subagent cannot ask the user questions: it runs in the background. Tell it to
report what it needs instead.

## Writing the system prompt

Write in second person, as instructions to the agent. A solid structure:

```markdown
You are <role> specializing in <domain>.

**Responsibilities:**
1. <primary job>
2. <secondary job>

**Process:**
1. <first concrete step, with the tool to use>
2. ...

**Quality standards:**
- <measurable standard>

**Output format:**
<exact structure of the report>

**Edge cases:**
- <situation>: <what to do>
```

Rules:

- Be specific: "Run `grep -n 'TODO'` on the changed files" beats "look for
  problems".
- Define the output: the main agent has to use it. Tables, headings, file:line
  references.
- Say when to stop and what to do when blocked.
- Require evidence: every claim tied to a file, line, command output.
- 500 to 3,000 words. Shorter lacks guidance; longer dilutes it.
- Plain hyphens, no long dashes.

Full guide with templates per agent kind: `references/system-prompt-design.md`.

### A filled-in system prompt

```markdown
You are a security reviewer specializing in web authentication code.

**Responsibilities:**
1. Find vulnerabilities in the files named in the task.
2. Rank them by real exploitability, not by theoretical severity.

**Process:**
1. List the target files with `find_files`; read each with `read_file`.
2. `grep` for secrets, raw SQL, `eval`, disabled TLS checks, missing auth
   decorators.
3. For each hit, read the surrounding function and confirm it is reachable.
4. Stop after the named scope; do not audit the whole repository.

**Quality standards:**
- Every finding cites `path:line` and the exact excerpt.
- No finding without a concrete attack scenario.

**Output format:**
| Severity | File:line | Issue | Fix |
followed by a one-line verdict: SAFE TO SHIP or BLOCK.

**Edge cases:**
- Nothing found: say so and list what was checked.
- Generated or vendored code: skip it and mention it.
```

## Creating an agent

**Assisted**: spawn the `agent-creator` agent with the user's description, or
follow `references/agent-generation-prompt.md` yourself: it turns a request
into a name, a description with examples and a complete system prompt.

**Manual**:

1. Choose the name and the trigger.
2. Write the description with examples.
3. Pick the minimal tools.
4. Write the system prompt with the structure above.
5. Save to `agents/<name>.md` (plugin), `<workspace>/.navin/agents/<name>.md`
   (project) or `~/.navin/agents/<name>.md` (personal).
6. Validate: `bash <this skill>/scripts/validate-agent.sh <file>`.

```bash
bash <this skill>/scripts/validate-agent.sh .navin/agents/security-reviewer.md
# [WARNING] description has no <example> blocks (add 2-4)
# Agent 'security-reviewer': PASS
```

The script takes one file (exit 2 without it) and exits 1 on a critical
issue: missing frontmatter, invalid `name`, empty `description`, `tools` not
a list or comma string, non-string `model`, empty body. It warns on missing
or excessive examples, a description that does not start with the trigger, an
unknown color, a prompt too short or too long, no second person, no output
format, long dashes.

## Where agents are found

In order, first name wins: the workspace `.navin/agents/` and other
`.<tool>/agents/` folders, the user's `~/.navin/agents/` (and other home tool
folders), enabled plugins' `agents/`, then Navin's built-in agents. Subfolders
are scanned recursively, so `agents/review/security.md` works. Files are
re-read on change; no restart needed.

Concretely, for an agent named `security-reviewer`:

```
<workspace>/.navin/agents/security-reviewer.md     # project, highest priority
<workspace>/.agents/agents/security-reviewer.md    # other dot folders, scanned too
~/.navin/agents/security-reviewer.md               # personal
~/.navin/plugins/<plugin>/agents/security-reviewer.md  # enabled plugin
navin/builtin_agents/<name>.md                     # shipped with Navin
```

Built-in agents include `agent-creator`, `code-architect`, `code-explorer`,
`code-reviewer`, `plugin-validator` and `skill-reviewer`. Both `agents/` and
`agent/` subfolders are read, and `README.md` files are ignored.

## How Navin runs an agent

The main agent sees each agent's name and description (examples stripped) and
delegates through the `spawn` tool:

```python
spawn(agent="security-reviewer", task="Review auth/ for the reset flow added today")
spawn(agent="security-reviewer", task="...", scope_paths="navin/auth, tests/auth")
spawn(agent="security-reviewer", task="...", isolate=True)  # own git checkout from HEAD
```

Several spawns can run in parallel; each returns its final report to the
main agent. The `task` string is the only context the subagent receives, so
put the paths, the goal and the expected output in it.

## Testing

1. **Routing**: in a chat, phrase the task like each example and check that
   Navin spawns the agent. Phrase a nearby task and check it does not.
2. **Behaviour**: spawn it directly, `spawn(agent="<name>", task="...")`, on a
   real case. Check it follows the process and returns the declared format.
3. **Tools**: if `tools` is set, confirm the job is doable with them; a
   reviewer that must run tests needs `exec`.

## Quick reference

Minimal agent:

```markdown
---
name: changelog-writer
description: Use this agent when the user asks to "write the changelog" or "summarize changes since the last tag". Examples:

<example>
user: "Draft release notes for 1.4"
assistant: "I'll use the changelog-writer agent."
</example>
---

You write changelogs from git history. Run `git log <last-tag>..HEAD --oneline`,
group commits into Added / Changed / Fixed, and return Markdown only.
```

## Resources

- `references/system-prompt-design.md` - prompt structure and templates for
  analysis, generation, validation and orchestration agents.
- `references/triggering-examples.md` - writing `<example>` blocks that route
  correctly.
- `references/agent-generation-prompt.md` - the procedure to turn a request
  into a complete agent file.
- `examples/complete-agent-examples.md` - four complete agents.
- `examples/agent-creation-prompt.md` - a ready-to-use request that asks
  Navin to generate a new agent file.
- `scripts/validate-agent.sh` - check an agent file.
