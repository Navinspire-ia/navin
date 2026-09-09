// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  CHANNEL_PRESENTATION,
  channelDocsUrl,
  isHiddenToolsChannel,
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
});
