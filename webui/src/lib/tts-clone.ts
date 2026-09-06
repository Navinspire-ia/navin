/**
 * Mirrors `navin.audio.tts_clone.model_supports_reference`.
 *
 * Kept on the client so Settings and Translate & dub can label a model
 * even when an older gateway has not yet sent `supportsReference`.
 */

const REFERENCE_MARKERS = [
  "fish-audio",
  "fishaudio",
  "elevenlabs",
  "eleven_labs",
  "eleven-multilingual",
  "minimax",
  "playht",
  "play.ai",
  "playai-clone",
] as const;

const CATALOGUE_ONLY_PROVIDERS = new Set([
  "openai",
  "groq",
  "gemini",
  "google",
  "ollama",
  "vllm",
  "lm_studio",
  "lm-studio",
  "lmstudio",
]);

export function ttsModelSupportsReference(
  model: string | null | undefined,
  provider?: string | null,
): boolean {
  const slug = (model ?? "").trim().toLowerCase();
  const host = (provider ?? "").trim().toLowerCase();
  if (CATALOGUE_ONLY_PROVIDERS.has(host)) return false;
  return REFERENCE_MARKERS.some((marker) => slug.includes(marker));
}
