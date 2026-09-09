// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  AUTO_MODEL_PRESET,
  isAutoModelPreset,
  routeSwaps,
  routeSwapsSummary,
} from "@/lib/model-routing-summary";
import type { SettingsPayload } from "@/lib/types";

const t = ((key: string, options?: { defaultValue?: string }) =>
  options?.defaultValue ?? key) as typeof import("i18next").t;

function settings(overrides: Partial<SettingsPayload> = {}): SettingsPayload {
  return {
    agent: { model: "z-ai/glm-4.7-flash", model_preset: "glm" },
    model_presets: [
      { name: "glm", label: "GLM 4.7 Flash", model: "z-ai/glm-4.7-flash" },
      { name: "nemotron", label: "Nemotron 3 Ultra", model: "nvidia/nemotron-3-ultra" },
      { name: "grok", label: "Grok 4.6", model: "x-ai/grok-4.6" },
      { name: "glm-twin", label: "GLM again", model: "z-ai/glm-4.7-flash" },
      { name: "hidden", label: "Hidden", model: "foo/bar", enabled: false },
      { name: "tier-fast", label: "Fast tier", model: "deepseek/v4-flash", enabled: false, routing_only: true },
      { name: "veo", label: "Veo", model: "google/veo-3", modality: "video" },
    ],
    model_routes: {
      dev: "nemotron",
      plan: "grok",
      fast: "glm",
      docs: "default",
      review: "glm-twin",
      security: "hidden",
      search: "tier-fast",
      deep: "veo",
      vision: "   ",
    },
    ...overrides,
  } as unknown as SettingsPayload;
}

describe("model-routing-summary", () => {
  it("lists only the routes that really change the chat model", () => {
    const swaps = routeSwaps(settings(), t);
    expect(swaps.map((swap) => swap.role).sort()).toEqual(["dev", "plan", "search"]);
    const dev = swaps.find((swap) => swap.role === "dev");
    expect(dev).toMatchObject({ roleLabel: "Coding", modelLabel: "Nemotron 3 Ultra", presetName: "nemotron" });
  });

  it("is empty without settings or without routes", () => {
    expect(routeSwaps(null, t)).toEqual([]);
    expect(routeSwaps(settings({ model_routes: {} }), t)).toEqual([]);
  });

  it("summarises with an arrow per route and a +N tail", () => {
    const swaps = routeSwaps(settings(), t);
    expect(routeSwapsSummary(swaps)).toBe(
      "Coding -> Nemotron 3 Ultra · Planning -> Grok 4.6 · Search -> Fast tier",
    );
    expect(routeSwapsSummary(swaps, 1)).toBe("Coding -> Nemotron 3 Ultra · +2");
  });

  it("recognises the auto sentinel", () => {
    expect(isAutoModelPreset(AUTO_MODEL_PRESET)).toBe(true);
    expect(isAutoModelPreset(" Auto ")).toBe(true);
    expect(isAutoModelPreset("glm")).toBe(false);
    expect(isAutoModelPreset(null)).toBe(false);
  });
});
