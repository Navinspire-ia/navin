# Organization gateway (Navin SM)

An organization runs its own LiteLLM proxy in front of the providers it chose
(Azure OpenAI, Bedrock, Vertex, vLLM, Ollama...). Navin talks to that proxy
only, with the models the organization lists. The file is produced by Navin
Manager (Navin SM profiles) or written by hand.

## Where Navin reads it

1. The managed settings file (`NAVIN_MANAGED_SETTINGS`, then
   `/etc/navin/managed-settings.json`, `/Library/Application Support/Navin/managed-settings.json`,
   `%ProgramData%\Navin\managed-settings.json`). Deployed by the IT
   department (MDM, GPO, Ansible): the user cannot remove it.
2. Otherwise the file the user imported in Settings > Organization (or with
   `navin org import <file>`), kept as `~/.navin/organization.json`.

The two top-level keys below sit next to the managed settings keys
(`permissions`, `mcpAllowlist`, `env`...), so one file carries the gateway and
the policy. An imported file only brings `organization` and `gateway`; its
policy keys are ignored (only an administrator can enforce a policy).

## Format (version 1)

```json
{
  "organization": {
    "id": "banque-x",
    "name": "Banque X",
    "supportContact": "it@banque-x.example"
  },
  "gateway": {
    "type": "litellm",
    "baseUrl": "https://llm.banque-x.internal",
    "auth": "user_key",
    "headers": {"X-Tenant": "retail"},
    "caBundle": "/etc/pki/tls/certs/banque-x-ca.pem",
    "models": [
      {"id": "gpt-4o", "label": "GPT-4o (Azure)", "contextWindow": 128000,
       "maxOutputTokens": 16384, "vision": true},
      {"id": "claude-sonnet", "label": "Claude Sonnet (Bedrock)", "reasoningEffort": "low"},
      {"id": "llama-local", "label": "Llama 3.3 (vLLM)"}
    ],
    "defaultModel": "gpt-4o",
    "routes": {"fast": "llama-local"},
    "exclusive": true
  }
}
```

| Key | Rule |
| --- | --- |
| `organization.id` | required, lowercase letters, digits and `-` (1 to 64) |
| `organization.name` | required, shown in Settings and in the model picker |
| `organization.supportContact` | optional, shown to users when the gateway fails |
| `gateway.type` | required, `litellm` (any OpenAI compatible proxy works) |
| `gateway.baseUrl` | required, `http` or `https` address of the proxy, without `/chat/completions` |
| `gateway.auth` | `user_key` (each user enters the virtual key LiteLLM issued to them), `shared_key` (the file carries `apiKey`), `none` (the proxy authenticates by network) |
| `gateway.apiKey` | required with `shared_key`, refused otherwise; never written to the user's settings |
| `gateway.headers` | optional map of extra HTTP headers (strings); `Authorization`, `Proxy-Authorization`, `Cookie`, `Api-Key` and `X-Api-Key` are refused (a secret never travels in a header of the file) |
| `gateway.caBundle` | optional PEM file trusted on top of the public roots (internal PKI) |
| `gateway.models` | required, 1 to 200 entries; `id` is the LiteLLM `model_name`, unique |
| `models[].contextWindow`, `models[].maxOutputTokens` | optional positive integers |
| `models[].vision` | optional, the model accepts images |
| `models[].reasoningEffort` | optional, `none`, `minimal`, `low`, `medium` or `high` |
| `gateway.defaultModel` | required, one of the model ids |
| `gateway.routes` | optional map of Navin task roles to model ids |
| `gateway.exclusive` | optional, default `true`: only this gateway can be used (personal providers and the Navin account are refused) |

## What Navin does with it

- A provider `organization` points at `baseUrl`; every model becomes the
  preset `org:<id>`, labelled with its `label`.
- The default model is `defaultModel` until the user picks another
  organization model. In exclusive mode a model of any other provider is
  refused with the organization name in the message.
- Nothing managed by the file is written to `~/.navin/config.json`: only the
  user's own key (`user_key`), encrypted like every other secret.
- A file that exists but is invalid is reported in Settings > Organization
  with the reason. A managed file with an invalid `gateway` blocks every
  model call (fail closed) instead of falling back to personal providers.
