import type { MediaModelKind, ProviderModelInfo } from "./types";

// Compatibility with older gateways that do not yet return media_modalities.
// Keep dedicated family fallbacks aligned with providers/media_models.py.
const FAMILIES: Record<MediaModelKind, RegExp> = {
  stt: /(?:^|[-_.])(?:asr|stt|whisper|transcribe|parakeet|sensevoice(?:small)?|nova-3|chirp-3)(?:[-_.]|$)/,
  tts: /(?:^|[-_.])(?:tts|kokoro|orpheus|csm|aura-2|mai-voice)(?:[-_.]|$)/,
  video: /(?:^|[-_.])(?:video|veo|sora|seedance|kling|hailuo|happyhorse|wan-[0-9]|gen-4|aleph)(?:[-_.0-9]|$)/,
  image: /(?:^|[-_.])(?:gpt-image|qwen-image|glm-image|mai-image|muse-image|grok-imagine-image|hunyuan-image|imagegen|imagen|dall-e|seedream|stable-diffusion|sdxl|sd3|recraft|riverflow|ideogram|kolors|cogview|flux)(?:[-_.0-9]|$)|^gemini-[\w.-]*(?:flash|pro)(?:-lite|-preview)?-image(?:[-_.]|$)/,
  music: /(?:^|[-_.])(?:lyria|musicgen|music|stable-audio|suno|udio|ace-step)(?:[-_.0-9]|$)/,
};

export function supportsMediaModel(row: Pick<ProviderModelInfo, "id" | "output_modalities" | "media_modalities">, kind: MediaModelKind): boolean {
  if (row.media_modalities) return row.media_modalities.includes(kind);
  const slug = row.id.toLowerCase().split("/").pop() ?? "";
  const outputs = row.output_modalities;
  if (outputs?.length) {
    if (kind === "tts") return outputs.includes("speech") || (outputs.includes("audio") && FAMILIES.tts.test(slug));
    if (kind === "stt") return outputs.includes("transcription");
    if (kind === "music") return outputs.includes("music") || (outputs.includes("audio") && FAMILIES.music.test(slug));
    return outputs.includes(kind);
  }
  if (/embed|rerank|caption|classif|moderation|segmentation|understanding/.test(slug)) return false;
  if (kind === "image" && FAMILIES.video.test(slug)) return false;
  return FAMILIES[kind].test(slug);
}

export function filterMediaModels(rows: readonly ProviderModelInfo[], kind: MediaModelKind): ProviderModelInfo[] {
  return rows.filter((row) => supportsMediaModel(row, kind));
}
