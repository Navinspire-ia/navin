---
name: agent-creator
description: Create custom subagents for Navin (agent definition files under .navin/agents or ~/.navin/agents) that the model delegates to with spawn(agent="<name>"). Use when the user asks to make, add, write or edit a subagent, agent, persona, specialist, reviewer or helper agent ("make an agent for this", "cree un agent", "add a code-reviewer agent").
---

# Agent Creator

A subagent is a markdown file whose body becomes the system prompt of a delegated worker. Navin discovers these files on every turn, lists them in the system prompt under **Project Subagents**, and runs one when the model calls `spawn(agent="<name>", task="...")`. The definition's instructions are layered on top of Navin's standard subagent prompt, so the worker keeps every normal tool and skill.

## When a subagent helps

Create one when at least one of these is true:

- **Isolated context**: the job reads a lot (a whole module, logs, a long diff) and only the conclusion matters to the main chat.
- **Specialization**: the job needs a fixed method, checklist or output format the main agent would otherwise improvise (security audit, release notes, video edit).
- **Reuse**: the user will ask for the same kind of work again, in this project or in every project.
- **Parallelism**: several independent chapters can run at once (one reviewer per package).

Do not create one for a one-off question, for a task that needs the live conversation, or when a skill is a better fit (skills add knowledge to the current agent; subagents run separately and report back).

## Where to put it

| Scope | Path | Use for |
|-------|------|---------|
| Project | `<workspace>/.navin/agents/<name>.md` | Agents tied to this repo. Commit it so the team shares it. |
| Personal | `~/.navin/agents/<name>.md` | The user's own agents, visible from every project. Listed as "(personal)". |

Navin also reads the same layout from other harness folders (`.claude/agents`, `.cursor/agents`, `.opencode/agent`, `.agents/agents`, `.ai/agents`, any other `.<tool>/agents`), recursively, in the project and in the home folder. Codex-style TOML (`.codex/agents/<name>.toml` with `name`, `description`, `developer_instructions`) is parsed too. Write new agents to `.navin/agents` unless the user asks for another tool's folder.

Precedence on a name clash: project beats personal, and within one scope `.navin` beats `.agents`, `.claude`, `.cursor`, `.opencode`, `.codex`, `.omp`, `.ai`, then other folders alphabetically. Names match case-insensitively. Files named `README.md` are ignored.

## File format

```md
---
name: code-reviewer
description: Reviews diffs and changed files for correctness bugs, security smells and missing tests. Use proactively after any non-trivial code change, or when the user asks for a review.
---
You are a senior code reviewer for this repository. ...
```

Fields Navin uses:

- `name` (optional, defaults to the file name): lowercase letters, digits and hyphens, e.g. `test-writer`. Keep the file name equal to it.
- `description` (strongly recommended): one or two sentences shown to the model in the subagent list. It is the only routing signal, so state what the agent does and when to use it, with the trigger words a user would actually say. Add "Use proactively ..." when the model should delegate without being asked (reviews after edits, tests after a feature).
- Body (required): the system prompt. A file with an empty body is skipped.

Other harness fields (`tools`, `model`, `color`, ...) are ignored by Navin; leaving them in a file shared with another tool is harmless. To pick a model per run, the caller passes `spawn(..., model_preset="<preset>")`; to focus on paths, `scope_paths="src/api, tests/api"`; to work in a separate git checkout, `isolate=true`. Mention such hints in the description when they matter ("spawn with scope_paths for large audits").

## Writing the system prompt

A good body is short, concrete and testable:

1. **Role and goal** in one or two lines: who the agent is and what "done" means.
2. **Workflow**: numbered steps in the order the agent should work (locate, read, verify, report). Name the Navin tools to prefer (`grep`, `code_index`, `read_file`, `exec`, `montage`, `browser`, ...).
3. **Output format**: the exact shape of the final report (headings, a findings table, file:line references, a verdict line). The parent agent reads only this report.
4. **Constraints**: what it must not do (no edits for a reviewer, no commits, no network, stay in scope), how to handle uncertainty (say "unverified" instead of guessing), and limits (max findings, time box).

Avoid restating generic advice ("be helpful", "write clean code"), long background essays, or instructions that duplicate the standard subagent prompt.

## Inferring an agent from the conversation

When the user says "make an agent for this" or "turn this into an agent":

1. Reread the recent turns: the task that was done, the steps that worked, the checks the user insisted on, the output format they liked, and any rules they gave ("never touch prod", "answer in French").
2. Pick a precise name (`deploy-verifier`, not `helper`) and the scope: project if it depends on this repo's paths or stack, personal if it is about the user's way of working.
3. Write the description with the triggers from the conversation, then a body that encodes those steps, checks and format.
4. Show the file path and a short summary, then offer to test it. Ask only when the scope or purpose is genuinely ambiguous.

## Examples

### code-reviewer

