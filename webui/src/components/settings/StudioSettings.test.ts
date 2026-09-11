// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { STUDIO_MODULE_IDS } from "@/lib/studio-modules";

const here = dirname(fileURLToPath(import.meta.url));

describe("Studio settings module", () => {
  it("is a real Settings section wired to the sidebar prefs", () => {
    const panel = readFileSync(join(here, "StudioSettings.tsx"), "utf8");
    const settings = readFileSync(join(here, "SettingsView.tsx"), "utf8");
    const app = readFileSync(join(here, "../../App.tsx"), "utf8");
    const sidebar = readFileSync(join(here, "../Sidebar.tsx"), "utf8");
    expect(panel).toContain("useStudioModuleDrag");
    expect(panel).toContain("onPointerDown");
    expect(panel).toContain("host-no-drag");
    expect(panel).not.toContain("draggable");
    expect(panel).toContain("ToggleButton");
    expect(panel).toContain("moveStudioModule");
    expect(panel).toContain("toggleStudioHidden");
    expect(panel).toContain('data-testid="studio-module-list"');
    expect(panel).not.toContain("Coming soon");
    expect(settings).toContain('key: "studio"');
    expect(settings).toContain('activeSection === "studio"');
    expect(settings).toContain("<StudioSettings");
    expect(settings).toContain('onSelectSection("studio")');
    expect(app).toContain('"studio"');
    expect(sidebar).toContain("useStudioModuleDrag");
    expect(sidebar).toContain("visibleStudioModules");
    expect(sidebar).toContain("showStudio");
    expect(sidebar).toContain("toggleStudioHidden");
    expect(sidebar).toContain('data-testid="sidebar-studio-config"');
    expect(sidebar).toContain('onOpenSettings("studio")');
    expect(sidebar).toContain("sidebar-studio-toggle-");
    expect(STUDIO_MODULE_IDS).toHaveLength(14);
  });
});
