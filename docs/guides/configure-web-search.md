# How to Configure Web Search for a Navin AI Agent

Navin includes built-in web search and web fetch tools. Search uses DuckDuckGo by default and can be configured for API-backed or self-hosted providers from Settings.

## What you will build

- web tools enabled in Navin
- one search provider selected in Settings
- optional web fetch settings for page reading

## When to use this

Configure web search when the agent needs current information, public web research, source discovery, or page fetching during a task.

## Configure in Settings

1. Open Navin and confirm a model works under **Settings → Providers** / **Models**.
2. Open **Settings → Web**.
3. Enable web search, choose a provider, and enter its API key if required.
4. Save and restart when prompted.
5. Ask a question that requires current information and inspect the cited sources in the chat activity.

Web tools are enabled by default. Configure them only when you want a specific provider, API key, proxy, fetch behavior, or SSRF allowlist.

### Advanced JSON shapes (optional)

Default search provider:

```json
{
  "tools": {
    "web": {
      "enable": true,
      "search": {
        "provider": "duckduckgo"
      }
    }
  }
}
```

API-backed provider example:

```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "brave",
        "apiKey": "${BRAVE_API_KEY}"
      }
    }
  }
}
```

## Production notes

- Keep API keys in Settings or OS environment variables.
- Set max results when you need fewer or more hits per query.
- Set a web proxy only to a proxy you trust.
- Disable Jina reader in fetch settings if you need local page conversion only.

## Security notes

- Web fetch and HTTP MCP share an SSRF guard.
- Private, loopback, link-local, and cloud metadata addresses are blocked by default.
- Add SSRF whitelist entries only for narrow trusted CIDRs.
- Do not give public chat users unrestricted web and shell access without review.

## Troubleshooting

- Search returns no results: switch provider or check the provider API key in Settings.
- Fetch blocked: inspect the target URL and SSRF whitelist.
- Proxy changes network behavior: verify proxy and bypass settings for local addresses.

## Related docs

- [`secure-local-ai-agent.md`](./secure-local-ai-agent.md)
- [`../configuration.md`](../configuration.md)