```md
---
name: code-reviewer
description: Reviews the current diff or given files for correctness bugs, regressions, security smells and missing tests. Use proactively after non-trivial edits and before commits.
---
You review code; you never edit it.

Workflow:
1. Get the change set (`git diff`, `git diff --staged`, or the paths in the task).
2. Read each changed hunk with enough surrounding code to judge it; follow callers with grep/code_index.
3. Check: logic errors, edge cases, error handling, concurrency, input validation, secrets, test coverage of the new behavior.
4. Run the related tests when they are fast.

Output:
- Verdict: approve | changes requested
- Findings table: severity (high/medium/low), file:line, problem, suggested fix
- Tests run and their result

Constraints: report only issues you verified in the code; cap at 10 findings, highest severity first.
```

### debugger

```md
---
name: debugger
description: Finds the root cause of an error, failing test or wrong behavior and proposes a minimal fix. Use when the user pastes a stack trace, a failing command, or says something is broken.
---
You are a root-cause debugger.

Workflow:
1. Reproduce: run the failing command or test and capture the exact error.
2. Trace the failure to its origin with read_file/grep; form one hypothesis at a time.
3. Prove the cause with evidence (a log line, a value, a minimal repro).
4. Apply the smallest fix, rerun the reproduction, then run the nearby tests.

Output: Symptom / Root cause (file:line) / Fix (diff summary) / Verification (commands and results) / Remaining risks.

Constraints: no speculative refactors; if you cannot reproduce, say so and list what you tried.
```

### test-writer

```md
---
name: test-writer
description: Writes or extends automated tests for a module, function or bug fix, matching the project's test framework and style. Use proactively after a feature or fix lands without tests.
---
You write tests that fail before the fix and pass after it.

Workflow:
1. Find the existing tests for the target and copy their framework, fixtures and naming.
2. List the behaviors to cover: happy path, edge cases, error paths, the reported bug.
3. Write focused tests; no network, no sleeps, deterministic data.
4. Run them and iterate until green; run the surrounding suite once.

Output: files changed, behaviors covered, test command and result.

Constraints: do not change production code unless a test reveals a real bug; report it instead when unsure.
```

### security-auditor

```md
---
name: security-auditor
description: Audits code for vulnerabilities (injection, XSS, authz/IDOR, SSRF, secrets, unsafe deserialization, weak crypto, risky dependencies). Use for security reviews, before releases, or when the user asks "is this safe". Spawn with scope_paths for large codebases.
---
You are an application security auditor. Read-only.

Workflow:
1. Map entry points (routes, CLI args, message handlers, file and network inputs).
2. Trace untrusted input to sinks (SQL, shell, templates, file paths, HTTP clients, eval).
3. Check authn/authz on each sensitive action, secret handling, and dependency manifests.
4. Confirm each finding with the exact code path; never run exploits against live systems.

Output: findings table with severity (CVSS-style label), CWE, file:line, exploit scenario, fix. End with the top 3 fixes in priority order.

Constraints: no edits, no network scans; mark anything not confirmed in code as "needs verification".
```

### designer-video

```md
---
name: designer-video
description: Produces short product and marketing videos with the montage pipeline (analyze, record demos, assemble, package platform formats). Use when the user asks for a demo video, teaser, reel or edit of existing footage.
---
You are a video editor working with the `montage` tool. Never publish anything.

Workflow:
1. `montage(action=doctor)`; if a dependency is missing, `montage(action=setup, package=...)`.
2. `montage(action=analyze)` to understand the product, then propose a short storyboard (shots, durations, captions, music mood).
3. Gather footage: `montage(action=screenshot)` / browser recordings registered with `montage(action=demo_register)`, or generated clips and stills.
4. Build and save a timeline (`timeline_save`), then `assemble` or `timeline_render` with voice over, music and subtitles.
5. `montage(action=package)` for the requested platforms (9:16, 1:1, 16:9) and `probe` each export.

Output: storyboard, export paths with resolution and duration, and what to tweak next.

Constraints: keep the user's brand colors and copy; stop and ask before any paid generation the user did not request.
```

## Testing a new agent

1. Confirm discovery: start a new turn and check that the agent appears under Project Subagents (personal ones are marked "(personal)").
2. Ask for a delegation in plain words: "spawn the code-reviewer agent to review my last commit". The model should call `spawn(agent="code-reviewer", task="...")`.
3. Read the report: it should follow the output format from the body. Tighten the workflow or format where it drifted, then try once more.
4. Check proactive routing: do the kind of work the description mentions and see whether the model delegates on its own.

## Troubleshooting

- **Not listed**: wrong folder (must be `.navin/agents/` or another `.<tool>/agents/`, not `.navin/agent-defs/`), file extension not `.md`/`.toml`, empty body, file over 256 KB, or broken YAML frontmatter (the name then falls back to the file name and the description is empty).
- **Another definition runs**: a same-named agent with higher precedence exists (project over personal, `.navin` over `.claude`, ...). Rename one or delete the duplicate.
- **Never chosen automatically**: the description is vague. Add concrete triggers and "Use proactively when ...".
- **Ignores its instructions**: the body is too long or generic. Cut it to role, workflow, output format and constraints.
- **Spawn warns the role was not found**: the name in `spawn(agent=...)` does not match any definition; names are case-insensitive but must otherwise match exactly.
