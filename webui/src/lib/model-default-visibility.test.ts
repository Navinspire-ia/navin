import { describe, expect, it } from "vitest";
import type { SettingsPayload } from "./types";
import { isDuplicateDefault } from "./model-default-visibility";

function fixture() {
  const defaults = { name: "default", is_default: true, model: "chat-model", provider: "custom",
    max_tokens: 8192, context_window_tokens: 200000, temperature: 0.7, reasoning_effort: null };
  const active = { ...defaults, name: "my-model", is_default: false, enabled: true };
  return { agent: { model_preset: active.name }, model_presets: [defaults, active] } as SettingsPayload;
}

describe("default model visibility", () => {
  it("shows the selected configuration once", () => {
    const settings = fixture();
    expect(settings.model_presets.filter((p) => !isDuplicateDefault(p, settings)))
      .toEqual([settings.model_presets[1]]);
  });

  it("keeps a fallback with different reasoning settings", () => {
    const settings = fixture();
    settings.model_presets[0].reasoning_effort = "high";
    expect(isDuplicateDefault(settings.model_presets[0], settings)).toBe(false);
  });

  it("keeps Default when it is selected", () => {
    const settings = fixture();
    settings.agent.model_preset = null;
    expect(isDuplicateDefault(settings.model_presets[0], settings)).toBe(false);
  });

  it("preserves managed Navin rows", () => {
    const settings = fixture();
    for (const row of settings.model_presets) row.provider = "navin";
    expect(isDuplicateDefault(settings.model_presets[0], settings)).toBe(false);
  });

  it("keeps the duplicate hidden after selecting another configuration", () => {
    const settings = fixture();
    settings.model_presets.push({ ...settings.model_presets[1], name: "second", model: "other-model" });
    settings.agent.model_preset = "second";
    expect(isDuplicateDefault(settings.model_presets[0], settings)).toBe(true);
  });
});
