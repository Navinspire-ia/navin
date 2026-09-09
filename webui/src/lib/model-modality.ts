import { supportsMediaModel } from "./media-models";
import type { MediaModelKind } from "./types";

/** Chat-defaultable modalities only (media presets are specialty tools). */
export type ModelModality = "text" | "image" | "video" | "audio" | "music" | "stt";

export type MediaModality = Exclude<ModelModality, "text">;

/** Global chat default (everything except Montage). Every subscription. */
export const DEFAULT_CHAT_MODEL = "z-ai/glm-5.3-flash";
export const DEFAULT_CHAT_PRESET = "glm-5-3-flash";

/** Multimodal chat default for Montage only (OpenRouter). Plus+ vision brain. */
export const MONTAGE_DEFAULT_MODEL = "x-ai/grok-4.6";
export const MONTAGE_DEFAULT_PRESET = "grok-4-6";

export function isMediaModality(
  modality: string | null | undefined,
): modality is MediaModality {
  return (
    modality === "image"
    || modality === "video"
    || modality === "audio"
    || modality === "music"
    || modality === "stt"
  );
}

/** Heuristic when a stale preset lost its modality field (e.g. Lyria as "text"). */
export function isMediaModelSlug(slug: string | null | undefined): boolean {
  const cleaned = (slug || "").trim().toLowerCase();
  if (!cleaned) return false;
  if ((["stt", "tts", "image", "video", "music"] as MediaModelKind[])
    .some((kind) => supportsMediaModel({ id: cleaned }, kind))) return true;
  const leaf = cleaned.includes("/") ? cleaned.split("/").pop() ?? cleaned : cleaned;
  return (
    leaf.includes("lyria")
    || leaf.includes("veo-")
    || leaf.includes("seedream")
    || leaf.includes("seedance")
    || leaf.includes("kling")
    || leaf.includes("flash-image")
    || leaf.includes("pro-image")
    || leaf.includes("flash-tts")
    || leaf.includes("grok-voice")
    || leaf.includes("grok-stt")
    || leaf.includes("transcribe")
    || leaf.includes("parakeet")
    || leaf.includes("nova-3")
    || leaf.includes("asr-")
  );
}

/** Families that read image input. Mirrors navin/providers/model_capabilities.py.
 *
 * Matching a family rather than an exact slug matters: vendors ship a new
 * dated or sized variant every few weeks, and each one would otherwise be
 * mislabelled text-only until someone edited this list. */
const VISION_FAMILY_MARKERS: readonly string[] = [
  "gpt-4o",
  "gpt-4.1",
  "gpt-4-turbo",
  "gpt-4-vision",
  "gpt-5",
  "o3",
  "o4-mini",
  "chatgpt-4o",
  "claude-3",
  "claude-4",
  "claude-5",
  "claude-sonnet",
  "claude-opus",
  "claude-haiku",
  "gemini",
  "gemma-3",
  "grok-2-vision",
  "grok-3",
  "grok-4",
  "grok-vision",
  "llama-3.2-11b",
  "llama-3.2-90b",
  "llama-4",
  "qwen-vl",
  "qwen2-vl",
  "qwen2.5-vl",
  "qwen3-vl",
  "qwen3.8-max",
  "qwen3.8-flash",
  "qwen3.8-27b",
  "qwen3.7-plus",
  "qwen3.6-plus",
  "qwen3.5-plus",
  "qvq",
  "pixtral",
  "mistral-medium-3",
  "mistral-small-3.1",
  "mistral-small-3.2",
  "internvl",
  "minicpm-v",
  "moondream",
  "llava",
  "cogvlm",
  "glm-4v",
  "glm-4.1v",
  "glm-4.5v",
  "yi-vision",
  "phi-3-vision",
  "phi-3.5-vision",
  "phi-4-multimodal",
  "molmo",
  "aya-vision",
  "nemotron-nano-vl",
  "step-1v",
  "ernie-4.5-vl",
  "kimi-vl",
  "dots.vlm",
  "-vl-",
  "-vl:",
  "vision",
  "omni",
  "multimodal",
  "mimo-v2.5",
];

/** Names that contain a vision marker but cannot read images. */
const TEXT_ONLY_OVERRIDES: readonly string[] = [
  "whisper",
  "tts",
  "text-embedding",
  "embed",
  "rerank",
  "moderation",
  "voice",
  "-stt",
  "transcribe",
  "parakeet",
];

