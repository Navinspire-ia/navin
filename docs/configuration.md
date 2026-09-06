# Configuration: Providers, Models, and Presets

This page explains how Navin chooses a provider, a model, and a named configuration. Everyday setup uses **Settings** in the desktop app. JSON below is an optional advanced reference. Terminal-oriented map: [CLI settings](./cli/settings.md).

Use it together with:

| Need | Page |
|---|---|
| Pasteable Settings recipes for a known provider | [`provider-cookbook.md`](./provider-cookbook.md) |
| Matching keys, model IDs, and common failures | [`providers.md`](./providers.md) |
| Fallback chains when a provider fails | [`guides/configure-model-fallback.md`](./guides/configure-model-fallback.md) |
| Custom OpenAI-compatible endpoints | [`guides/configure-openai-compatible-provider.md`](./guides/configure-openai-compatible-provider.md) |

Primary path: open Navin → **Settings → Providers** / **Models** / **Task routing**. Advanced users may also find `~/.navin/config.json` on disk; prefer Settings for secrets and day-to-day changes.

## Mental model

Three layers work together:

1. **Provider** - credentials and endpoint (API key, base URL, OAuth, proxy).
2. **Model configuration / preset** - a named pair of provider + model, plus generation settings.
3. **Active selection** - which preset the agent uses for normal turns.

In the app this maps to:

| In the app | Meaning |
|---|---|
| **Settings → Providers** | Credentials and endpoints |
| **Settings → Models** → add configuration | Named presets |
| **Settings → Models** → current configuration / Active badge | Default preset for chat |
| **Settings → Models** → Task routing | Role-based model picks |

A configuration name (label) is only a human-readable shortcut in the chat model selector. Prefer names that describe the model or its role, for example `Claude Sonnet`, `GLM 5.2`, or `Everyday coding` - not free-form task titles unrelated to the model.

## Quick start (Settings)

1. Open **Settings → Providers** and add an API key (or local base URL) for the service you use.
2. Open **Settings → Models**.
3. Click **Add model**, pick the provider, pick a model ID from the catalog (or type one), give a clear configuration name, then **Save**.
4. Select that configuration as **Current configuration** so it shows the **Active** badge.
5. Optionally open **Task routing** and assign cheaper or stronger presets to roles (`fast`, `dev`, `deep`, …). Matching workflows (`/forge`, `/blueprint`, studios, audits…) pick that model automatically; you can also switch with `/pilot <task>` in the composer.
6. Send a short test message in chat to confirm replies work.

## Providers

### Settings first

Use **Settings → Providers** for hosted keys (OpenRouter, Anthropic, OpenAI, …), local servers (Ollama, LM Studio, vLLM), and custom base URLs. Paste keys in the form fields; Navin stores them for the local install.

