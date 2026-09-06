/**
 * What Task routing (Settings > Models > Task routing) will do to a chat that
 * has no pinned model: which task roles leave the default model for another
 * one. The composer uses this to show an explicit "Auto" state and to explain
 * a swap the moment it happens, instead of letting the user discover a
 * different model name in the trace line.
 */

import type { TFunction } from "i18next";

import { taskRoleShortLabel } from "@/lib/task-route-label";
import type { SettingsPayload } from "@/lib/types";

/** Sent as ``model_preset`` to hand the choice back to Task routing. */
export const AUTO_MODEL_PRESET = "auto";

export interface RouteSwap {
  role: string;
  roleLabel: string;
  presetName: string;
  modelLabel: string;
}

type PresetRow = SettingsPayload["model_presets"][number];

function presetByName(settings: SettingsPayload, name: string): PresetRow | null {
  return settings.model_presets.find((row) => row.name === name) ?? null;
}

/**
 * Routes that actually change the model. A route pointing at the default
 * preset, at an unknown / disabled preset, or at the same underlying model as
 * the default is not a swap the user needs to know about.
 */
export function routeSwaps(settings: SettingsPayload | null, t: TFunction): RouteSwap[] {
  if (!settings) return [];
  const routes = settings.model_routes ?? {};
  const defaultName = settings.agent.model_preset || "default";
  const defaultRow = presetByName(settings, defaultName);
  const defaultModel = defaultRow?.model ?? settings.agent.model ?? "";
  const swaps: RouteSwap[] = [];
  for (const [role, presetName] of Object.entries(routes)) {
    if (typeof presetName !== "string" || !presetName.trim()) continue;
    const name = presetName.trim();
    if (name === "default" || name === defaultName) continue;
    const row = presetByName(settings, name);
    // Tier aliases (routing_only) are hidden from pickers but still route.
    if (!row || (row.enabled === false && !row.routing_only)) continue;
    if ((row.modality ?? "text") !== "text") continue;
    if (row.model && defaultModel && row.model === defaultModel) continue;
    swaps.push({
      role,
      roleLabel: taskRoleShortLabel(role, t),
      presetName: name,
      modelLabel: row.label || row.model || name,
    });
  }
  swaps.sort((a, b) => a.roleLabel.localeCompare(b.roleLabel));
  return swaps;
}

/** "Codage -> Nemotron 3 Ultra · Plan -> Grok 4.6", capped for a tooltip. */
export function routeSwapsSummary(swaps: RouteSwap[], max = 4): string {
  const shown = swaps.slice(0, max).map((swap) => `${swap.roleLabel} -> ${swap.modelLabel}`);
  const rest = swaps.length - shown.length;
  return rest > 0 ? `${shown.join(" · ")} · +${rest}` : shown.join(" · ");
}

export function isAutoModelPreset(value: string | null | undefined): boolean {
  return (value ?? "").trim().toLowerCase() === AUTO_MODEL_PRESET;
}
