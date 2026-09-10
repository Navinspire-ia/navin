// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  CHANNEL_PRESENTATION,
  channelDocsUrl,
  channelFilterTab,
  isHiddenToolsChannel,
  mergeSocialChannelFeatures,
  SOCIAL_CHANNEL_NAMES,
} from "@/components/settings/channels/catalog";

describe("tools channel catalog", () => {
  it("hides the always-on WebSocket workbench channel", () => {
    expect(isHiddenToolsChannel("websocket")).toBe(true);
    expect(isHiddenToolsChannel("whatsapp")).toBe(false);
  });

  it("never builds a private GitHub docs URL from a relative path", () => {
    expect(channelDocsUrl("guides/whatsapp-ai-agent.md")).toBeUndefined();
    expect(channelDocsUrl("guides/email-ai-agent.md", "")).toBeUndefined();
    expect(channelDocsUrl("https://t.me/BotFather")).toBe("https://t.me/BotFather");
  });

  it("points official setup URLs at real vendor pages", () => {
    expect(CHANNEL_PRESENTATION.telegram.setup?.officialUrl).toBe("https://t.me/BotFather");
    expect(CHANNEL_PRESENTATION.slack.setup?.officialUrl).toBe("https://api.slack.com/apps");
    expect(CHANNEL_PRESENTATION.discord.setup?.officialUrl).toBe(
      "https://discord.com/developers/applications",
    );
    expect(CHANNEL_PRESENTATION.email.setup?.officialUrl).toBe(
      "https://support.google.com/accounts/answer/185833",
    );
    expect(CHANNEL_PRESENTATION.signal.setup?.officialUrl).toBe(
      "https://github.com/AsamK/signal-cli",
    );
    expect(CHANNEL_PRESENTATION.msteams.setup?.officialUrl).toBe(
      "https://dev.teams.microsoft.com/apps",
    );
    expect(CHANNEL_PRESENTATION.whatsapp.setup?.mode).toBe("connect");
    expect(CHANNEL_PRESENTATION.whatsapp.setup?.command).toBeUndefined();
  });

  it("lists marketing social networks as OAuth channels", () => {
    for (const name of ["reddit", "linkedin", "instagram", "facebook", "tiktok"] as const) {
      expect(CHANNEL_PRESENTATION[name].setup?.mode).toBe("oauth");
      expect(CHANNEL_PRESENTATION[name].setup?.officialUrl).toMatch(/^https:\/\//);
    }
  });

  it("groups channels into social, chat, and other tabs", () => {
    expect(channelFilterTab("reddit")).toBe("social");
    expect(channelFilterTab("telegram")).toBe("chat");
    expect(channelFilterTab("email")).toBe("other");
    expect(channelFilterTab("msteams")).toBe("other");
  });

  it("keeps social networks visible when the gateway catalog omits them", () => {
    const merged = mergeSocialChannelFeatures([
      {
        name: "telegram",
        display_name: "Telegram",
        type: "channel",
        enabled: false,
        installed: true,
        ready: false,
        status: "not_enabled",
        install_supported: true,
        requires_restart: true,
      },
    ]);
    expect(merged.map((row) => row.name)).toEqual(["telegram", ...SOCIAL_CHANNEL_NAMES]);
    expect(merged.filter((row) => row.kind === "social")).toHaveLength(SOCIAL_CHANNEL_NAMES.length);
  });
});
