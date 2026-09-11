// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("studio module pointer drag", () => {
  it("is shared by Settings and the sidebar, without HTML5 drag", () => {
    const hook = readFileSync(resolve(__dirname, "useStudioModuleDrag.ts"), "utf8");
    const settings = readFileSync(
      resolve(__dirname, "../components/settings/StudioSettings.tsx"),
      "utf8",
    );
    const sidebar = readFileSync(resolve(__dirname, "../components/Sidebar.tsx"), "utf8");
    expect(hook).toContain("pointermove");
    expect(hook).toContain("placeStudioModule");
    expect(hook).toContain("studioModuleAtY");
    expect(hook).toContain("setPointerCapture");
    expect(hook).not.toContain("draggable");
    expect(settings).toContain("useStudioModuleDrag");
    expect(sidebar).toContain("useStudioModuleDrag");
    expect(sidebar).toContain('data-testid="sidebar-studio-config"');
    expect(sidebar).toContain("sidebar-studio-toggle-");
  });
});
