// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * The voices Navin's Live can speak with, and who they sound like. One source
 * for the Live voice list in Settings, the avatar (man / woman) and the
 * avatar switch, which picks a voice of the other body.
 */
export type VoiceGender = "F" | "M";

export type LiveVoiceOption = {
  id: string;
  gender: VoiceGender;
  /** Language the voice is made for; empty = multilingual. */
  lang: string;
};

const voice = (id: string, gender: VoiceGender, lang = ""): LiveVoiceOption => ({ id, gender, lang });

/** Amazon Nova 2 Sonic (navin/live/nova_sonic.py VOICES). */
export const NOVA_SONIC_VOICES: LiveVoiceOption[] = [
  voice("ambre", "F", "fr"), voice("florian", "M", "fr"),
  voice("tiffany", "F", "en"), voice("matthew", "M", "en"), voice("amy", "F", "en"), voice("olivia", "F", "en"),
  voice("kiara", "F", "en"), voice("arjun", "M", "en"),
  voice("beatrice", "F", "it"), voice("lorenzo", "M", "it"), voice("tina", "F", "de"), voice("lennart", "M", "de"),
  voice("lupe", "F", "es"), voice("carlos", "M", "es"), voice("carolina", "F", "pt"), voice("leo", "M", "pt"),
];

/** Google Gemini Live prebuilt voices (multilingual). */
export const GEMINI_LIVE_VOICES: LiveVoiceOption[] = [
  ["Kore", "F"], ["Aoede", "F"], ["Leda", "F"], ["Zephyr", "F"], ["Callirrhoe", "F"], ["Autonoe", "F"],
  ["Despina", "F"], ["Erinome", "F"], ["Laomedeia", "F"], ["Achernar", "F"], ["Gacrux", "F"],
  ["Pulcherrima", "F"], ["Vindemiatrix", "F"], ["Sulafat", "F"],
  ["Puck", "M"], ["Charon", "M"], ["Fenrir", "M"], ["Orus", "M"], ["Enceladus", "M"], ["Iapetus", "M"],
  ["Umbriel", "M"], ["Algieba", "M"], ["Algenib", "M"], ["Rasalgethi", "M"], ["Alnilam", "M"],
  ["Schedar", "M"], ["Achird", "M"], ["Zubenelgenubi", "M"], ["Sadachbia", "M"], ["Sadaltager", "M"],
].map(([id, gender]) => voice(id, gender as VoiceGender));

/** Amazon Polly (navin/audio/models.py): text-to-speech of the pipeline engine. */
const POLLY_VOICES: LiveVoiceOption[] = [
  voice("Lea", "F", "fr"), voice("Remi", "M", "fr"), voice("Gabrielle", "F", "fr"), voice("Liam", "M", "fr"),
  voice("Joanna", "F", "en"), voice("Matthew", "M", "en"), voice("Ruth", "F", "en"), voice("Stephen", "M", "en"),
  voice("Amy", "F", "en"), voice("Brian", "M", "en"), voice("Arthur", "M", "en"), voice("Danielle", "F", "en"),
  voice("Hala", "F", "ar"), voice("Zayd", "M", "ar"),
];

/** OpenAI (TTS and Realtime) and xAI Grok voices. */
const OTHER_VOICES: LiveVoiceOption[] = [
  ["marin", "F"], ["coral", "F"], ["nova", "F"], ["shimmer", "F"], ["sage", "F"], ["alloy", "F"],
  ["cedar", "M"], ["ash", "M"], ["ballad", "M"], ["echo", "M"], ["fable", "M"], ["onyx", "M"], ["verse", "M"],
  ["eve", "F"], ["ara", "F"], ["rex", "M"],
].map(([id, gender]) => voice(id, gender as VoiceGender));

const GENDERS = new Map<string, VoiceGender>(
  [...NOVA_SONIC_VOICES, ...GEMINI_LIVE_VOICES, ...POLLY_VOICES, ...OTHER_VOICES].map((item) => [item.id.toLowerCase(), item.gender]),
);

/** Who a voice sounds like; null for "auto", empty and unknown voices. */
export function voiceGender(id: string | null | undefined): VoiceGender | null {
  const key = (id ?? "").trim().toLowerCase().split(/[\s:/]/).pop() ?? "";
  return GENDERS.get(key) ?? null;
}

/** Voices of a realtime Live model ("provider::model"), or null when Navin does not list them. */
export function realtimeVoiceOptions(model: string | null | undefined): LiveVoiceOption[] | null {
  const id = (model ?? "").toLowerCase();
  if (id.includes("nova")) return NOVA_SONIC_VOICES;
  if (id.includes("gemini")) return GEMINI_LIVE_VOICES;
  return null;
}

function pick(options: LiveVoiceOption[], gender: VoiceGender, language: string): string | null {
  const lang = language.toLowerCase().slice(0, 2);
  const match = options.find((item) => item.gender === gender && item.lang === lang)
    ?? options.find((item) => item.gender === gender && (item.lang === "" || item.lang === "en"))
    ?? options.find((item) => item.gender === gender);
  return match?.id ?? null;
}

/** A realtime voice of the given body for this model, in the user's language. */
export function realtimeVoiceFor(gender: VoiceGender, model: string | null | undefined, language: string): string | null {
  const options = realtimeVoiceOptions(model);
  if (options) return pick(options, gender, language);
  return pick(OTHER_VOICES.filter((item) => ["marin", "cedar"].includes(item.id)), gender, language);
}

/** Keep Nova's automatic voice consistent with the default female avatar. */
export function realtimeSessionVoice(chosen: string | null | undefined, model: string | null | undefined, language: string): string | undefined {
  const explicit = chosen?.trim();
  if (explicit && explicit.toLowerCase() !== "auto") return explicit;
  if (model?.toLowerCase().includes("sonic")) return pick(NOVA_SONIC_VOICES, "F", language) ?? "tiffany";
  return undefined;
}

/** A text-to-speech voice of the given body for the pipeline engine (Polly, OpenAI, Gemini, Grok). */
export function speechVoiceFor(gender: VoiceGender, ttsModel: string | null | undefined, language: string): string | null {
  const id = (ttsModel ?? "").toLowerCase();
  if (id.includes("polly")) return pick(POLLY_VOICES, gender, language);
  if (id.includes("gemini")) return pick(GEMINI_LIVE_VOICES, gender, language);
  if (id.includes("grok")) return gender === "M" ? "rex" : "eve";
  return pick(OTHER_VOICES.filter((item) => ["coral", "onyx"].includes(item.id)), gender, language);
}
