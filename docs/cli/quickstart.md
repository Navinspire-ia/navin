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

Or **Account** in the same Settings: sign in on navin.live for a paid managed key.

Or an environment variable (`OPENROUTER_API_KEY`, …). Env keys are not written to `config.json`.

## 4. First turn

Type a request and press Enter. `/` lists commands. **Ctrl+P** is the palette. **?** is help.

## 5. License (optional)

```bash
navin license activate NAVIN-XXXX-XXXX-XXXX-XXXX
navin license status
```

Or **Account → Sign in** in `navin-cli`.
