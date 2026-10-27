// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";

import {
  loadLastModelPick,
  loadThreadEffort,
  loadThreadModelPreset,
  saveLastModelPick,
  saveThreadEffort,
  saveThreadModelPreset,
} from "./thread-model-preset";

describe("thread model + effort persistence", () => {
  beforeEach(() => window.localStorage.clear());

  it("restores model and effort of a reopened chat", () => {
    saveThreadModelPreset("chat-a", "opus");
    saveLastModelPick("opus");
    saveThreadEffort("chat-a", "opus", "high");
    // "Reopen": read back from storage only.
    expect(loadThreadModelPreset("chat-a")).toBe("opus");
    expect(loadThreadEffort("chat-a", "opus")).toBe("high");
    expect(loadLastModelPick()).toBe("opus");
  });

  it("keeps efforts per chat independent", () => {
    saveThreadEffort("chat-a", "opus", "high");
    saveThreadEffort("chat-b", "opus", "low");
    expect(loadThreadEffort("chat-a", "opus")).toBe("high");
    expect(loadThreadEffort("chat-b", "opus")).toBe("low");
  });

  it("keeps each model's effort within one chat", () => {
    saveThreadEffort("chat-a", "opus", "high");
    saveThreadEffort("chat-a", "gpt", "medium");
    expect(loadThreadEffort("chat-a", "opus")).toBe("high");
    expect(loadThreadEffort("chat-a", "gpt")).toBe("medium");
  });

  it("falls back to the last pick for a new chat, null when unknown", () => {
    saveThreadEffort("chat-a", "opus", "high");
    expect(loadThreadEffort("chat-new", "opus")).toBe("high");
    expect(loadThreadEffort(null, "opus")).toBe("high");
    expect(loadThreadEffort("chat-new", "other")).toBeNull();
  });

  it("keeps the explicit Auto (empty) effort", () => {
    saveThreadEffort("chat-a", "opus", "");
    expect(loadThreadEffort("chat-a", "opus")).toBe("");
  });

  it("survives corrupted storage", () => {
    window.localStorage.setItem("navin.thread-reasoning-efforts", "{bad");
    window.localStorage.setItem("navin.thread-reasoning-last-pick", "[1]");
    expect(loadThreadEffort("chat-a", "opus")).toBeNull();
    saveThreadEffort("chat-a", "opus", "low");
    expect(loadThreadEffort("chat-a", "opus")).toBe("low");
  });
});
