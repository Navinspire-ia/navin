# Writing examples that route correctly

Navin decides whether to delegate by reading agent descriptions. The examples
show it the situations that should trigger the agent. Their content is stripped
from the compact list in the system prompt, but the trigger sentence before
them is kept, so put the essential rule first.

## Format

```
<example>
Context: <optional: what just happened>
user: "<what the user said>"
assistant: "<reply that names the agent and the job>"
<commentary>
<why this agent fits here>
</commentary>
</example>
```

## Four kinds of examples to cover

1. **Explicit request**: the user names the job.

```
<example>
user: "Review my changes before I open the PR."
assistant: "I'll use the code-reviewer agent on your diff."
</example>
```

2. **Proactive use**: the main agent finished work that this agent should
   check.

```
<example>
Context: The assistant just wrote a new auth middleware.
user: "Thanks, looks good."
assistant: "Before we move on, I'll run the security-reviewer agent on the new middleware."
<commentary>New security-sensitive code: review proactively.</commentary>
</example>
```

3. **Indirect phrasing**: same need, different words.

```
<example>
user: "Is there anything risky in this PR?"
assistant: "I'll use the code-reviewer agent to check for risks."
</example>
```

4. **Scale**: a job too big for the main thread.

```
<example>
user: "Audit all 40 services for outdated dependencies."
assistant: "I'll spawn the dependency-auditor agent for this."
</example>
```

## Anti-patterns

- **Too generic**: `user: "Help me"`. Routes everything or nothing.
- **Identical examples**: four phrasings of the same request teach nothing new.
- **Assistant line without the agent name**: the link between need and agent
  is lost.
- **Missing negative boundary**: when two agents are close (code-reviewer vs
  security-reviewer), add a sentence before the examples: "For security-only
  reviews use security-reviewer instead."

## How many

Two to four. One is not enough to generalize; more than four costs tokens
without improving routing.

## Testing routing

For each example, send its `user:` line in a fresh chat and check that Navin
spawns the agent. Then send two near-misses and check it does not.
