# agent-sdk-dev

Build applications that run the Navin agent, and check them before they ship.

## What it contains

- `/new-sdk-app`: interviews you (language, project name, agent type, starting
  point, tooling), checks the current package versions with `pip` / `npm`,
  scaffolds the project with a working example, installs it, runs the type
  check and finishes with the matching verifier.
- `agent-sdk-verifier-py`: read-only review of a Python app on the Navin SDK
  (`from navin import Navin`): installation, configuration, real SDK usage,
  secrets in the environment, error handling, documentation.
- `agent-sdk-verifier-ts`: the same review for a TypeScript app that calls
  `navin serve` through an OpenAI-compatible client.

Both verifiers end with a verdict: PASS, PASS WITH WARNINGS or FAIL, with
file and line for every finding.

## Two integration paths

| Path | When | Entry point |
|---|---|---|
| Python SDK | scripts, backends, evals, notebooks | `Navin.from_config()`, `run`, `stream` |
| HTTP API | Node, browsers through a backend, other languages | `navin serve`, `/v1/chat/completions` |

## Example

```text
/new-sdk-app python support-bot
```

Scaffolds `support-bot/` with a streaming example like:

```python
async with Navin.from_config() as bot:
    async for event in bot.stream("Summarize open tickets", session_key="ops"):
        if event.type == "text.delta":
            print(event.delta, end="")
```

TypeScript example, after `navin serve` is running:

```text
/new-sdk-app typescript docs-assistant
```

To review an existing project, ask for it directly, for example "run
agent-sdk-verifier-ts on ./web-client".
