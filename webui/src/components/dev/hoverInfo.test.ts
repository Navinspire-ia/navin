// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { isUsefulHoverText } from "./hoverInfo";

describe("isUsefulHoverText", () => {
  it("rejects an echo of the identifier", () => {
    expect(isUsefulHoverText("requests", "requests")).toBe(false);
    expect(isUsefulHoverText("  requests  ", "requests")).toBe(false);
    expect(isUsefulHoverText("", "requests")).toBe(false);
  });

  it("rejects a markdown-wrapped echo from a language server", () => {
    expect(isUsefulHoverText("`requests`", "requests")).toBe(false);
    expect(isUsefulHoverText("```yaml\nrequests\n```", "requests")).toBe(false);
    expect(isUsefulHoverText("**requests**", "requests")).toBe(false);
  });

  it("rejects an echo of the source line", () => {
    expect(isUsefulHoverText("        requests:", "requests", "        requests:")).toBe(
      false,
    );
    expect(isUsefulHoverText("requests", "requests", "        requests:")).toBe(false);
  });

  it("keeps a hover that adds a kind, path or signature", () => {
    expect(isUsefulHoverText("AuthService (class)\npkg/auth.py:1", "AuthService")).toBe(
      true,
    );
    expect(
      isUsefulHoverText("cpu: string\nRequested CPU for the container.", "cpu"),
    ).toBe(true);
  });
});
