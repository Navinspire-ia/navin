import type { SettingsPayload } from "./types";

type Preset = SettingsPayload["model_presets"][number];

/** Keep the fallback editable when it differs from the active configuration. */
export function isDuplicateDefault(preset: Preset, settings: SettingsPayload): boolean {
  if (!preset.is_default) return false;
  const active = settings.model_presets.find(
    (row) => row.name === settings.agent.model_preset && !row.is_default,
  );
  if (!active || active.enabled === false || active.routing_only) return false;
  if (active.provider === "navin" || preset.provider === "navin") return false;
  if (!preset.model.trim()) return true;
  return settings.model_presets.some((row) => !row.is_default && row.enabled !== false
    && !row.routing_only && row.provider !== "navin"
    && (["model", "provider", "max_tokens", "context_window_tokens", "temperature",
      "reasoning_effort"] as const).every((key) => preset[key] === row[key]));
}
