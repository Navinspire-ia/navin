---
description: Explain the Ralph loop technique and the /ralph-loop and /cancel-ralph commands
---

# Ralph loop help

Explain the following to the user, briefly and in their language.

## The idea

A Ralph loop repeats one fixed task. Each time the agent ends its turn, Navin hands it the same prompt again, with the iteration number. The prompt never changes; what changes is the project: the files, the git history, the test results. Every round the agent sees its own earlier work and improves on it, until the job is done.

## Commands

- `/ralph-loop "PROMPT" --max-iterations N --completion-promise "TEXT"` starts a loop. `--max-iterations 0` means unlimited (Navin still stops at 200 rounds). The loop ends when the final answer holds exactly `<promise>TEXT</promise>`, or when N iterations are done.
- `/cancel-ralph` stops the loop and says at which iteration it was.
- `/ralph-help` shows this help.

The state is kept in `.navin/ralph-loop.local.md` of the project; while it is active the hooks summary shows `ralph loop active: iteration N/M`.

## When it fits

- Tasks with a clear, checkable finish: make the tests pass, migrate every file, fix all lint errors.
- Work that improves by repetition: refactors, coverage, polishing.
- Not for tasks that need human choices along the way, or with no way to tell they are done.

## Writing a good prompt

- State the finish line in testable words ("all tests in tests/api pass").
- Split big work into stages and say how to see which stage is next.
- Ask the agent to run the checks each round and to fix what fails.
- Say what to do when stuck: write down what blocks, try another approach.
- Example:

```
/ralph-loop "Build the todo REST API in api/. Each round: run pytest, fix the first failure, add a test for each new endpoint. Done when every test passes and the README lists the endpoints." --max-iterations 25 --completion-promise "API DONE"
```

## Safety

- Always set `--max-iterations`: a promise may never become true.
- The promise must be honest: the agent must not write it to escape the loop.
- The stop button ends the current turn, but the loop stays armed for the next one: use `/cancel-ralph` to really stop it.
