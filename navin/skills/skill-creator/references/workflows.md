# Workflow patterns for skills

Use these patterns when a skill guides the agent through several steps. Pick the simplest that fits; combine them only when the task really branches.

## Sequential workflow

A fixed order of steps, each with a clear output. Number them and name the artifact each step produces.

```markdown
## Workflow

1. **Inspect**: run `scripts/inspect.py <file>` and read the JSON summary.
2. **Plan**: list the changes in a short table (what, where, why) and show it to the user when the scope is unclear.
3. **Apply**: run the edit script with `--out` to a new file; never overwrite the input.
4. **Verify**: re-run the inspection on the output and compare with step 1.
5. **Deliver**: attach the file and quote what changed.
```

Rules: one verb per step, the verification step is never optional, and the delivery step says what was not changed.

## Conditional workflow

The path depends on a fact discovered at the start. Put the discriminating check first and describe each branch in its own subsection so the agent reads only the branch it needs.

```markdown
## Workflow

Run `scripts/probe.py <input>` first. It prints `kind: fillable|printed|scanned`.

### kind = fillable
...

### kind = printed
...

### kind = scanned
Stop and route to the OCR skill; say so to the user.
```

## Gate and loop

A step that must pass before the skill can deliver (validation, recalculation, build). Describe the gate, the fix loop and the exit condition.

```markdown
### Validate

Run `scripts/validate.py <output>`. Exit code 1 lists the problems: fix them in the source, re-run, and repeat until the report is clean. Never deliver on a failing gate; if the gate cannot run here (missing tool), say so explicitly.
```

## Checklist

For review-type skills (audit, QA, pre-delivery critique) a checklist beats prose. Keep each item verifiable and phrase it as the state to reach, not as a question.

```markdown
## Pre-delivery checklist

- [ ] Every control in the UI is wired to a handler
- [ ] Empty, loading and error states exist for each list
- [ ] No em dash or en dash in the copy
- [ ] Build is green after the last edit
```

## Hand-off between skills

When a skill ends where another begins, name the next skill and the artifact it receives.

```markdown
Once the outline is approved, hand over to `docx-generator` with the outline file and the chosen language.
```

## Anti-patterns

- Steps that describe thinking ("consider the options") instead of an action with an output.
- Branches without a discriminating check at the top.
- A workflow longer than 8 steps: split the skill or move detail to a reference file.
- Repeating in the workflow what a script's `--help` already says; cite the command once.
