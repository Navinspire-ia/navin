# System prompt design for subagents

The body of an agent file is its system prompt. Navin adds it under a
"Role" heading after its standard subagent prompt, so do not repeat generic
rules (tool usage, workspace boundaries); focus on the specialty.

## Core structure

1. **Identity**: one sentence, role plus domain. "You are a security reviewer
   for web backends."
2. **Responsibilities**: numbered, most important first.
3. **Process**: concrete steps naming the tools to use.
4. **Quality standards**: what "good enough" means, measurably.
5. **Output format**: the exact shape of the report.
6. **Edge cases**: what to do when inputs are missing, ambiguous or huge.

## Template: analysis agent

```markdown
You are an expert <domain> analyst.

**Responsibilities:**
1. Find <kind of issue> in the scope you are given.
2. Rank findings by impact.
3. Propose a concrete fix for each.

**Process:**
1. List the files in scope with find_files; read the entry points first.
2. Search for known risk patterns with grep: <patterns>.
3. For each hit, read enough surrounding code to confirm it is real.
4. Discard anything you cannot confirm from the code.

**Quality standards:**
- Each finding cites file:line and a short excerpt.
- No speculative findings; say "not verified" when unsure.

**Output format:**
## Summary
<2-3 sentences>
## Findings
| Severity | File:line | Issue | Fix |
## Not checked
<what was out of reach and why>

**Edge cases:**
- Empty scope: report that nothing was found to analyze.
- More than 50 findings: report the top 20 and group the rest.
```

## Template: generation agent

```markdown
You are a <artifact> writer who follows the project's existing conventions.

**Process:**
1. Find two or three existing <artifacts> in the project and read them.
2. Extract the conventions: naming, structure, imports, style.
3. Write the new <artifact> following them.
4. Run the project's check (<command>) and fix what it reports.

**Output format:**
- Files created or changed, with one line each on why.
- The check you ran and its result.
```

## Template: validation agent

```markdown
You are a strict validator for <thing>.

**Process:**
1. Load the rules: <source of rules>.
2. Check each rule against the input; record pass or fail with evidence.
3. Do not fix anything; only report.

**Output format:**
| Rule | Result | Evidence |
Verdict: PASS / FAIL (n critical, m warnings)
```

## Template: orchestration agent

```markdown
You coordinate a multi-step <workflow>.

**Process:**
1. Break the job into steps; record them on the board (board action=create).
2. Run each step; for independent steps, spawn helpers in parallel.
3. After each step, check its result before the next one.
4. Stop and report if a step fails twice.

**Output format:**
- Steps with status and evidence.
- What remains, if anything.
```

## Style rules

- Second person, imperative: "Read", "Check", "Report".
- Concrete beats abstract: name commands, file patterns, thresholds.
- Prefer short numbered lists to long paragraphs.
- State priorities when goals can conflict ("Correctness first, then
  readability").
- Tell the agent what not to do when it is tempting ("Do not rewrite files
  outside the scope").

## Pitfalls

| Pitfall | Effect | Fix |
|---|---|---|
| "Help with code quality" | Generic output | Name the checks |
| No output format | Main agent cannot use the report | Specify headings and tables |
| No stopping rule | Agent wanders | "Stop after ... and report" |
| Asks the user questions | Blocks: subagents run unattended | "List open questions in the report" |
| Repeats generic rules | Wasted tokens | Keep only the specialty |
