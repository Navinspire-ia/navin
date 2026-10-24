# ralph-loop

A native Navin loop that repeats one task until it is truly done. Each time the agent ends its turn, Navin's Stop step hands the same prompt back with the iteration number. The agent sees its earlier work in the files and git history and improves on it.

## Commands

- `/ralph-loop "PROMPT" [--max-iterations N] [--completion-promise "TEXT"]` starts the loop. `0` iterations means unlimited (hard ceiling: 200).
- `/cancel-ralph` stops it and reports the iteration reached.
- `/ralph-help` explains the technique, when to use it and how to write the prompt.

The loop ends when the final answer contains exactly `<promise>TEXT</promise>` or when the cap is reached. Each round shows a notice row (`Ralph loop: iteration 3/20`). A red end-of-turn check or a security finding is handled first; the loop waits for the next round.

## Example

```
/ralph-loop "Make every test in tests/ pass. Each round run pytest and fix the first failure." --max-iterations 20 --completion-promise "ALL GREEN"
```

State file (one per project):

```
---
active: true
iteration: 3
max_iterations: 20
completion_promise: "ALL GREEN"
started_at: "2025-01-01T10:00:00Z"
session: "cli:default"
---

Make every test in tests/ pass. ...
```

From a shell: `python -m navin.agent.ralph_loop status <workspace>`.

## Safety

Always set `--max-iterations`. The stop button ends the current turn only; run `/cancel-ralph` to disarm the loop.
