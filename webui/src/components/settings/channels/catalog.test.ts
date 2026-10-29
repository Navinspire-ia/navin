// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  CHANNEL_CONNECTOR_TWINS,
  CHANNEL_GROUPS,
  CHANNEL_ICONS,
  CHANNEL_PRESENTATION,
  channelCountKey,
  channelGroupCount,
  FEATURED_CONNECTORS,
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

  it("folds every connector category into exactly one of six tabs", () => {
    expect(CHANNEL_GROUPS.map((group) => group.id)).toEqual(["all", "messaging", "social", "productivity", "business", "data"]);
    expect(CHANNEL_GROUPS[0].connectors).toBeUndefined();
    const ids = CHANNEL_GROUPS.flatMap((group) => group.connectors ?? []);
    expect(new Set(ids).size).toBe(ids.length);
    // The 25 categories of navin/connectors/catalog.json.
    expect(ids.sort()).toEqual(
      ["cloud", "cms", "communication", "crm", "crypto", "data", "design", "documents", "ecommerce", "emailing", "erp", "forms", "geo", "hr", "marketing", "media", "monitoring", "payments", "prospecting", "search", "security", "social", "calendar", "video", "voice"].sort(),
    );
    const channelBuckets = CHANNEL_GROUPS.slice(1).flatMap((group) => group.channels);
    expect(channelBuckets.sort()).toEqual(["chat", "other", "social"]);
  });

  it("counts a tab from the cards the list reports, or its channels alone before they load", () => {
    const [all, messaging, social, productivity] = CHANNEL_GROUPS;
    const buckets = ["social", "social", "chat", "chat", "chat", "other"] as const;
    // Two chat channels share the card of their app: the list reports one card each.
    const counts = {
      communication: 6,
      video: 3,
      social: 4,
      calendar: 2,
      documents: 1,
      [channelCountKey("social")]: 2,
      [channelCountKey("chat")]: 1,
      [channelCountKey("other")]: 1,
    };
    expect(channelGroupCount(all, buckets, counts)).toBe(4 + 16);
    expect(channelGroupCount(messaging, buckets, counts)).toBe(2 + 9);
    expect(channelGroupCount(social, buckets, counts)).toBe(2 + 4);
    expect(channelGroupCount(productivity, buckets, counts)).toBe(3);
    expect(channelGroupCount(messaging, buckets, null)).toBe(4);
    expect(channelGroupCount(productivity, buckets, null)).toBe(0);
    expect(channelGroupCount(all, [], {})).toBe(0);
  });

  it("gives every channel a bundled logo and pairs each shared card with a real app connector", () => {
    const assets = resolve(__dirname, "../../../assets");
    for (const [name, icon] of Object.entries(CHANNEL_ICONS)) {
      const file = icon.startsWith("channels/") ? resolve(assets, icon) : resolve(assets, "connectors", icon);
      expect(existsSync(file), `${name}: ${icon}`).toBe(true);
    }
    for (const name of Object.keys(CHANNEL_PRESENTATION).filter((key) => !isHiddenToolsChannel(key))) {
      expect(CHANNEL_ICONS[name], name).toBeTruthy();
    }
    const catalog = JSON.parse(
      readFileSync(resolve(__dirname, "../../../../../navin/connectors/data/catalog.json"), "utf8"),
    ) as { providers: Record<string, { service?: string; id?: string }> | { service?: string; id?: string }[] };
    const rows = Array.isArray(catalog.providers) ? catalog.providers : Object.values(catalog.providers);
    const services = new Set(rows.map((row) => row.service ?? row.id));
    for (const [channel, service] of Object.entries(CHANNEL_CONNECTOR_TWINS)) {
      expect(services.has(service), `${channel} -> ${service}`).toBe(true);
      expect(CHANNEL_PRESENTATION[channel], channel).toBeTruthy();
    }
    // The apps that lead the grid exist, once each.
    expect(new Set(FEATURED_CONNECTORS).size).toBe(FEATURED_CONNECTORS.length);
    for (const service of FEATURED_CONNECTORS) expect(services.has(service), service).toBe(true);
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
