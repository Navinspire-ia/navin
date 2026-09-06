# Provider Cookbook

Short Settings recipes for connecting common providers in the Navin desktop app. Each recipe lists what to enter in **Settings → Providers** and **Settings → Models**, and what a failure usually means.

If this is your first install, start with [`start-without-technical-background.md`](./start-without-technical-background.md). For field meanings, read [`providers.md`](./providers.md) and [`configuration.md`](./configuration.md).

Recipes are examples, not rankings. Pick the recipe that matches the credential, endpoint, and model ID you already intend to use.

## Choose a recipe

| What you have | Recipe | Must match |
|---|---|---|
| A gateway key and model IDs like `provider/model-name` | [OpenRouter Gateway](#recipe-openrouter-gateway) | API key, OpenRouter provider, gateway model ID |
| An OpenCode Zen or Go key | [OpenCode Zen or Go](#recipe-opencode-zen-or-go) | `OPENCODE_API_KEY` (or paste in Settings), Zen/Go provider, matching model ID |
| An OpenAI platform API key | [OpenAI Direct](#recipe-openai-direct) | OpenAI key, OpenAI provider, OpenAI model ID |
| An Anthropic API key | [Anthropic Direct](#recipe-anthropic-direct) | Anthropic key, Anthropic provider, non-gateway model ID |
| A Kimi Coding Plan key | [Kimi Coding Plan](#recipe-kimi-coding-plan) | Kimi Coding key, `kimi_coding` provider, `kimi-for-coding` |
| An OpenAI-compatible `/v1` endpoint | [Custom OpenAI-Compatible Provider](#recipe-custom-openai-compatible-provider) | Base URL, optional key, model ID from that endpoint |
| Ollama already running locally | [Ollama Local Model](#recipe-ollama-local-model) | Ollama base URL, pulled model, local server up |
| Ollama not installed yet | [Configure Ollama locally](./guides/configure-ollama-local.md) | Settings → Providers → Ollama panel |
| vLLM, LM Studio, or similar | [vLLM or LM Studio](#recipe-vllm-or-lm-studio) | Local `/v1` base URL, any required key, served model name |
| No key at all, want free models now | [OmniRoute Free Gateway](#recipe-omniroute-free-gateway) | OmniRoute running on `localhost:20128`, model `auto` |
| A primary model plus backups | [Fallback Presets](#recipe-fallback-presets) | Named configurations + fallback list in Settings → Models |

## How to use a recipe

1. Open Navin.
2. Open **Settings → Providers** and enter the key or base URL from the recipe.
3. Open **Settings → Models**, add a configuration with that provider and model ID, then set it **Active**.
4. Send a short chat message to verify.
5. Optionally connect channels under **Settings → Channels**.

Prefer pasting keys into Settings. If you use OS environment variables, set them before launching Navin so the app can resolve `${VAR}` references in advanced config.

## Recipe: OpenRouter Gateway

1. **Settings → Providers → OpenRouter** - paste your OpenRouter API key.
2. **Settings → Models** - add a configuration:
   - Provider: `openrouter`
   - Model: for example `anthropic/claude-sonnet-4.5`
   - Label: `Primary` (or any clear name)
3. Set it **Active** and chat.

If chat fails with unauthorized, re-paste the key. If the model is not found, pick an ID OpenRouter lists for your account.

## Recipe: OpenCode Zen or Go

Both use an OpenCode API key; pick the provider that matches your subscription.

**Zen**

1. **Settings → Providers → OpenCode Zen** - paste the key.
2. **Settings → Models** - provider `opencode_zen`, model such as `opencode/deepseek-v4-pro`.

**Go**

1. **Settings → Providers → OpenCode Go** - paste the key.
2. **Settings → Models** - provider `opencode_go`, model such as `opencode-go/deepseek-v4-flash`.

Use model IDs that OpenCode lists for the chat/completions-compatible path. Set the configuration Active and test in chat.

## Recipe: OpenAI Direct

1. **Settings → Providers → OpenAI** - paste `OPENAI_API_KEY`.
2. **Settings → Models** - provider `openai`, model such as `gpt-5` (use an ID available to your account).
3. Set Active and chat.

Leave OpenAI `apiType` alone unless Settings document a specific mode you need.

## Recipe: Anthropic Direct

1. **Settings → Providers → Anthropic** - paste `ANTHROPIC_API_KEY`.
2. **Settings → Models** - provider `anthropic`, model such as `claude-sonnet-4-5` (Anthropic ID, not `anthropic/claude-…`).
3. Set Active and chat.

If you copied `anthropic/claude-sonnet-4.5`, that gateway-style path belongs under OpenRouter, not Anthropic direct.

For an Anthropic-compatible proxy, keep provider Anthropic and set the custom base URL in the Anthropic provider panel.

## Recipe: Kimi Coding Plan

1. **Settings → Providers → Kimi Coding** - paste the Coding Plan key.
2. **Settings → Models** - provider `kimi_coding`, model `kimi-for-coding`.
3. Set Active and chat.

Do not configure Kimi Coding as a generic custom OpenAI provider; Navin uses a dedicated path for this plan.

## Recipe: Custom OpenAI-Compatible Provider

1. **Settings → Providers** - add **Custom** (or a named custom entry).
2. Set base URL (include `/v1` when required) and API key if needed.
3. **Settings → Models** - point a configuration at that provider name and the model ID the endpoint serves.
4. Set Active and chat.

For multiple custom endpoints, create separate provider entries (for example Work proxy and Lab local) instead of overloading one Custom block. Anthropic-compatible proxies should use the Anthropic provider with a custom base URL, not a generic custom OpenAI entry.

## Recipe: Ollama Local Model

Prefer **Settings → Providers → Ollama** (Detect → Install → Pull → Configure). Details: [Configure Ollama locally](./guides/configure-ollama-local.md).

When Ollama is already running with a pulled model:

1. Confirm the Ollama panel shows a healthy server.
2. Pull or select `llama3.2` (or another local tag).
3. Use **Configure / Use** so Navin creates an Active local configuration.
4. Chat in the desktop app.

If replies fail with connection refused, start Ollama from its app or the Settings panel, then retry.

## Recipe: vLLM or LM Studio

**vLLM (example)**

1. **Settings → Providers → vLLM** (or custom named entry) - base URL `http://127.0.0.1:8000/v1`, key `EMPTY` if required.
2. **Settings → Models** - provider `vllm`, model = the name your server serves.

**LM Studio**

1. **Settings → Providers → LM Studio** - base URL `http://localhost:1234/v1`.
2. **Settings → Models** - provider `lm_studio`, model = your loaded local model.

Set Active and chat. Ensure the local server is running before testing Navin.

## Recipe: OmniRoute Free Gateway

[OmniRoute](https://github.com/diegosouzapw/OmniRoute) is a local, MIT-licensed gateway to 350+ providers (150+ free tiers). It answers right after install, no signup and no key.

1. Install and start it: `npm install -g omniroute` then `omniroute` (dashboard and API on `http://localhost:20128`).
2. **Settings → Providers → OmniRoute** - keep the base URL `http://localhost:20128/v1`, Auth **None**, then **Test connection**. It should list the `auto` combos plus every model OmniRoute exposes.
3. **Settings → Models** - provider `omniroute`, model `auto` (or `auto/coding`, `auto/fast`, or a specific id such as `oc/kimi-k2.5`).
4. Set Active and chat. Connect more upstream providers from the OmniRoute dashboard to widen the `auto` pool.

If OmniRoute runs with `REQUIRE_API_KEY=true`, switch Auth to **Bearer** and paste a key from its dashboard (Endpoints).

## Recipe: Fallback Presets

1. Create two or more configurations under **Settings → Models** (for example Fast on OpenRouter, Deep on Anthropic, Local on Ollama).
2. Set Fast as **Active**.
3. Add Deep and Local (in order) to the fallback list under Models / agent defaults.
4. Chat normally. On retryable primary failures, Navin tries the next named configuration.

Fallback entries are configuration names, not raw model IDs. Keep context windows realistic across the chain. See [Configure model fallback](./guides/configure-model-fallback.md).

## Recipe: Switch models at runtime

After you have more than one configuration, use the chat model selector or composer actions:

```text
/model
/model local
/model fast
```

Runtime switches do not permanently rewrite Settings until you change the Active configuration. An in-progress turn keeps using the model it started with.

## Quick failure map

| Symptom | Usually means | First check |
|---|---|---|
| Unauthorized / invalid API key | Key missing, wrong, or under the wrong provider | Re-paste under **Settings → Providers** |
| Model not found | Model ID does not belong to the selected provider | Compare provider + model in **Settings → Models** |
| Connection refused | Local server down or wrong base URL | Start Ollama / LM Studio / vLLM / OmniRoute; fix base URL |
| Provider not found | Misspelled provider registry name | Use names such as `openrouter`, `openai`, `anthropic`, `ollama`, `vllm`, `lm_studio`, `omniroute` |

## Next references

| Need | Read |
|---|---|
| Field meanings and provider resolution | [`providers.md`](./providers.md) |
| Settings-first configuration | [`configuration.md`](./configuration.md) |
| First launch without a technical background | [`start-without-technical-background.md`](./start-without-technical-background.md) |
