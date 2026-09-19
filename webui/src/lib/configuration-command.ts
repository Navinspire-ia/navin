// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { SettingsSectionKey } from "@/components/settings/SettingsView";
import type { SlashCommand } from "@/lib/types";

// These commands open the existing settings screens before chat routing.
const sections: Record<string, SettingsSectionKey> = {
  overview: "overview", account: "account", appearance: "appearance",
  providers: "providers", models: "models", image: "image", video: "video",
  voice: "voice", browser: "browser", computer: "computer", tools: "tools",
  channels: "channels", apps: "apps", automations: "automations",
  skills: "skills", templates: "templates", runtime: "runtime",
  advanced: "advanced", about: "about", studio: "studio",
  model: "models", mcp: "tools", system: "runtime", security: "advanced",
  guardrails: "advanced", theme: "appearance", web: "browser",
};

const shortcuts = Object.keys(sections).filter((section) => !["overview", "studio"].includes(section));

export type ConfigurationCommand =
  | { section: SettingsSectionKey; error?: never }
  | { section?: never; error: "unknown_section" };

export function configurationCommand(content: string): ConfigurationCommand | null {
  const [head, ...args] = content.trim().toLowerCase().split(/\s+/);
  if (head === "/settings") {
    if (args.length === 0) return { section: "overview" };
    const section = args.length === 1 && Object.hasOwn(sections, args[0]) ? sections[args[0]] : null;
    return section ? { section } : { error: "unknown_section" };
  }
  const name = head.slice(1);
  // Keep commands with briefs or preset names on their existing engine path.
  if (!head.startsWith("/") || args.length > 0 || !shortcuts.includes(name)) return null;
  return { section: sections[name] };
}

export function configurationCommands(commands: SlashCommand[]): SlashCommand[] {
  const local: SlashCommand[] = [
    {
      command: "/settings", title: "Settings", description: "Open Navin settings",
      icon: "settings", lifecycle: "side_channel", acceptsArgs: true, argHint: "[section]",
    },
    ...shortcuts.map((section): SlashCommand => ({
      command: `/${section}`, title: `${section[0].toUpperCase()}${section.slice(1)} settings`,
      description: `Open ${sections[section]} configuration`,
      icon: "settings", lifecycle: "side_channel", acceptsArgs: false,
    })),
  ];
  return [...commands, ...local.filter((item) => !commands.some((row) => row.command === item.command))];
}

export function configurationHash(section: SettingsSectionKey, sessionKey?: string | null): string {
  const query = new URLSearchParams();
  if (sessionKey) query.set("chat", sessionKey);
  query.set("section", section);
  return `#/settings?${query}`;
}
