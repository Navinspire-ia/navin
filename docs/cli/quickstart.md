# Quickstart

## 1. Check the install

```bash
navin --version
navin doctor
```

If `navin` is not found, run `navin install-cli` or use the [install](./install.md) one-liner.

## 2. Open a project

```bash
cd your-project
navin-cli
```

`navin-cli ~/code/app` works in another folder.

Desktop window on that folder:

```bash
navin .
```

## 3. Connect a model

In `navin-cli`, **Ctrl+G → Providers**: pick a provider, paste the API key (and an API base for local servers). Then **Models**: add a configuration and set it active.

Or an environment variable (`OPENROUTER_API_KEY`, …). Env keys are not written to `config.json`.

From a `navin-agi` source checkout:

```bash
.venv/bin/navin-cli
```

## 4. First turn

Type a request and press Enter. `/` lists commands. **Ctrl+P** is the palette. **?** is help.
