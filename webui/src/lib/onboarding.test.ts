// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { beforeEach, describe, expect, it, vi } from "vitest";

const store = new Map<string, string>();

vi.stubGlobal("window", {
  localStorage: {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    clear: () => {
      store.clear();
    },
  },
});

vi.mock("@/lib/api", () => ({
  fetchOnboardingState: vi.fn(async () => ({ completed: false })),
  updateOnboardingState: vi.fn(async (state: unknown) => state),
}));

import {
  DEFAULT_ONBOARDING,
  FREE_ONBOARDING_PATHS,
  markOnboardingComplete,
  needsFirstRunWizard,
  ONBOARDING_PATHS,
  ONBOARDING_STORAGE_KEY,
  readOnboardingState,
  writeOnboardingState,
} from "./onboarding";

describe("onboarding state", () => {
  beforeEach(() => {
    store.clear();
  });

  it("defaults to incomplete", () => {
    expect(readOnboardingState()).toEqual(DEFAULT_ONBOARDING);
    expect(needsFirstRunWizard()).toBe(true);
  });

  it("persists completion", () => {
    const next = markOnboardingComplete({ path: "byok", language: "fr" });
    expect(next.completed).toBe(true);
    expect(next.path).toBe("byok");
    expect(next.language).toBe("fr");
    expect(needsFirstRunWizard()).toBe(false);
    expect(JSON.parse(store.get(ONBOARDING_STORAGE_KEY) ?? "{}").completed).toBe(true);
  });

  it("tolerates corrupt storage", () => {
    store.set(ONBOARDING_STORAGE_KEY, "{not-json");
    expect(readOnboardingState()).toEqual(DEFAULT_ONBOARDING);
  });

  it("round-trips write/read", () => {
    writeOnboardingState({
      completed: false,
      path: "account",
      demoOpened: true,
    });
    expect(readOnboardingState()).toMatchObject({
      completed: false,
      path: "account",
      demoOpened: true,
    });
  });

  it("keeps the local free paths and drops unknown ones", () => {
    for (const path of ONBOARDING_PATHS) {
      store.set(ONBOARDING_STORAGE_KEY, JSON.stringify({ completed: true, path }));
      expect(readOnboardingState().path).toBe(path);
    }
    expect(ONBOARDING_PATHS).toContain("omniroute");
    expect(ONBOARDING_PATHS).toContain("ollama");
    expect(FREE_ONBOARDING_PATHS).toEqual(["free", "omniroute", "ollama"]);
    store.set(ONBOARDING_STORAGE_KEY, JSON.stringify({ completed: true, path: "lmstudio" }));
    expect(readOnboardingState().path).toBeUndefined();
  });
});
