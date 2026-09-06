# Settings

**Ctrl+G** in `navin-cli`, or Settings in the desktop app (`navin .`). Prefer that form for secrets. You can also edit `~/.navin/config.json`.

| Piece | Meaning |
|---|---|
| Provider | Key, `apiBase`, OAuth |
| Model | Provider + model id + label |
| Active model | What chat uses |

```bash
navin status
navin doctor
```

There is no `navin config set api-key` command. Use the Settings form, an environment variable (`OPENROUTER_API_KEY`, …), or `navin provider login` for OAuth (`openai_codex`, `github_copilot`, `xai_oauth`).

Local example: Ollama `apiBase` `http://127.0.0.1:11434/v1`.

Switch model in chat: `/model <preset>`.
