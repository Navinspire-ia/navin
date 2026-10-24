# learning-output-style

Learn by doing. The agent writes the boilerplate, setup and obvious code itself, and stops at the meaningful decision points (business rules, error handling, algorithm or data structure choices, design trade-offs) to ask you to write those 5 to 10 lines. It prepares the file and the function signature, marks the spot with a `TODO(human)` comment, explains what to write and why, then waits for you. It also adds the Insight blocks of the explanatory style.

The instructions are part of the system prompt on every turn of the main agent, so they survive compaction. Subagents do not get them.

## Commands

- `/output-style learning` - select it for this project.
- `/output-style learning --personal` - select it for every project.
- `/output-style` - list the styles, the active one marked with `*`.
- `/output-style off` - back to the default style.

## Example TODO(human) handoff

The agent prepares the code:

```python
def next_delay(attempt: int) -> float:
    """Seconds to wait before retry number *attempt* (1-based)."""
    # TODO(human): return the delay for this attempt
    raise NotImplementedError
```

Then hands over in the chat:

```
Context: the retry wrapper is in place. The delay policy is a reliability versus latency trade-off.
Your turn: in net/retry.py, fill in next_delay(attempt) where the TODO(human) comment is.
Think about: a fixed delay is predictable; exponential backoff with jitter spares a struggling server but can make users wait.
```

## Example insight block

```
> Insight
> - The wrapper takes the delay function as a parameter, so tests can pass a zero delay.
```

## Custom style

Write your own variant in `.navin/output-styles/<name>.md` (or `~/.navin/output-styles/<name>.md`) with `name` and `description` frontmatter and the instructions as the body, then run `/output-style <name>`. Project styles win over personal ones, which win over the builtin styles.