### Advanced JSON shape (optional)

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "${OPENROUTER_API_KEY}"
    },
    "anthropic": {
      "apiKey": "${ANTHROPIC_API_KEY}"
    },
    "ollama": {
      "apiBase": "http://127.0.0.1:11434/v1"
    },
    "custom": {
      "apiKey": "${CUSTOM_API_KEY}",
      "apiBase": "https://api.example.com/v1"
    }
  }
}
```

### Fields

| Field | Meaning |
|---|---|
| `apiKey` | Credential. Prefer `${ENV_VAR}` references in advanced edits. |
| `apiBase` | HTTP base URL. Usually required for local servers and custom endpoints; often omitted for built-in hosted providers. |
| `apiType` | Only for `providers.openai`: `auto`, `chat_completions`, or `responses`. |
| `proxy` | Optional HTTP proxy for OpenAI-compatible / Codex paths. Not used by native Anthropic, Bedrock, Azure OpenAI, or GitHub Copilot backends. |
| `extraHeaders` / `extraBody` / `extraQuery` | Provider-specific request extensions (gateways, Azure-style query params). |

### Common provider names

Use the registry name in presets (`provider: "openrouter"`), not the display label.

| Kind | Examples |
|---|---|
| Hosted direct | `openai`, `anthropic`, `gemini`, `deepseek`, `mistral`, `xai`, `groq` |
| Gateways | `openrouter`, `opencode` / `opencode_zen`, `opencode_go`, `huggingface`, `siliconflow` |
| Cloud / enterprise | `azure_openai`, `bedrock`, `github_copilot` |
| Local | `ollama`, `vllm`, `lm_studio`, `omniroute` (free multi-provider gateway on `localhost:20128`), `atomic_chat`, `ovms`, `custom` |
| OAuth / subscription | `openai_codex`, `github_copilot`, `xai_oauth` (sign in from Settings / provider login UI when offered) |

Full matching rules and failure tables: [`providers.md`](./providers.md). Settings recipes: [`provider-cookbook.md`](./provider-cookbook.md). Guided Ollama: [`guides/configure-ollama-local.md`](./guides/configure-ollama-local.md).

### Bedrock notes

AWS Bedrock uses the native Converse API. Prefer your usual AWS credential chain or a bearer token, set provider to Bedrock in Settings, and use Bedrock model IDs (for example `anthropic.claude-sonnet-4-5-v1:0`). Do not mix OpenRouter-style IDs with the Bedrock provider.

## Model configurations (presets)

### Settings first

**Settings → Models** creates and activates named configurations. Switch models from the chat model selector or with composer actions such as `/model` / `/pilot` depending on setup.

### Advanced JSON shape (optional)

```json
{
  "modelPresets": {
    "primary": {
      "label": "Claude Sonnet",
      "provider": "openrouter",
      "model": "anthropic/claude-sonnet-4.5",
      "maxTokens": 8192,
      "contextWindowTokens": 65536,
      "temperature": 0.1,
      "reasoningEffort": "medium"
    },
    "economy": {
      "label": "GLM 5.2",
      "provider": "openrouter",
      "model": "z-ai/glm-5.2",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "economy"
    }
  }
}
```

### Fields

| Field | Meaning |
|---|---|
| Key (`primary`, `economy`, …) | Stable internal name. Referenced by active preset, fallbacks, and task routes. Cannot be `default` (reserved). |
| `label` | Display name in Settings / chat selector. |
| `provider` | Registry provider name that owns the request. |
| `model` | Model ID **as that provider expects it** (gateway IDs often include a vendor prefix). |
| `maxTokens` | Max completion tokens for a turn. |
| `contextWindowTokens` | Context budget Navin uses when building prompts. |
| `temperature` | Sampling temperature. |
| `reasoningEffort` | Optional thinking effort (`low` / `medium` / `high` / `adaptive` / `none`) when the model supports it. |

### Implicit `default` preset

If you do not create named presets, Navin builds an implicit configuration named `default` from basic agent defaults. Named presets are clearer for switching and fallbacks.

## Task routing (`modelRoutes`)

Assign a **named model configuration** to each kind of work so expensive frontier models are only used when needed. Configure the map in **Settings → Models → Task routing**.

| Role | Typical use |
|---|---|
| `deep` | Architecture, hard debugging, large refactors |
| `dev` | Everyday coding |
| `fast` | Short questions, renames, commit messages |
| `search` | Web research turns |
| `plan` | Specs and step breakdowns |
| `review` | Diff / code review |
| `security` | Audits and permission-sensitive work |
| `docs` | READMEs, summaries, writing |

### Automatic application

When a message starts with a workflow slash action, Navin selects the routed preset for **that turn** (it wins over the chat model picker for that turn). Plain chat without a workflow action keeps the active / thread model.

| Role | Applied automatically by |
|---|---|
| `plan` | `/blueprint`, `/board` |
| `dev` | `/forge`, `/mobile`, `/ops` |
| `deep` | `/risklens`, `/debug` |
| `security` | `/fortify`, `/probe`, `/unmask`, `/lineage`, `/xray`, `/gatekeeper`, `/perimeter`, `/bastion`, `/vault`, `/threatmap`, `/redteam`, `/comply`, `/recon`, `/dast`, `/pentest` |
| `review` | `/inspect`, `/turbo` |
| `docs` | `/atlas`, `/report`, `/studio`, `/campaign`, `/leads`, `/seo` |
| `search` | `/scrape` |
| `fast` | `/pulse`, and the code editor inline assist |

### Manual switch (`/pilot`)

In chat: `/pilot` lists routes; `/pilot review` (for example) switches the **session** preset to the one assigned to that role (in memory until restart or another switch). Prefer automatic routing for workflows; use `/pilot` when you want to stay on a role across free-form turns.

### Advanced JSON shape (optional)

```json
{
  "modelRoutes": {
    "deep": "primary",
    "dev": "primary",
    "fast": "economy",
    "search": "economy",
    "plan": "primary",
    "review": "primary",
    "security": "primary",
    "docs": "economy"
  }
}
```

Values are preset names such as `primary` or `economy` - not free-form task titles.

## Model fallbacks

A model that blocks never ends the turn while another configured model can take the step. The rule is fixed: the chosen model is asked again at most twice (only for failures that clear in seconds: busy, 5xx, dropped connection, empty body), then the next model of the list answers that step and a notice names both models. A definitive refusal (bad key, dropped slug, 4xx, content filter, exhausted account) or a timeout switches at once, without a retry.

The list is automatic: when `fallbackModels` is not set, the other enabled text presets whose provider holds credentials form the chain - the default preset first, then the presets the task routes name, then the rest of the catalog alternating vendors (capped at five). To pin an explicit order, set the chain under **Settings → Models** (fallback list) or in advanced config:

```json
{
  "agents": {
    "defaults": {
      "modelPreset": "primary",
      "fallbackModels": ["economy", "localSmall"]
    }
  }
}
```

- Entries are **preset names**, not raw model IDs (unless you use an inline object; see [`providers.md`](./providers.md)).
- Context is built for the smallest window in the active chain so every candidate can accept the same prompt.

Step-by-step: [`guides/configure-model-fallback.md`](./guides/configure-model-fallback.md).

## End-to-end example (advanced JSON)

OpenRouter as gateway, one strong preset, one cheap preset, routing, and fallback - equivalent to creating two configurations and task routing in Settings:

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "${OPENROUTER_API_KEY}"
    }
  },
  "modelPresets": {
    "strong": {
      "label": "Claude Sonnet",
      "provider": "openrouter",
      "model": "anthropic/claude-sonnet-4.5",
      "maxTokens": 8192,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    },
    "fast": {
      "label": "GLM 5.2",
      "provider": "openrouter",
      "model": "z-ai/glm-5.2",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    }
  },
  "modelRoutes": {
    "deep": "strong",
    "dev": "strong",
    "fast": "fast",
    "docs": "fast"
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast",
      "fallbackModels": ["strong"]
    }
  }
}
```

