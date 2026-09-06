# How to Configure an OpenAI-Compatible Provider in Navin

Navin can call OpenAI-compatible model providers by setting a base URL, optional API key, and a model configuration that references that provider - from **Settings**.

## What you will build

- a custom provider entry in Settings
- a model configuration pointing at that provider
- one successful chat reply

## When to use this

Use this for local or hosted services that expose OpenAI-compatible endpoints, including internal gateways, local model servers, and provider proxies that are not already named in Navin.

## Configure in Settings

1. Open Navin → **Settings → Providers**.
2. Add a **custom** (or named custom) provider.
3. Paste the API key if required, and set the base URL (include `/v1` when the service expects it).
4. Open **Settings → Models**, add a configuration that uses that provider and the model ID the service serves.
5. Set the configuration **Active**.
6. Send a short test message in chat.

### Advanced JSON shape (optional)

```json
{
  "providers": {
    "custom": {
      "apiKey": "${CUSTOM_API_KEY}",
      "apiBase": "https://api.example.com/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "label": "Custom",
      "provider": "custom",
      "model": "provider-model-name",
      "maxTokens": 4096,
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

Prefer Settings for everyday setup. Confirm the provider's own dashboard or docs list the model ID before debugging Navin.

## Production notes

- Include the version path in the base URL when the service expects `/v1`.
- Use separate provider names for separate endpoints.
- Use a placeholder key such as `EMPTY` only when the endpoint requires a non-empty key but does not validate it.
- Leave OpenAI `apiType` unset for ordinary OpenAI-compatible custom endpoints.

## Security notes

- Keep provider keys in Settings or OS environment variables.
- Treat internal model gateways as sensitive network services.
- Do not point Navin at untrusted proxy endpoints for private projects.

## Troubleshooting

- Endpoint unreachable: fix the provider service or base URL before changing Navin.
- Model unknown: check the model ID expected by the provider.
- Auth fails: confirm Bearer auth requirements and that the key is saved in Settings for this install.

## Related docs

- [Provider Cookbook: Custom OpenAI-Compatible Provider](../provider-cookbook.md#recipe-custom-openai-compatible-provider)
- [Providers: Custom OpenAI-Compatible Endpoint](../providers.md#custom-openai-compatible-endpoint)
