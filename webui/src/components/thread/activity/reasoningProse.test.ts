// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { mergeReasoningParts, reasoningSummary } from "./reasoningProse";

describe("mergeReasoningParts", () => {
  it("joins non-empty steps with paragraph breaks", () => {
    expect(mergeReasoningParts(["  one  ", "", "two"])).toBe("one\n\ntwo");
  });
});

describe("reasoningSummary", () => {
  it("skips short scratch lines and keeps the latest substantial paragraph", () => {
    const text = [
      "I'll look at the handler.",
      "",
      "The Preview Ouvrir button treats 127.0.0.1 as the Navin app itself, so Windows opens another window.",
    ].join("\n");
    expect(reasoningSummary(text)).toContain("Preview Ouvrir");
    expect(reasoningSummary(text)).not.toContain("I'll look");
  });

  it("joins recent paragraphs so the resume is longer and readable", () => {
    const text = [
      "The Preview Ouvrir button treats 127.0.0.1 as the Navin app itself, so Windows opens another window.",
      "",
      "Preview Ouvrir is navigating the Navin window, not Edge. I will keep only Navin shell URLs in-app and send project loopback ports (Vite, Next, etc.) to the OS browser.",
    ].join("\n");
    const summary = reasoningSummary(text);
    expect(summary).toContain("127.0.0.1");
    expect(summary).toContain("OS browser");
    expect(summary.length).toBeGreaterThan(200);
  });

  it("strips markdown noise and keeps a longer readable resume", () => {
    const summary = reasoningSummary("## Root cause\n\n**localhost** is classified as in-app.");
    expect(summary).not.toContain("#");
    expect(summary).not.toContain("**");
    expect(summary).toContain("localhost is classified as in-app");
  });

  it("does not clip a clear paragraph under 480 characters", () => {
    const paragraph = "Preview Ouvrir is navigating the Navin window, not Edge. I will keep only Navin shell URLs in-app and send project loopback ports (Vite, Next, etc.) to the system browser so localhost previews open in Edge.";
    expect(reasoningSummary(paragraph)).toBe(paragraph);
    expect(reasoningSummary(paragraph).length).toBeGreaterThan(160);
  });
});
