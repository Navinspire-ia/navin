---
description: Scaffold a new app on the Navin agent (Python SDK or TypeScript over navin serve), then verify it
argument-hint: "[python|typescript] [project-name]"
---

Create a new application that runs the Navin agent, from interview to a
verified, runnable project.

Request: $ARGUMENTS

Two integration paths exist. Pick the one matching the language:

- **Python**: in-process, with the Navin Python SDK (`from navin import Navin`,
  package `navin-ai`). Full access to sessions, memory, hooks and streaming.
- **TypeScript** (or any other language): over HTTP, with an OpenAI-compatible
  client (the `openai` npm package) pointed at `navin serve`
  (`http://127.0.0.1:8900/v1`, route `/v1/chat/completions`).

## 1. Interview

Ask only what the arguments do not already answer, one `ask_user` question at
a time, each with 2 to 4 options and the recommended one first:

1. Language: Python with the SDK (recommended for scripts, backends, evals) or
   TypeScript over `navin serve` (recommended for Node services and web
   backends).
2. Project name (folder): default from the arguments, else `navin-agent-app`.
   Refuse to overwrite a non-empty folder.
3. Agent type: coding assistant on a workspace, business assistant (support,
   research, reports), or custom (ask one line describing the task).
4. Starting point: minimal example (one turn), streaming example with tool
   events (recommended), or multi-session example (one history per user).
5. Tooling: Python: `uv` (recommended) or `pip` + venv. TypeScript: `npm`
   (recommended), `pnpm` or `bun`.

Summarize the choices in three lines before writing anything.

## 2. Check current versions

Never write a version from memory:

- Python: `pip index versions navin-ai` (or `uv pip index versions`), and the
  local `python --version` against the package requirement.
- TypeScript: `npm view openai version`, `npm view typescript version`,
  `npm view @types/node version`, `npm view tsx version`, and `node --version`.

If the network is unavailable, use the installed versions (`pip show navin-ai`,
`npm ls`) and say so in the summary.

## 3. Scaffold

**Python** (`<name>/`):

- `pyproject.toml` with `navin-ai>=<checked version>` and `requires-python`;
- `src/<pkg>/main.py`: the chosen example, using only real SDK names:
  `Navin.from_config(...)` inside `async with`, `await bot.run(...)` returning
  `RunResult` (`content`, `tools_used`, `usage`, `error`), or
  `async for event in bot.stream(...)` on `StreamEvent.type` values such as
  `text.delta`, `tool.started`, `run.failed`; `session_key=` per user for the
  multi-session variant; `bot.runtime.model` / `bot.runtime.workspace` printed
  at start-up for diagnosis;
- `.env.example` listing variable names only, `.gitignore` (venv, `.env`),
  `README.md` with install, configure, run and an example output.

Python example core (adapt to the agent type):

```python
import asyncio
from navin import Navin

async def main() -> None:
    async with Navin.from_config() as bot:
        result = await bot.run("List the top-level files.", session_key="demo")
        if result.error:
            raise SystemExit(f"agent error: {result.error}")
        print(result.content)

asyncio.run(main())
```

**TypeScript** (`<name>/`):

- `package.json` (`"type": "module"`, scripts `start`, `check`), checked
  versions of `openai`, `typescript`, `@types/node`, `tsx`;
- `tsconfig.json` with `strict: true`, `noEmit` for the check;
- `src/index.ts`: client built from `NAVIN_BASE_URL` (default
  `http://127.0.0.1:8900/v1`) and `NAVIN_API_KEY` (optional on localhost),
  `session_id` sent as an extra body field, streaming with `for await` for the
  streaming variant, clear error when the server is not running;
- `.env.example`, `.gitignore`, `README.md` explaining `navin serve` first.

TypeScript example core:

```ts
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: process.env.NAVIN_BASE_URL ?? "http://127.0.0.1:8900/v1",
  apiKey: process.env.NAVIN_API_KEY ?? "local",
});

const reply = await client.chat.completions.create({
  model: process.env.NAVIN_MODEL ?? "navin",
  messages: [{ role: "user", content: "List the top-level files." }],
  // @ts-expect-error Navin extension: isolates the conversation
  session_id: "demo",
});
console.log(reply.choices[0]?.message?.content);
```

Read the model name from `GET /v1/models` when the server is running and use
it as the default.

## 4. Install and check

- Python: create the environment, install, then `python -m compileall -q src`
  and `mypy src` or `pyright` when available; run the example once if a Navin
  config exists (`navin agent -m "Hello!"` must work first).
- TypeScript: install, `npx tsc --noEmit`; if `navin serve` is reachable
  (`curl -s http://127.0.0.1:8900/v1/models`), run `npm start` once.

Fix type errors before going on. Keep the exact outputs for the verifier.

## 5. Verify

Spawn the matching verifier with the project path and the check outputs:

- Python: `spawn agent=agent-sdk-verifier-py`
- TypeScript: `spawn agent=agent-sdk-verifier-ts`

Apply its critical findings, re-run the check, and ask the verifier again if
anything critical changed.

## 6. Summary

Report the files created, versions used (and how they were checked), the
commands to run the app, the verifier verdict, and next steps (hooks, sessions,
deployment with `navin serve --host` plus an API key when not local).
