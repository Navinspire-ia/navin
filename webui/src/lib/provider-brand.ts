export interface ProviderBrand {
  logoUrl: string;
  logoUrls: string[];
  color: string;
  initials: string;
}

function officialFaviconUrl(domain: string): string {
  return `https://${domain}/favicon.ico`;
}

function duckDuckGoFaviconUrl(domain: string): string {
  return `https://icons.duckduckgo.com/ip3/${encodeURIComponent(domain)}.ico`;
}

function googleFaviconUrl(domain: string): string {
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64`;
}

export function faviconUrls(domain: string): string[] {
  const faviconDomain = faviconDomainFromValue(domain);
  return [
    officialFaviconUrl(faviconDomain),
    duckDuckGoFaviconUrl(faviconDomain),
    googleFaviconUrl(domain),
  ];
}

function brand(
  domain: string,
  color: string,
  initials: string,
  logoOverrides: string[] = [],
): ProviderBrand {
  const logoUrls = [...logoOverrides];
  faviconUrls(domain).forEach((url) => addUniqueLogoUrl(logoUrls, url));
  return {
    logoUrl: logoUrls[0],
    logoUrls,
    color,
    initials,
  };
}

function addUniqueLogoUrl(urls: string[], url: string | null | undefined): void {
  const value = url?.trim();
  if (value && !urls.includes(value)) urls.push(value);
}

function domainFromLogoUrl(url: string): string | null {
  if (url.startsWith("/")) return null;
  try {
    const parsed = new URL(url);
    if (!/^https?:$/.test(parsed.protocol)) return null;
    const host = parsed.hostname.toLowerCase();
    if (host === "www.google.com" || host === "google.com") {
      return parsed.searchParams.get("domain");
    }
    if (host === "icons.duckduckgo.com") {
      const match = parsed.pathname.match(/^\/ip3\/(.+)\.ico$/);
      return match ? decodeURIComponent(match[1]) : null;
    }
    return host.replace(/^www\./, "");
  } catch {
    return null;
  }
}

function faviconDomainFromValue(value: string): string {
  const host = value.split("/")[0]?.trim();
  return host || value;
}

export function logoFallbackUrls(logoUrl: string | null | undefined): string[] {
  const value = logoUrl?.trim();
  if (!value) return [];
  if (value.startsWith("/")) return [value];

  const urls: string[] = [];
  const domain = domainFromLogoUrl(value);
  const isFaviconProxy = /^(https?:\/\/)?(www\.google\.com|google\.com|icons\.duckduckgo\.com)\//i.test(value);
  if (domain && isFaviconProxy) {
    addUniqueLogoUrl(urls, value);
    faviconUrls(domain).forEach((url) => addUniqueLogoUrl(urls, url));
    return urls;
  }
  addUniqueLogoUrl(urls, value);
  if (domain) faviconUrls(domain).forEach((url) => addUniqueLogoUrl(urls, url));
  return urls;
}

export const PROVIDER_BRAND_ALIASES: Record<string, string> = {
  brave_search: "brave",
  openai_codex: "openai",
};

export const PROVIDER_LABEL_ALIASES: Record<string, string> = {
  brave_search: "Brave Search",
  openai_codex: "OpenAI",
};

const PROVIDER_BRANDS: Record<string, ProviderBrand> = {
  anthropic: brand("anthropic.com", "#D97757", "A"),
  assemblyai: brand("assemblyai.com", "#111827", "AA"),
  atomic_chat: brand("atomic.chat", "#111827", "AC"),
  azure_openai: brand("azure.microsoft.com", "#0078D4", "AZ"),
  bedrock: brand("aws.amazon.com", "#FF9900", "AWS"),
  bocha: brand("bochaai.com", "#2563EB", "B"),
  brave: brand("brave.com", "#FB542B", "B"),
  custom_anthropic: brand("anthropic.com", "#D97757", "CA"),
  deepseek: brand("deepseek.com", "#4D6BFE", "DS"),
  hunyuan: brand("hunyuan.tencent.com", "#0052D9", "HY"),
  kimi_coding: brand("kimi.com", "#111827", "KC"),
  qianfan: brand("qianfan.cloud.baidu.com", "#2932E1", "QF"),
  qwen: brand("tongyi.aliyun.com", "#FF6A00", "QW"),
  stepfun: brand("stepfun.com", "#111827", "ST"),
  volcengine: brand("volcengine.com", "#1664FF", "DB"),
  duckduckgo: brand("duckduckgo.com", "#DE5833", "DDG"),
  exa: brand("exa.ai", "#5B5BF6", "E"),
  gemini: brand("gemini.google.com", "#4285F4", "G"),
  github_copilot: brand("github.com", "#24292F", "GH"),
  groq: brand("groq.com", "#F55036", "GQ"),
  huggingface: brand("huggingface.co", "#FF9D00", "HF"),
  jina: brand("jina.ai", "#7C3AED", "J"),
  kagi: brand("kagi.com", "#FFB319", "K"),
  keenable: brand("keenable.ai", "#0EA5E9", "K"),
  lm_studio: brand("lmstudio.ai", "#111827", "LM"),
  minimax: brand("minimax.io", "#111827", "MM"),
  mistral: brand("mistral.ai", "#FA520F", "M"),
  moonshot: brand("moonshot.ai", "#111827", "MS"),
  novita: brand("novita.ai", "#7C3AED", "N"),
  olostep: brand("olostep.com", "#111827", "O"),
  nvidia: brand("nvidia.com", "#76B900", "NV"),
  ollama: brand("ollama.com", "#111827", "O"),
  omniroute: brand("omniroute.online", "#16A34A", "OM"),
  openai: brand("openai.com", "#111827", "AI"),
  openrouter: brand("openrouter.ai", "#111827", "OR"),
  navin: brand("navin.live", "#111827", "N"),
  ovms: brand("openvino.ai", "#0071C5", "OV"),
  searxng: brand("searxng.org", "#3050FF", "SX"),
  siliconflow: brand("siliconflow.cn", "#111827", "SF"),
  tavily: brand("tavily.com", "#111827", "T"),
  vllm: brand("vllm.ai", "#2563EB", "VL"),
  // The same GLM models on two platforms. They share a logo, so the initials
  // are what tells them apart in the settings list.
  zai: brand("z.ai", "#155EEF", "Z", [
    "https://z-cdn.chatglm.cn/z-ai/static/logo.svg",
    "https://www.google.com/s2/favicons?domain=z.ai&sz=64",
  ]),
  zhipu: brand("bigmodel.cn", "#155EEF", "ZP", [
    "https://z-cdn.chatglm.cn/z-ai/static/logo.svg",
    "https://www.google.com/s2/favicons?domain=bigmodel.cn&sz=64",
  ]),
};

export function providerBrand(provider: string | null | undefined): ProviderBrand | null {
  if (!provider) return null;
  const key = PROVIDER_BRAND_ALIASES[provider] ?? provider;
  return PROVIDER_BRANDS[key] ?? null;
}

export function providerDisplayLabel(
  providers: Array<{ name: string; label: string }>,
  value: string | null | undefined,
): string {
  if (!value) return "";
  return providers.find((provider) => provider.name === value)?.label
    ?? PROVIDER_LABEL_ALIASES[value]
    ?? value;
}

export function inferProviderFromModelName(modelName: string | null | undefined): string | null {
  const normalized = (modelName ?? "").trim().toLowerCase();
  if (!normalized) return null;
  const prefix = normalized.split(/[/:]/)[0];
  if (providerBrand(prefix)) return prefix;
  if (/claude|anthropic/.test(normalized)) return "anthropic";
  if (/gpt-|^o\d|chatgpt|openai/.test(normalized)) return "openai";
  if (/deepseek/.test(normalized)) return "deepseek";
  if (/gemini/.test(normalized)) return "gemini";
  if (/qwen|dashscope/.test(normalized)) return "qwen";
  if (/doubao|volcengine|ark/.test(normalized)) return "volcengine";
  if (/hunyuan/.test(normalized)) return "hunyuan";
  if (/ernie|qianfan/.test(normalized)) return "qianfan";
  if (/step-/.test(normalized)) return "stepfun";
  if (/kimi|moonshot/.test(normalized)) return "moonshot";
  if (/minimax/.test(normalized)) return "minimax";
  if (/glm/.test(normalized)) return "zai";
  if (/mistral|mixtral/.test(normalized)) return "mistral";
  return null;
}