/**
 * Chat models that understand image / video input (not generators).
 * Still modality=text so they can be the chat default.
 *
 * Pass `declared` when the provider catalog reported the capability: an
 * explicit answer always beats the name heuristic.
 */
export function isVisionChatModel(
  slug: string | null | undefined,
  declared?: boolean | null,
): boolean {
  if (typeof declared === "boolean") return declared;
  const cleaned = (slug || "").trim().toLowerCase();
  if (!cleaned) return false;
  const base = cleaned.split(":", 1)[0] ?? cleaned;
  if (TEXT_ONLY_OVERRIDES.some((marker) => base.includes(marker))) return false;
  return VISION_FAMILY_MARKERS.some((marker) => base.includes(marker));
}

export function visionBadgeClass(): string {
  return "bg-cyan-500/15 text-cyan-400 ring-cyan-500/30";
}

export function visionBadgeLabel(locale: "fr" | "en" = "fr"): string {
  return locale === "en" ? "vision" : "vision";
}

export type SettingsModelSection = "text" | "image" | "video" | "audio";

export const SETTINGS_MODEL_SECTIONS: readonly SettingsModelSection[] = [
  "text",
  "image",
  "video",
  "audio",
];

/** Settings list buckets. Chat text + multimodal first, then image, video, audio. */
export function settingsModelSection(modality: string | null | undefined): SettingsModelSection {
  const kind = modality ?? "text";
  if (kind === "image") return "image";
  if (kind === "video") return "video";
  if (kind === "audio" || kind === "music" || kind === "stt") return "audio";
  if (isMediaModality(kind)) return "audio";
  return "text";
}

export function canBeChatDefault(
  modality: string | null | undefined,
  modelSlug?: string | null,
): boolean {
  if (isMediaModality(modality)) return false;
  if (modelSlug && isMediaModelSlug(modelSlug)) return false;
  return true;
}

/** Image / video / audio stay in Settings, never in the chat picker. */
export const PICKER_COLLAPSED_MEDIA = new Set(["image", "video", "audio", "music", "stt"]);

export type PickerVisibilityOption = {
  name: string;
  label?: string;
  model?: string;
  provider?: string | null;
  active?: boolean;
  isDefault?: boolean;
  modality?: string | null;
};

/** Hide is allowed for media even when that specialty slot is current. */
export function canHidePickerOption(option: PickerVisibilityOption): boolean {
  if (!option.name || option.name === "default" || option.isDefault) return false;
  if (isMediaModality(option.modality)) return true;
  return !option.active;
}

function matchesPickerQuery(option: PickerVisibilityOption, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    (option.label ?? "").toLowerCase().includes(q)
    || (option.model ?? "").toLowerCase().includes(q)
    || option.name.toLowerCase().includes(q)
    || (option.provider ?? "").toLowerCase().includes(q)
  );
}

/**
 * Chat picker rows: text and vision chat models only.
 * Image / video / audio / music stay in Settings.
 */
export function visiblePickerOptions<T extends PickerVisibilityOption>(
  options: T[],
  query: string,
): T[] {
  return options.filter((option) => {
    if (!canBeChatDefault(option.modality, option.model)) return false;
    return matchesPickerQuery(option, query);
  });
}

/** Tiny colored chip for media preset rows. */
export function modalityBadgeClass(modality: MediaModality): string {
  if (modality === "image") {
    return "bg-sky-500/15 text-sky-400 ring-sky-500/30";
  }
  if (modality === "video") {
    return "bg-violet-500/15 text-violet-400 ring-violet-500/30";
  }
  if (modality === "music") {
    return "bg-emerald-500/15 text-emerald-400 ring-emerald-500/30";
  }
  if (modality === "stt") {
    return "bg-rose-500/15 text-rose-400 ring-rose-500/30";
  }
  return "bg-amber-500/15 text-amber-400 ring-amber-500/30";
}

export function modalityBadgeLabel(
  modality: MediaModality,
  locale: "fr" | "en" = "fr",
): string {
  if (modality === "image") return "image";
  if (modality === "video") return locale === "en" ? "video" : "vidéo";
  if (modality === "music") return locale === "en" ? "music" : "musique";
  if (modality === "stt") return locale === "en" ? "voice input" : "dictée";
  return "audio";
}
