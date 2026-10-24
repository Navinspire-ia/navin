// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { presetProviderReady } from "./model-default-visibility";

const providers = [
  { name: "openai", configured: true },
  { name: "anthropic", configured: false },
] as never;

describe("presets offered in the model picker", () => {
  it("lists the models of configured providers only", () => {
    expect(presetProviderReady({ provider: "openai" }, providers)).toBe(true);
    expect(presetProviderReady({ provider: "anthropic" }, providers)).toBe(false);
    expect(presetProviderReady({ provider: "mistral" }, providers)).toBe(false);
  });

  it("keeps automatic routing and the Navin plan", () => {
    expect(presetProviderReady({ provider: "auto" }, providers)).toBe(true);
    expect(presetProviderReady({ provider: "" }, providers)).toBe(true);
    expect(presetProviderReady({ provider: "navin" }, providers)).toBe(true);
  });
});
