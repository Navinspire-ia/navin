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

/**
 * A preset is offered only when its provider can run it: a configured provider,
 * the Navin plan (the gateway only lists it with a live account) or automatic
 * routing. The active row stays listed so the badge never loses its model.
 */
export function presetProviderReady(
  preset: Pick<Preset, "provider">,
  providers: SettingsPayload["providers"],
): boolean {
  const name = (preset.provider ?? "").trim().toLowerCase();
  if (!name || name === "auto" || name === "navin") return true;
  return providers.some((row) => row.name === name && row.configured);
}
