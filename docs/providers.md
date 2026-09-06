# Providers and Models

Use this page when the first reply fails because of provider/model mismatch, or when you want to adapt a setup to a different provider. If you already know which provider you want and only need a short Settings recipe, use [`provider-cookbook.md`](./provider-cookbook.md).

For normal setup in the Navin desktop app, open **Settings → Providers** and **Settings → Models** to add credentials, create a model configuration, and select the active model. Use the JSON below only for advanced edits, local endpoints, provider-specific fields, or diagnosis.

For every setup, answer three questions:

1. Which provider owns the credential or endpoint?
2. What model name does that provider expect?
3. Does the provider need `apiKey`, `apiBase`, OAuth login, cloud credentials, or only a local server URL?

Prefer a named `modelPresets` entry for the model/provider pair, then select it with `agents.defaults.modelPreset`. Direct `agents.defaults.provider` and `agents.defaults.model` still work for existing configs, but presets make runtime `/model` switching and fallback chains clearer. Pin `provider` inside the preset while setting up; you can switch back to `"auto"` later.

## Choose a Provider Without Guessing

The docs show concrete provider names so the JSON is copyable, not because navin ranks providers. Start from the service or endpoint you actually control:

| If you have... | Configure... |
|---|---|
| An API key from a hosted provider or gateway | That provider's `providers.<name>.apiKey`, then a preset with that provider name and a model ID from that service. |
| An OpenCode Zen or Go key | `providers.opencodeZen.apiKey` or `providers.opencodeGo.apiKey`, then a preset with `provider: "opencode_zen"` or `provider: "opencode_go"`. |
| A company proxy or regional endpoint | The matching provider block plus `apiBase` if the proxy gives you a URL. |
| A local OpenAI-compatible server | A local provider block such as `ollama`, `vllm`, `lmStudio`, or `custom`, usually with `apiBase`. For Ollama, prefer Settings → Providers → Ollama ([guide](./guides/configure-ollama-local.md)). |
| An OAuth-based account | Use the provider sign-in flow in **Settings → Providers** when offered, then select that provider explicitly in a model configuration. |
| No provider yet | Pick one outside Navin based on account access, pricing, regional availability, privacy requirements, and the model IDs you need. Then paste its key and model ID in Settings. |

## Minimal Shape

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "openrouter",
      "model": "anthropic/claude-opus-4.5",
      "maxTokens": 8192,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

The provider config gives navin credentials and endpoint details. The model preset names the provider/model pair. The agent defaults choose which named preset to use for normal turns. Replace the example provider and model together; mixing an API key from one provider with a model ID from another is the most common first-run failure.

## Provider, Model, API Key, and Base URL

These fields answer different questions:

| Field | Where it lives | Meaning |
|---|---|---|
| `provider` | `modelPresets.<name>.provider` | Which navin provider adapter should send the request. |
| `model` | `modelPresets.<name>.model` | The model ID expected by that provider or gateway. |
| `apiKey` | `providers.<provider>.apiKey` | Credential for that provider. Use `${ENV_VAR}` for secrets. |
| `apiBase` | `providers.<provider>.apiBase` | HTTP base URL of the provider endpoint. |
| `proxy` | `providers.<provider>.proxy` | Optional HTTP proxy for this provider only. Supported for OpenAI-compatible providers and OpenAI Codex. |

You usually omit `apiBase` for hosted built-in providers such as OpenRouter, Anthropic direct, OpenAI direct, Groq, or Bedrock because navin knows their default endpoints. Set `apiBase` for `custom`, local OpenAI-compatible servers, provider proxies, regional endpoints, or subscription endpoints. Include the API version path when the endpoint requires it, for example `https://api.example.com/v1` or `http://localhost:11434/v1`.

Use `proxy` when one provider must send HTTP traffic through a proxy without changing process-wide `HTTP_PROXY` / `HTTPS_PROXY`. This is supported for providers that use navin's OpenAI-compatible client, including `openai`, `custom`, named custom providers, OpenRouter-style gateways, local OpenAI-compatible servers, and similar registry entries. It is also supported for `openai_codex`, including Codex OAuth token exchange/refresh and Codex Responses API requests. Native provider backends such as `anthropic`, `bedrock`, `azure_openai`, and `github_copilot` reject `proxy`; use their endpoint-specific configuration instead.