## Naming rules that avoid confusion

- Configuration **label**: what you see in the list (`Claude Sonnet`, `GLM 5.2`).
- Preset **key**: stable id in JSON (`strong`, `fast`) - used by routes and fallbacks.
- Provider **name**: registry id (`openrouter`, `anthropic`).
- Model **id**: exact string the provider accepts (`anthropic/claude-sonnet-4.5` on OpenRouter vs `claude-sonnet-4-5` on Anthropic direct).

Never mix a key from provider A with a model id that only exists on provider B. That is the most common first-run failure.

## Related agent defaults

Useful neighbors under agent defaults (not exhaustive):

| Field | Role |
|---|---|
| Active model preset | Named configuration for normal turns |
| Fallback models | Ordered backup presets |
| Max tool iterations | Cap on tool-calling loops per turn |
| Timezone / bot name / icon | Display and scheduling context |
| Reasoning effort | Default thinking effort when the preset omits it |
| Workspace / project | Folder the agent treats as home |

## Heartbeat

While Navin is running, heartbeat can periodically read `HEARTBEAT.md` in the project, run quiet checks, and only notify a chat when something actionable appears. Use heartbeat for low-noise watch loops. Use scheduled automations when every run should produce a visible reminder. Details: [`automations.md`](./automations.md).

## Checklist

1. Provider has a valid key or local base URL in **Settings → Providers**.
2. Preset provider matches that entry.
3. Preset model is a real ID for that provider (use the Settings catalog when unsure).
4. Current configuration shows the **Active** badge.
5. Route and fallback entries only reference existing presets.
6. A short chat message succeeds before debugging chat channels.

## See also

- [`providers.md`](./providers.md) - matching, gateways, local servers, diagnosis
- [`provider-cookbook.md`](./provider-cookbook.md) - Settings recipes
- [`guides/configure-model-fallback.md`](./guides/configure-model-fallback.md)
- [`guides/configure-openai-compatible-provider.md`](./guides/configure-openai-compatible-provider.md)
- [`my-tool.md`](./my-tool.md) - runtime self-inspection
