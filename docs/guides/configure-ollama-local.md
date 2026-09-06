# How to Configure Ollama Local Models in Navin

Use Ollama when you want models to run on this machine with no cloud API key. Navin talks to Ollama through its local OpenAI-compatible endpoint (typically `http://localhost:11434/v1`).

## What you will build

- Ollama installed and running on your machine
- at least one pulled model (for example `llama3.2`)
- Navin pointed at Ollama under **Settings → Providers**
- a model configuration pinned to the Ollama provider

## Recommended path: Settings → Providers → Ollama

Open **Settings → Providers → Ollama**. The Local Ollama setup panel runs this flow:

1. **Detect** - checks whether Ollama is installed, whether the local server answers, which models are already pulled, and whether Navin already has a base URL configured.
2. **Install** - when Ollama is missing, offers a platform installer or a link to [ollama.com/download](https://ollama.com/download). Install the Ollama desktop app from that site if the in-app installer is unavailable.
3. **Start** - starts the local server if it is installed but not answering.
4. **Pull** - downloads a recommended model (`llama3.2`, `qwen2.5-coder:7b`, `mistral`, `gemma3:4b`, `nomic-embed-text`) or any model id you type.
5. **Configure / Use** - writes the Ollama base URL when missing, creates a model configuration for the chosen model, and can make that configuration active. A custom base URL you already set (LAN host, non-default port) is preserved.

After **Use**, open **Settings → Models** if you want to review the active configuration, then send a chat message.

## Recommended models

| Model id | Role | Approx. size |
|---|---|---|
| `llama3.2` | General chat and light coding | ~2 GB |
| `qwen2.5-coder:7b` | Local coding | ~4.7 GB |
| `mistral` | Fast general-purpose | ~4.1 GB |
| `gemma3:4b` | Small everyday tasks | ~3.3 GB |
| `nomic-embed-text` | Free local embeddings / semantic search | ~274 MB |

Pull only what your GPU or RAM can hold. Start with `llama3.2` if unsure.

## Provider resolution notes

- Pin the Ollama provider in the model configuration. Generic names such as `llama3.2` do not always auto-route without a configured local base URL.
- An empty Ollama block is not treated as configured. Use the Settings panel (or set the base URL) and pin the provider.
- NVIDIA cloud `nemotron` models route to NVIDIA NIM when that key is present. Local Nemotron weights on Ollama use the Ollama provider.

## Embeddings / semantic search

In Settings (or advanced tools config), point semantic search at Ollama with model `nomic-embed-text` after pulling that model from the Ollama panel.

## Image generation

Ollama image models use a native API surface; chat still uses the `/v1` base. When the chat base URL ends with `/v1`, image generation rewrites it automatically. See [Image generation](../image-generation.md).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Connection refused | Ollama not running, or wrong host/port in the base URL |
| Model not found | Model not pulled; use the panel **Pull** button |
| Very slow replies | Model too large for the machine; try a smaller tag |
| Install button unavailable | Download Ollama from ollama.com and reopen the panel |
| Navin still on a cloud model | Active configuration is not the Ollama one; open **Settings → Models** |

## Related docs

- [Providers and models](../providers.md#ollama)
- [Provider cookbook: Ollama](../provider-cookbook.md#recipe-ollama-local-model)
- [OpenAI-compatible provider](./configure-openai-compatible-provider.md)