## Common Provider Patterns

### OpenRouter Gateway

Gateway-style setup for model IDs served through OpenRouter.

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "${OPENROUTER_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "openrouter",
      "model": "anthropic/claude-opus-4.5",
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Use the model ID exactly as OpenRouter lists it.

### OpenCode Zen and Go

OpenCode Zen and OpenCode Go are OpenCode-managed gateways for coding-agent models.
They share `OPENCODE_API_KEY`, but use separate provider config keys and default base
URLs in navin.

```json
{
  "providers": {
    "opencodeZen": {
      "apiKey": "${OPENCODE_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "opencode_zen",
      "model": "opencode/deepseek-v4-pro",
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

For OpenCode Go, switch the provider block and preset:

```json
{
  "providers": {
    "opencodeGo": {
      "apiKey": "${OPENCODE_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "opencode_go",
      "model": "opencode-go/deepseek-v4-flash",
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  }
}
```

OpenCode documents model IDs with `opencode/<model-id>` for Zen and
`opencode-go/<model-id>` for Go. navin accepts those prefixes and strips them
before sending the request to OpenCode. Use model IDs that OpenCode lists under
the `chat/completions` endpoint; models listed only under `responses`,
`messages`, or provider-specific endpoints are not handled by this
OpenAI-compatible provider path.

### Anthropic Direct

```json
{
  "providers": {
    "anthropic": {
      "apiKey": "${ANTHROPIC_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "anthropic",
      "model": "claude-opus-4-5",
      "maxTokens": 8192,
      "contextWindowTokens": 200000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Anthropic direct uses the native Anthropic provider. Do not use an OpenRouter model ID unless the provider is OpenRouter.

If you use an Anthropic-compatible proxy, keep the provider as `anthropic` and override `apiBase`:

```json
{
  "providers": {
    "anthropic": {
      "apiKey": "${ANTHROPIC_API_KEY}",
      "apiBase": "https://anthropic-proxy.example.com"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "anthropic",
      "model": "claude-sonnet-4-5"
    }
  }
}
```

Arbitrary custom provider names are OpenAI-compatible only; they do not use the Anthropic Messages API request format.

### OpenAI Direct

```json
{
  "providers": {
    "openai": {
      "apiKey": "${OPENAI_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "openai",
      "model": "gpt-5",
      "maxTokens": 8192,
      "contextWindowTokens": 128000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

`providers.openai.apiType` may be set when you need to force a specific OpenAI API surface. Other providers reject `apiType`; leave it unset outside `providers.openai`. Replace the model with a model ID available to your OpenAI account.

### Mistral

```json
{
  "providers": {
    "mistral": {
      "apiKey": "${MISTRAL_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "mistral",
      "model": "mistral-large-latest",
      "maxTokens": 8192,
      "contextWindowTokens": 128000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Codestral, Ministral, Devstral, and Magistral model ids also match this
provider. Magistral reasoning models reject `reasoningEffort` on the wire;
navin remaps ordinary effort values for other Mistral models.

### Groq

```json
{
  "providers": {
    "groq": {
      "apiKey": "${GROQ_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "groq",
      "model": "llama-3.3-70b-versatile",
      "maxTokens": 8192,
      "contextWindowTokens": 128000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Pin `provider: "groq"` for Groq model ids. The same key can also power
Whisper-style transcription under Settings → Voice.

### Hugging Face Inference

```json
{
  "providers": {
    "huggingface": {
      "apiKey": "${HF_TOKEN}"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "huggingface",
      "model": "meta-llama/Llama-3.1-8B-Instruct",
      "maxTokens": 8192,
      "contextWindowTokens": 128000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Default base is `https://router.huggingface.co/v1`. Env aliases:
`HF_TOKEN`, `HUGGINGFACE_TOKEN`, `HUGGING_FACE_HUB_TOKEN`.

### NVIDIA NIM

```json
{
  "providers": {
    "nvidia": {
      "apiKey": "${NVIDIA_NIM_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "nvidia",
      "model": "nvidia/llama-3.1-nemotron-70b-instruct",
      "maxTokens": 8192,
      "contextWindowTokens": 128000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Keys usually start with `nvapi-`. Alias: `NVIDIA_API_KEY`. Cloud Nemotron
ids route here, not to local Ollama.

### Custom OpenAI-Compatible Endpoint

The `custom` provider fits one OpenAI-compatible endpoint that is not represented by a named provider.

```json
{
  "providers": {
    "custom": {
      "apiKey": "${CUSTOM_API_KEY}",
      "apiBase": "https://example.com/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "custom",
      "model": "provider-model-name",
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

`custom` does not infer a default base URL. Set `apiBase`.

If you have more than one custom OpenAI-compatible endpoint, give each endpoint its own provider key under `providers` and use that same key in the model preset. The key can be a name that makes sense in your environment, such as `companyProxy`, `tenant-a`, or `dev-local`.

```json
{
  "providers": {
    "companyProxy": {
      "apiKey": "${COMPANY_PROXY_API_KEY}",
      "apiBase": "https://llm-proxy.example.com/v1"
    },
    "tenant-a": {
      "apiBase": "https://tenant-a.example.com/v1"
    }
  },
  "modelPresets": {
    "company": {
      "provider": "companyProxy",
      "model": "gpt-4o-mini",
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    },
    "tenantA": {
      "provider": "tenant-a",
      "model": "served-model-name",
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "company"
    }
  }
}
```

Custom provider keys are treated as direct OpenAI-compatible providers. `apiBase` is required because navin cannot know the endpoint URL. `apiKey` is optional for local servers or private proxies that do not require one. Choose a name that does not conflict with a built-in provider name or alias, such as `openai`, `openai-codex`, `github-copilot`, or `lm-studio`. Do not set `apiType` on custom provider keys; `apiType` is only for `providers.openai`.

If your custom endpoint documents a nonstandard thinking toggle, set `providers.<name>.thinkingStyle` to `thinking_type`, `enable_thinking`, or `reasoning_split`; navin then maps `reasoningEffort` onto that provider-specific request body. Leave it unset for ordinary OpenAI-compatible endpoints.

This named custom provider path is not for Anthropic-compatible endpoints. For Anthropic-compatible proxies, use `providers.anthropic.apiBase` and set the preset provider to `anthropic`.

### Ollama

Prefer **Settings → Providers → Ollama** and the Local Ollama setup panel
(Detect → Install → Pull → Configure). Full walkthrough:
[Configure Ollama locally](./guides/configure-ollama-local.md).

Manual equivalent: start Ollama, pull a model, then point navin at the
OpenAI-compatible endpoint.

```json
{
  "providers": {
    "ollama": {
      "apiBase": "http://localhost:11434/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "ollama",
      "model": "llama3.2",
      "maxTokens": 4096,
      "contextWindowTokens": 32768
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Most Ollama setups do not require an API key. An empty `providers.ollama`
block is not enough for auto-routing of bare model names such as `llama3.2`;
set `apiBase` or pin `provider: "ollama"`.

### vLLM or Other Local OpenAI-Compatible Server

`apiBase` is required for vLLM (there is no safe default port). Without it,
navin refuses to build the provider so requests cannot accidentally hit a
cloud endpoint.

```json
{
  "providers": {
    "vllm": {
      "apiBase": "http://127.0.0.1:8000/v1",
      "apiKey": "EMPTY"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "vllm",
      "model": "served-model-name",
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Some OpenAI-compatible local servers require any non-empty API key even when they do not validate it.

### LM Studio

```json
{
  "providers": {
    "lmStudio": {
      "apiBase": "http://localhost:1234/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "lm_studio",
      "model": "local-model",
      "maxTokens": 4096,
      "contextWindowTokens": 32768
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Config keys may be camelCase or snake_case. Provider names in model presets should use the registry name, such as `lm_studio`.

### OmniRoute (local free AI gateway)

[OmniRoute](https://github.com/diegosouzapw/OmniRoute) is an MIT-licensed gateway that runs on
your machine (`npm install -g omniroute && omniroute`) and exposes 350+ upstream providers,
including 150+ free tiers, behind one OpenAI-compatible endpoint at
`http://localhost:20128/v1`. A fresh install answers without any key: the model id `auto`
builds a virtual combo from the connected providers and fails over on quota or errors.

```json
{
  "providers": {
    "omniroute": {
      "apiBase": "http://localhost:20128/v1"
    }
  },
  "modelPresets": {
    "free": {
      "provider": "omniroute",
      "model": "auto",
      "maxTokens": 8192,
      "contextWindowTokens": 128000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "free"
    }
  }
}
```

- `auto` is the balanced default. `auto/coding`, `auto/fast`, `auto/cheap`, `auto/offline` and
  `auto/smart` weight the routing differently. Any upstream id listed by OmniRoute works too,
  for example `oc/kimi-k2.5` or `openai/gpt-5.4`: keep the prefix, OmniRoute routes on it.
- `omniroute/auto` also works as a model id without a preset; the `omniroute/` routing prefix
  is stripped before the request leaves Navin.
- No key is needed while OmniRoute keeps `REQUIRE_API_KEY` off (its default). If you turn it
  on, paste a key from the OmniRoute dashboard (Endpoints) in **Settings → Providers →
  OmniRoute** with Auth set to Bearer, or export `OMNIROUTE_API_KEY`.
- **Settings → Providers → OmniRoute → Test connection** probes `GET /v1/models`; the model
  picker searches that list on demand because the catalog holds 1000+ ids.

### AWS Bedrock

Bedrock can use the AWS credential chain, profile, region, or Bedrock bearer token depending on your AWS setup.

```json
{
  "providers": {
    "bedrock": {
      "region": "us-east-1",
      "profile": "default"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "bedrock",
      "model": "bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0",
      "maxTokens": 8192,
      "contextWindowTokens": 200000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

See `configuration.md#providers` for Bedrock-specific notes.

### OAuth Providers

Some providers do not use a pasted API key. In the desktop app, open **Settings → Providers** and use the sign-in / connect flow for OpenAI Codex or GitHub Copilot when those panels offer it. After login, create or select a model configuration that pins that provider.

OAuth providers are not valid automatic fallbacks. If login fails, check network/proxy settings and the exact provider name in Settings.

## Provider Resolution

The recommended path is a named preset selected by `agents.defaults.modelPreset`. The effective model parameters come from:

1. the named `modelPresets` entry referenced by `agents.defaults.modelPreset`;
2. otherwise the implicit `default` preset built from `agents.defaults.model`, `provider`, `maxTokens`, `contextWindowTokens`, `temperature`, and related fields.

Provider selection follows this practical rule:

- Explicit `provider` in the active preset or implicit default config wins.
- `provider: "auto"` tries model-name keywords, configured keys, local base URLs, and gateway providers.
- Gateway providers such as OpenRouter and AiHubMix can route many model families, so the model name must be valid for that gateway.
- Local providers should normally be explicit because generic local model names such as `llama3.2` do not always contain provider keywords.
- Local providers match in auto mode only when opted in (`apiBase` set), except explicit prefixes such as `ollama/<model>` which may use the registry default base.
- Pin `provider` for gateway catalog IDs (`anthropic/claude-…` on OpenRouter) so they do not route to the direct Anthropic provider when both keys exist.

### Model Name Prefixes

`family/model-name` does not always select provider `family`. Prefix-based provider inference only runs when the active provider is `"auto"`.

- Explicit provider wins: `provider: "openrouter"` with `model: "anthropic/claude-sonnet-4.5"` calls OpenRouter, not Anthropic.
- With `provider: "auto"`, a prefix matching a configured built-in or named custom provider can select that provider. Named custom prefixes are stripped before request, so `companyProxy/gpt-4o-mini` is sent upstream as `gpt-4o-mini`.
- With an explicit named custom provider, the model is sent as written; `provider: "companyProxy"` with `model: "openai/gpt-4o-mini"` sends `openai/gpt-4o-mini` to `companyProxy`.

Pin `provider` in presets when using gateway catalog IDs such as `anthropic/claude-sonnet-4.5`.

## Model Presets

Model presets are the recommended model configuration surface. Use them when you want named model choices, runtime `/model` switching, or reusable fallback targets.

```json
{
  "modelPresets": {
    "fast": {
      "label": "Fast",
      "provider": "openrouter",
      "model": "anthropic/claude-sonnet-4.5",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    },
    "deep": {
      "label": "Deep",
      "provider": "anthropic",
      "model": "claude-opus-4-5",
      "maxTokens": 8192,
      "contextWindowTokens": 200000,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast"
    }
  }
}
```

The preset name `default` is reserved for the implicit `agents.defaults` settings. Do not define `modelPresets.default`; use `/model default` to return to the direct `agents.defaults.*` fields in older configs.

## Fallback Models

A model that blocks hands the step to the next model of the list instead of ending the turn. The chosen model is retried at most twice, only for failures that clear in seconds (busy, 5xx, dropped connection, empty body, unclassified glitch), each wait capped at 10 seconds; a definitive refusal (4xx, bad or expired key, dropped slug, content filter, exhausted account) or a timeout switches immediately. Every switch is announced in the chat with both model names. A model whose provider named a longer wait (`Retry-After`) or ran out of credit is skipped for that long (at most a minute) so the following steps do not pay the same refusal.

When `fallbackModels` is empty, the list of configured presets is the fallback list: enabled text presets whose provider holds credentials, ordered as the default preset, then the presets named by task routes, then the rest of the catalog alternating vendors, free-tier slugs last, five candidates at most. Each automatic candidate runs with the active preset's `maxTokens`, `contextWindowTokens` and `temperature`, so the chain never shrinks the context the turn was planned against. Set `fallbackModels` to pin an explicit order instead. Keep fallbacks compatible with the task size and tool use. Prefer fallback presets so each candidate has a name and a complete provider, model, generation, and context-window configuration.

```json
{
  "modelPresets": {
    "fast": {
      "label": "Fast",
      "provider": "openrouter",
      "model": "anthropic/claude-sonnet-4.5",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    },
    "deep": {
      "label": "Deep",
      "provider": "anthropic",
      "model": "claude-opus-4-5",
      "maxTokens": 8192,
      "contextWindowTokens": 200000,
      "temperature": 0.1
    },
    "localSmall": {
      "label": "Local Small",
      "provider": "ollama",
      "model": "llama3.2",
      "maxTokens": 4096,
      "contextWindowTokens": 32768,
      "temperature": 0.2
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast",
      "fallbackModels": ["deep", "localSmall"]
    }
  }
}
```

String entries in `fallbackModels` are preset names, not raw model names. navin tries them in order after the active preset. Each fallback preset uses its own `provider`, `model`, `maxTokens`, `contextWindowTokens`, `temperature`, and optional `reasoningEffort`.

Use inline fallback objects only when a model is not worth naming as a preset:

```json
{
  "modelPresets": {
    "fast": {
      "provider": "openrouter",
      "model": "anthropic/claude-sonnet-4.5",
      "maxTokens": 4096,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast",
      "fallbackModels": [
        {
          "provider": "deepseek",
          "model": "deepseek-v4-pro",
          "maxTokens": 4096,
          "contextWindowTokens": 262144
        }
      ]
    }
  }
}
```

`fallbackModels` belongs under `agents.defaults`, not inside each preset. If fallback candidates use smaller context windows, navin builds context using the smallest window in the active chain so every candidate can receive the same prompt. See `configuration.md#model-fallbacks` for failure conditions.

## Quick checks

Before debugging a chat channel, verify the desktop chat:

1. Open **Settings → Providers** and confirm the key or base URL.
2. Open **Settings → Models** and confirm a configuration is **Active**.
3. Send a short message in the Navin chat window.

| Symptom | Likely cause |
|---|---|
| 401, unauthorized, invalid API key | Key is missing, expired, copied with whitespace, or stored under the wrong provider |
| model not found | Model ID does not exist for the selected provider or gateway |
| connection refused | Local provider server is not running or the base URL points to the wrong port |
| provider not found | The active configuration uses a misspelled provider; use registry names such as `openrouter`, `anthropic`, `ollama`, `vllm`, `lm_studio` |
| works in desktop chat but not a channel | Provider is fine; debug **Settings → Channels** and keep Navin open |

For Settings-first configuration and advanced notes, see [`configuration.md`](./configuration.md).
