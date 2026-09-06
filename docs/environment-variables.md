# Environment Variables Reference

Navin reads API keys and behavior overrides from the process environment.
Exporting a variable is enough - no config edit, no import step:

```bash
# Linux / macOS / WSL
export KIMI_API_KEY="sk-..."
navin .

# PowerShell
$env:KIMI_API_KEY = "sk-..."
navin .
```

**Precedence** - for each provider, the first source that carries a value wins:

1. the key stored in Settings > Providers (`config.json`);
2. the provider's primary environment variable;
3. its aliases (the names other tools use - Claude Code, OpenCode, Hermes…),
   in the order listed below.

Keys found in the environment are used for the request only and are **never
written** to `config.json`.

## LLM Provider API Keys

The table derives from the provider registry
(`navin/providers/registry.py`) - a test keeps it in sync.

| Provider | Variable | Aliases also honored |
|----------|----------|----------------------|
| OpenRouter | `OPENROUTER_API_KEY` | - |
| OpenCode Zen | `OPENCODE_API_KEY` | `OPENCODE_ZEN_API_KEY` |
| OpenCode Go | `OPENCODE_API_KEY` | `OPENCODE_GO_API_KEY` |
| Hugging Face | `HF_TOKEN` | `HUGGINGFACE_TOKEN`, `HUGGING_FACE_HUB_TOKEN` |
| SiliconFlow | `OPENAI_API_KEY` | - |
| Novita AI | `NOVITA_API_KEY` | - |
| Anthropic | `ANTHROPIC_API_KEY` | - |
| OpenAI | `OPENAI_API_KEY` | - |
| AWS Bedrock | `AWS_BEARER_TOKEN_BEDROCK` | - (also honors the standard AWS credential chain: `AWS_PROFILE`, `AWS_REGION`, `~/.aws/credentials`) |
| DeepSeek | `DEEPSEEK_API_KEY` | - |
| Gemini | `GEMINI_API_KEY` | `GOOGLE_API_KEY` |
| Z.AI (GLM) | `ZAI_API_KEY` | `GLM_API_KEY`, `Z_AI_API_KEY` |
| Zhipu AI | `ZHIPUAI_API_KEY` | `ZHIPU_API_KEY` |
| Moonshot (Kimi) | `MOONSHOT_API_KEY` | `KIMI_API_KEY` |
| Kimi Coding | `KIMI_CODING_API_KEY` | - |
| Qwen (Alibaba) | `DASHSCOPE_API_KEY` | `QWEN_API_KEY`, `ALIBABA_API_KEY` |
| DashScope | `DASHSCOPE_API_KEY` | - (same credential as Qwen, international endpoint) |
| Doubao (Volcengine Ark) | `VOLCENGINE_API_KEY` | `ARK_API_KEY`, `BYTEPLUS_API_KEY` |
| Tencent Hunyuan | `HUNYUAN_API_KEY` | `TENCENT_HUNYUAN_API_KEY` |
| Baidu Qianfan | `QIANFAN_API_KEY` | `ERNIE_API_KEY`, `BAIDU_API_KEY` |
| StepFun | `STEPFUN_API_KEY` | - |
| SiliconFlow | `SILICONFLOW_API_KEY` | `SF_API_KEY` |
| MiniMax | `MINIMAX_API_KEY` | - |
| Mistral | `MISTRAL_API_KEY` | - |
| vLLM | `HOSTED_VLLM_API_KEY` | `VLLM_API_KEY` |
| Ollama | `OLLAMA_API_KEY` | - (local: key optional) |
| LM Studio | `LM_STUDIO_API_KEY` | `LM_API_KEY`, `LMSTUDIO_API_KEY` |
| Atomic Chat | `ATOMIC_CHAT_API_KEY` | - (local: key optional) |
| OmniRoute | `OMNIROUTE_API_KEY` | - (local gateway: key only when `REQUIRE_API_KEY` is on) |
| NVIDIA NIM | `NVIDIA_NIM_API_KEY` | `NVIDIA_API_KEY` |
| xAI | `XAI_API_KEY` | - |
| Groq | `GROQ_API_KEY` | - |
| AssemblyAI | `ASSEMBLYAI_API_KEY` | - (transcription only) |

OAuth providers (OpenAI Codex, GitHub Copilot, Grok x.ai subscription) and Azure OpenAI / OpenVINO /
Custom endpoints don't use these variables - they authenticate through their
own flow or through Settings.

## Project Configuration Discovery (no variables needed)

Skills, subagents and instruction files carried by the repository are read
natively on every request - nothing to export or import:

- **Skills**: `skills/`, plus `<harness>/skills/` for every harness folder
  (`.navin`, `.agents`, `.claude`, `.cursor`, `.opencode`, `.codex`, `.omp`,
  `.ai`). Symlinked skill directories (mirror layouts like `.agents/skills/`)
  are followed.
