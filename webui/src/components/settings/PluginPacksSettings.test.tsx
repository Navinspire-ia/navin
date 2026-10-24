// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import i18n from "@/i18n";
import type { PluginPackSummary } from "@/lib/types";

import { PluginPacksSettings } from "./PluginPacksSettings";

const api = vi.hoisted(() => ({
  fetchPluginPacks: vi.fn(),
  removePluginPack: vi.fn(),
  setPluginPackEnabled: vi.fn(),
}));
vi.mock("@/lib/api", () => api);
vi.mock("@/providers/ClientProvider", () => ({ useClient: () => ({ token: "t" }) }));
vi.mock("@/components/settings/MigrationSettings", () => ({ MigrationSettings: () => null }));
vi.mock("@/components/settings/InstallSkillDialog", () => ({ InstallSkillDialog: () => null }));

function pack(overrides: Partial<PluginPackSummary>): PluginPackSummary {
  return {
    name: "kit",
    display_name: "Kit",
    version: "0.1.0",
    description: "",
    author: "",
    homepage: "",
    source: "path",
    enabled: true,
    skills: ["one"],
    mcp_servers: [],
    ...overrides,
  };
}

beforeEach(async () => {
  await i18n.changeLanguage("en");
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PluginPacksSettings components", () => {
  it("shows the agents, commands and hook events a pack ships", async () => {
    api.fetchPluginPacks.mockResolvedValue({
      plugins: [
        pack({
          agents: ["doc-writer"],
          commands: ["lint-docs", "fix-links"],
          hooks: ["PreToolUse", "Stop"],
        }),
      ],
    });
    render(<PluginPacksSettings />);
    expect(await screen.findByText("1 agent(s)")).toBeTruthy();
    const commands = screen.getByText("2 command(s)");
    expect(commands.closest("span")?.getAttribute("title")).toBe("/lint-docs, /fix-links");
    expect(screen.getByText("Hooks: PreToolUse, Stop")).toBeTruthy();
  });

  it("hides the extra badges for a skills-only pack from an older backend", async () => {
    api.fetchPluginPacks.mockResolvedValue({ plugins: [pack({})] });
    render(<PluginPacksSettings />);
    expect(await screen.findByText("1 skill(s)")).toBeTruthy();
    expect(screen.queryByText(/agent\(s\)/)).toBeNull();
    expect(screen.queryByText(/command\(s\)/)).toBeNull();
    expect(screen.queryByText(/^Hooks:/)).toBeNull();
  });
});
