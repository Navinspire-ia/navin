// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import type { MarketingConnector, MarketingContent } from "@/lib/marketing-api";
import {
  CONNECTOR_FIELDS,
  CONTENT_CHANNELS,
  channelLabel,
  connectorTone,
  contentActions,
  formatWhen,
  scheduleValue,
  sortQueue,
  statusTone,
  summarizeQueue,
} from "@/lib/marketing-publish";

function connector(patch: Partial<MarketingConnector>): MarketingConnector {
  return { channel: "linkedin", mode: "api", enabled: false, configured: false, ready: false, missing: [], fields: {}, secrets: {}, ...patch };
}

describe("marketing publish helpers", () => {
  it("labels every content channel and the webhook transport", () => {
    expect(CONTENT_CHANNELS).toContain("telegram");
    expect(channelLabel("producthunt")).toBe("Product Hunt");
    expect(channelLabel("x")).toBe("X");
    expect(channelLabel("webhook")).toBe("Webhook");
    expect(channelLabel("mastodon")).toBe("Mastodon");
    expect(channelLabel("")).toBe("");
  });

  it("knows which fields each connector needs and marks the secrets", () => {
    expect(CONNECTOR_FIELDS.x.map((field) => field.key)).toEqual(["x_api_key", "x_api_secret", "x_access_token", "x_access_secret"]);
    expect(CONNECTOR_FIELDS.x.every((field) => field.secret)).toBe(true);
    expect(CONNECTOR_FIELDS.facebook.find((field) => field.key === "page_id")?.secret).toBeUndefined();
    expect(CONNECTOR_FIELDS.blog.map((field) => field.key)).toEqual(["dir", "base_url"]);
  });

  it("summarises the connector state in one tone", () => {
    expect(connectorTone(connector({ ready: true, configured: true }))).toBe("ready");
    expect(connectorTone(connector({ configured: true }))).toBe("configured");
    expect(connectorTone(connector({ missing: ["linkedin_token"] }))).toBe("missing");
    expect(connectorTone(connector({ channel: "instagram", mode: "manual" }))).toBe("manual");
    expect(connectorTone(connector({ channel: "tiktok", mode: "manual", bridge: "webhook", ready: true }))).toBe("bridged");
  });

  it("summarises the queue and never reports a negative sendable count", () => {
    const summary = summarizeQueue({
      due: ["a"],
      waiting: ["b", "c"],
      approved: ["d", "e"],
      auto: [],
      blocked: ["e"],
      ready_channels: ["linkedin"],
      per_cycle: 3,
      auto_publish: false,
    });
    expect(summary).toMatchObject({ due: 1, waiting: 2, approved: 2, blocked: 1, sendable: 2, perCycle: 3, autoPublish: false });
    expect(summary.readyChannels).toEqual(["linkedin"]);
    const empty = summarizeQueue(undefined);
    expect(empty.sendable).toBe(0);
    expect(empty.perCycle).toBe(1);
  });

  it("offers the right actions per status", () => {
    expect(contentActions({ id: "1", status: "ready" })).toMatchObject({ approve: true, schedule: true, publish: true, unschedule: false, retire: true });
    expect(contentActions({ id: "1", status: "scheduled" })).toMatchObject({ approve: false, unschedule: true, publish: true });
    expect(contentActions({ id: "1", status: "failed" })).toMatchObject({ approve: true, unschedule: true });
    expect(contentActions({ id: "1", status: "published" })).toMatchObject({ approve: false, schedule: false, publish: false, retire: false, preview: true });
    expect(contentActions({ id: "1", status: "retired" })).toMatchObject({ publish: false, preview: false });
  });

  it("accepts relative and absolute schedule input", () => {
    expect(scheduleValue(" +2H ")).toBe("+2h");
    expect(scheduleValue("+1d")).toBe("+1d");
    expect(scheduleValue("2026-09-03T09:00:00Z")).toBe("2026-09-03T09:00:00.000Z");
    expect(scheduleValue("next tuesday")).toBe("");
    expect(scheduleValue("")).toBe("");
  });

  it("formats epochs and tones statuses", () => {
    expect(formatWhen(0)).toBe("");
    expect(formatWhen(1_788_343_200, "en")).toMatch(/2026/);
    expect(statusTone("published")).toBe("success");
    expect(statusTone("failed")).toBe("danger");
    expect(statusTone("scheduled")).toBe("info");
    expect(statusTone("ready")).toBe("warning");
    expect(statusTone("retired")).toBe("neutral");
  });

  it("sorts the queue: failures, then due scheduled, approved, drafts, published last", () => {
    const rows: MarketingContent[] = [
      { id: "pub", status: "published", published_at: 10 },
      { id: "late", status: "scheduled", scheduled_at: 300 },
      { id: "ready", status: "ready" },
      { id: "soon", status: "scheduled", scheduled_at: 100 },
      { id: "bad", status: "failed" },
      { id: "ok", status: "approved" },
      { id: "win", status: "winner", published_at: 50 },
    ];
    expect(sortQueue(rows).map((row) => row.id)).toEqual(["bad", "soon", "late", "ok", "ready", "win", "pub"]);
  });
});