- **Subagents**: `<harness>/agents/` and OpenCode's singular `<harness>/agent/`,
  scanned recursively (category layouts like `.ai/agents/<category>/<name>.md`
  work as-is). Markdown-with-frontmatter and Codex TOML
  (`name` / `description` / `developer_instructions`) are both parsed.
- **Instructions**: `AGENTS.md` (plus `SOUL.md`, `USER.md`); `CLAUDE.md` is
  honored when no `AGENTS.md` exists, and Claude Code's `@path` import lines
  are resolved in both.
- A skill's own `requires.env` frontmatter is checked against the process
  environment, so skills that need e.g. `NOTION_API_KEY` report exactly which
  variable is missing.

## Navin Runtime Overrides (`NAVIN_*`)

Optional knobs; most users never set any of these.

| Variable | Description |
|----------|-------------|
| `NAVIN_LLM_TIMEOUT_S` | LLM API call timeout in seconds. |
| `NAVIN_OPENAI_COMPAT_TIMEOUT_S` | Timeout override for OpenAI-compatible providers only. |
| `NAVIN_STREAM_IDLE_TIMEOUT_S` | Kill a streaming response when no chunk arrives within this window. |
| `NAVIN_MAX_CONCURRENT_REQUESTS` | Cap on concurrent model requests (default `200`, aligned with `max_concurrent_subagents`). `<=0` = unlimited. |
| `NAVIN_MODEL_CATALOG_URL` | Override the managed model-catalog endpoint (default: navin.live). |
| `NAVIN_LICENSE_SERVER_URL` | **Dev-only.** License endpoint for testing against a local site checkout. Must be HTTPS (plain HTTP is only accepted toward localhost); anything else is ignored and the official server is used. Spoofing it gains nothing: paid plans are enforced server-side through the provisioned, spend-capped API key. |
| `NAVIN_UPDATE_BASE_URL` | Override the desktop auto-update endpoint. Safe by construction: every update must still pass the bundled Ed25519 signature, size, SHA-256 and HTTPS checks. |
| `NAVIN_UPDATE_PUBLIC_KEY` | **Dev-only, ignored in packaged builds.** Replaces the update-signature key when testing the pipeline from a source checkout. |
| `NAVIN_UPDATE_ALLOW_HTTP` | **Dev-only, ignored in packaged builds.** Allows a localhost plain-HTTP update endpoint. |
| `NAVIN_DOCS_BASE_URL` | Override the documentation base URL. |
| `NAVIN_EXTENSION_REGISTRY_URL` / `NAVIN_EXTENSION_RAW_BASE` | Override the extension registry endpoints. |
| `NAVIN_PATH_PREPEND` / `NAVIN_PATH_APPEND` | Extra `PATH` entries injected into tool subprocesses. |
| `NAVIN_TMUX_SOCKET_DIR` | Directory for the tmux sockets backing persistent shells. |
| `NAVIN_SANDBOX_BIN` / `NAVIN_SANDBOX_ENFORCED` | Sandbox binary override / require sandboxing for shell tools. |
| `NAVIN_WORKSPACE_SANDBOX_PROVIDER` / `NAVIN_WORKSPACE_SANDBOX_ENFORCED` | Workspace sandbox backend selection / enforcement. |
| `NAVIN_CHROMIUM` / `NAVIN_CHROME_PATH` | Browser binary used by the browser tools. |
| `NAVIN_DISABLE_NATIVE` | Disable native accelerated modules (troubleshooting). |
| `NAVIN_DIR` | Override the Navin state directory (default: `~/.navin`). |
| `NAVIN_DESKTOP_CONFIG` / `NAVIN_DESKTOP_BIN` / `NAVIN_DESKTOP_ZOOM` | Desktop app: config path, sidecar binary, UI zoom. |
| `NAVIN_WEBUI_TAB` | Open the WebUI on a specific tab at startup. |
| `NAVIN_PRESENTATION_TEMPLATES_DIR` | Extra directory of presentation templates. |
| `NAVIN_INSTALL_KIND` | Set by installers to tag the install type (telemetry-free display only). |
| `NAVIN_SKIP_WEBUI_BUILD` | Skip the WebUI build step in dev tooling. |
| `NAVIN_BUNDLED_EXTRAS` | Set by packaged builds to locate bundled extras. |
| `NAVIN_COPILOT_BASE_URL` / `NAVIN_COPILOT_TOKEN_URL` / `NAVIN_GITHUB_*` | GitHub Copilot endpoint overrides (enterprise proxies). |
| `NAVIN_RESTART_*` | Internal handoff variables written by the self-restart flow - never set manually. |
