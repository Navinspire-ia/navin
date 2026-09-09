// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { HTML_PREVIEW_SANDBOX } from "./DevHtmlPreview";

describe("HTML_PREVIEW_SANDBOX", () => {
  it("allows scripts so Three.js and UI handlers can run", () => {
    expect(HTML_PREVIEW_SANDBOX).toContain("allow-scripts");
    expect(HTML_PREVIEW_SANDBOX).toContain("allow-same-origin");
  });

  it("does not use an empty sandbox that blocks all scripts", () => {
    expect(HTML_PREVIEW_SANDBOX.trim()).not.toBe("");
  });
});
