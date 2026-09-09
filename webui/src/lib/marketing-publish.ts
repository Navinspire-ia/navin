// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { MarketingConnector, MarketingContent, MarketingQueue } from "@/lib/marketing-api";

/** Every content channel the desk knows, in the order the UI shows them. */
export const CONTENT_CHANNELS = [
  "linkedin",
  "x",
  "facebook",
  "instagram",
  "tiktok",
  "youtube",
  "telegram",
  "blog",
  "email",
  "reddit",
  "producthunt",
] as const;

export type ContentChannel = (typeof CONTENT_CHANNELS)[number];

/** Channels with a posting connector (API or local sink); the rest are copy-and-paste or webhook bridged. */
export const API_CHANNELS = ["linkedin", "x", "facebook", "instagram", "tiktok", "reddit", "telegram", "email", "webhook", "blog"] as const;

export const CHANNEL_LABELS: Record<string, string> = {
  linkedin: "LinkedIn",
  x: "X",
  facebook: "Facebook",
  instagram: "Instagram",
  tiktok: "TikTok",
  youtube: "YouTube",
  telegram: "Telegram",
  blog: "Blog",
  email: "Email",
  reddit: "Reddit",
  producthunt: "Product Hunt",
  webhook: "Webhook",
};

export function channelLabel(channel?: string | null): string {
  const key = String(channel || "").toLowerCase();
  return CHANNEL_LABELS[key] || (key ? key.charAt(0).toUpperCase() + key.slice(1) : "");
}

/** Text fields each connector needs besides its secrets. */
export const CONNECTOR_FIELDS: Record<string, { key: string; placeholder: string; secret?: boolean }[]> = {
  linkedin: [
    { key: "linkedin_token", placeholder: "AQV...", secret: true },
    { key: "author", placeholder: "urn:li:person:... or urn:li:organization:..." },
  ],
  x: [
    { key: "x_api_key", placeholder: "API key", secret: true },
    { key: "x_api_secret", placeholder: "API secret", secret: true },
    { key: "x_access_token", placeholder: "Access token", secret: true },
    { key: "x_access_secret", placeholder: "Access token secret", secret: true },
  ],
  facebook: [
    { key: "facebook_page_token", placeholder: "EAAB...", secret: true },
    { key: "page_id", placeholder: "1234567890" },
  ],
  instagram: [
    { key: "instagram_access_token", placeholder: "Instagram access token", secret: true },
    { key: "instagram_user_id", placeholder: "Instagram professional account ID" },
  ],
  tiktok: [
    { key: "tiktok_access_token", placeholder: "TikTok access token", secret: true },
  ],
  reddit: [
    { key: "reddit_access_token", placeholder: "Reddit access token", secret: true },
    { key: "subreddit", placeholder: "Community name without r/" },
    { key: "user_agent", placeholder: "web:your-app:1.0 (by /u/your-account)" },
    { key: "flair_id", placeholder: "Optional flair ID required by the community" },
  ],
  telegram: [
    { key: "telegram_bot_token", placeholder: "123456:ABC... (empty = Navin bot)", secret: true },
    { key: "chat_id", placeholder: "@channel or -100..." },
  ],
  email: [{ key: "to", placeholder: "news@company.com, list@company.com" }],
  webhook: [
    { key: "url", placeholder: "https://hooks.zapier.com/..." },
    { key: "webhook_secret", placeholder: "shared secret for X-Navin-Signature", secret: true },
  ],
  blog: [
    { key: "dir", placeholder: "/path/to/site/_posts (empty = marketing/blog)" },
    { key: "base_url", placeholder: "https://blog.example.com" },
  ],
};

export type ConnectorTone = "ready" | "configured" | "missing" | "manual" | "bridged";

/** One word the settings pane can colour: what the connector can do right now. */
export function connectorTone(row: MarketingConnector): ConnectorTone {
  if (row.mode === "manual") return row.bridge ? "bridged" : "manual";
  if (row.ready) return "ready";
  if (row.configured) return "configured";
  return "missing";
}

export type QueueSummary = {
  due: number;
  waiting: number;
  approved: number;
  auto: number;
  blocked: number;
  sendable: number;
  readyChannels: string[];
  perCycle: number;
  autoPublish: boolean;
};

export function summarizeQueue(queue?: MarketingQueue | null): QueueSummary {
  const due = queue?.due?.length || 0;
  const approved = queue?.approved?.length || 0;
  const auto = queue?.auto?.length || 0;
  const blocked = queue?.blocked?.length || 0;
  return {
    due,
    waiting: queue?.waiting?.length || 0,
    approved,
    auto,
    blocked,
    sendable: Math.max(0, due + approved + auto - blocked),
    readyChannels: [...(queue?.ready_channels || [])],
    perCycle: Math.max(1, Number(queue?.per_cycle || 1)),
    autoPublish: Boolean(queue?.auto_publish),
  };
}

/** Which row actions make sense for one content status. */
export function contentActions(row: MarketingContent): {
  approve: boolean;
  schedule: boolean;
  publish: boolean;
  unschedule: boolean;
  retire: boolean;
  preview: boolean;
} {
  const status = String(row.status || "draft");
  const live = status === "published" || status === "winner" || status === "publishing";
  return {
    approve: status === "draft" || status === "ready" || status === "failed",
    schedule: !live && status !== "retired",
    publish: !live && status !== "retired",
    unschedule: status === "scheduled" || status === "approved" || status === "failed",
    retire: !live && status !== "retired",
    preview: status !== "retired",
  };
}

/**
 * Turn the quick picker value into what the API accepts: "+2h", "+1d", or a local datetime.
 * Returns "" when nothing usable was typed.
 */
export function scheduleValue(raw: string): string {
  const text = raw.trim();
  if (!text) return "";
  if (/^\+\s*\d+\s*[mhd]$/i.test(text)) return text.replace(/\s+/g, "").toLowerCase();
  const local = new Date(text);
  if (Number.isNaN(local.getTime())) return "";
  return local.toISOString();
}

export function formatWhen(ts?: number | null, locale = "en"): string {
  const value = Number(ts || 0);
  if (!value) return "";
  try {
    return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value * 1000));
  } catch {
    return new Date(value * 1000).toISOString();
  }
}

export type StatusTone = "neutral" | "info" | "success" | "warning" | "danger";

export function statusTone(status?: string | null): StatusTone {
  switch (String(status || "")) {
    case "published":
    case "winner":
      return "success";
    case "scheduled":
    case "approved":
    case "publishing":
      return "info";
    case "failed":
      return "danger";
    case "retired":
      return "neutral";
    default:
      return "warning";
  }
}

/** Sort for the queue: failures first, then due/scheduled by time, then approved, ready, published last. */
export function sortQueue(rows: MarketingContent[]): MarketingContent[] {
  const rank: Record<string, number> = { failed: 0, scheduled: 1, approved: 2, ready: 3, draft: 4, winner: 5, published: 6, retired: 7 };
  return [...rows].sort((a, b) => {
    const ra = rank[String(a.status || "draft")] ?? 4;
    const rb = rank[String(b.status || "draft")] ?? 4;
    if (ra !== rb) return ra - rb;
    if (ra === 1) return Number(a.scheduled_at || 0) - Number(b.scheduled_at || 0);
    if (ra === 6 || ra === 5) return Number(b.published_at || 0) - Number(a.published_at || 0);
    return 0;
  });
}
