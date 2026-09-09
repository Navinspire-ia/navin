// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("Montage timeline Fluent dropdowns", () => {
  it("uses a full inverted palette so the selected option stays readable", () => {
    const source = readFileSync(resolve(__dirname, "./MontageTimeline.tsx"), "utf8");
    // Fluent paints selected / hovered items with menuItemTextHovered = neutralDark.
    // The default #201f1e on navy made slidedown (and hover) disappear.
    expect(source).toContain("isInverted: true");
    expect(source).toContain('neutralDark: "#F8FAFC"');
    expect(source).toContain('neutralPrimary: "#F8FAFC"');
    expect(source).toContain('white: "#0F172A"');
  });
});
