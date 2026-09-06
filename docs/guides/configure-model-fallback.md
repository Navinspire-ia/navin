# How to Configure Model Fallback in Navin

Model fallback lets Navin try a primary model first, then continue on one or more other configurations when the primary blocks: rate limit, outage, expired key, dropped model id, refusal, timeout.

## What happens by default

You already have fallback as soon as two text configurations exist. When the active model blocks, Navin asks it again at most twice (only for failures that clear in seconds, each wait capped at 10 seconds), then the next configuration whose provider has credentials answers that step. The chat shows a notice naming both models. A definitive refusal (4xx, bad key, content filter, exhausted account) or a timeout switches at once.

The automatic order is: the default configuration, then the configurations named by task routes, then the rest of the list alternating vendors, free-tier models last (five candidates at most). Configurations that are disabled, non-text (image, video, audio), or whose provider has no credentials are never used.

Follow the steps below only when you want to pin your own order.

## What you will build

- two or more model configurations in **Settings → Models**
- one active (primary) configuration
- an ordered fallback list

## When to use this

Use an explicit list when you want a fixed order across rate limits, provider outages, local model downtime, or cost-sensitive routing.

## Configure in Settings

1. Open Navin and add credentials under **Settings → Providers** for each provider you will use.
2. Open **Settings → Models** and create at least two configurations (for example **Fast** and **Deep**).
3. Set the everyday configuration as **Active**.
4. Open the fallback list for agent defaults (same Models area) and add the backup configuration names in order.
5. Send a chat message. If the primary provider fails in a retryable way, Navin tries the next named configuration.

Verify each provider works on its own (switch Active temporarily and chat) before adding it as a fallback.

### Advanced JSON shape (optional)

```json
{
  "modelPresets": {
    "fast": {
      "label": "Fast",
      "provider": "primary-provider",
      "model": "primary-model-id",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    },
    "deep": {
      "label": "Deep",
      "provider": "fallback-provider",
      "model": "fallback-model-id",
      "maxTokens": 4096,
      "contextWindowTokens": 200000,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast",
      "fallbackModels": ["deep"]
    }
  }
}
```

String entries in the fallback list are configuration names, not raw model IDs. Replace placeholder model IDs with IDs from your provider. The [Provider Cookbook](../provider-cookbook.md) has concrete Settings recipes.

## Production notes

- Keep fallback context windows realistic; smaller fallback windows constrain how much context can fit.
- Put cheaper or faster fallbacks before expensive ones when acceptable.
- Use `/model <name>` in chat for runtime switching without editing Settings permanently.
- Keep labels human-readable in the Models list.

## Security notes

- Different providers may have different data handling policies.
- Do not put provider keys in shared documents.
- Confirm fallback models can safely receive the same prompts and files.

## Troubleshooting

- Fallback never triggers: confirm the primary error is treated as retryable.
- Startup / save fails: check that each fallback name matches a configuration under Models.
- Output truncated after fallback: review max tokens and context window on the fallback configuration.

## Related docs

- [Providers and Models](../providers.md)
- [Provider Cookbook: Fallback Presets](../provider-cookbook.md#recipe-fallback-presets)
- [Configuration](../configuration.md)
