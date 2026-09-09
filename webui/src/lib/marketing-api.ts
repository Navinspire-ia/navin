// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { apiBodyHeaders, apiRequest } from "@/lib/api";
import { studioSessionQuery } from "@/lib/studio-request";
import type { TradingLoopSchedule } from "@/lib/trading-api";

export type MarketingLoopSchedule = TradingLoopSchedule;

export interface MarketingBrand {
  company?: string;
  product?: string;
  site?: string;
  description?: string;
  tone?: string;
  colors?: string[];
  fonts?: string[];
  logo?: string;
  audience?: string;
  forbidden?: string[];
  competitors?: string[];
  liked_examples?: string[];
  languages?: string[];
  countries?: string[];
  industries?: string[];
}

export interface MarketingProduct {
  name?: string;
  one_liner?: string;
  category?: string;
  pain?: string;
  value_prop?: string;
  stack?: string[];
  workspace?: string;
  site?: string;
  source_kind?: string;
  docs?: string[];
  screenshots?: string[];
}

export interface MarketingPositioning {
  icp?: string;
  personas?: string[];
  pain?: string;
  value_prop?: string;
  differentiation?: string;
  statement?: string;
}

export interface MarketingCampaign {
  id: string;
  label?: string;
  goal?: string;
  days?: number;
  signups?: number;
  channels?: string[];
  mix?: Record<string, number>;
  objectives?: string[];
  status?: string;
}

export type MarketingContentStatus =
  | "draft"
  | "ready"
  | "approved"
  | "scheduled"
  | "publishing"
  | "published"
  | "failed"
  | "winner"
  | "retired";

export interface MarketingContent {
  id: string;
  updated_at?: number;
  channel?: string;
  title?: string;
  body?: string;
  status?: MarketingContentStatus | string;
  hook?: string;
  campaign_id?: string;
  parent_id?: string;
  cta?: string;
  hashtags?: string[];
  angle?: string;
  source?: string;
  model?: string;
  creative_id?: string;
  media_type?: "image" | "video";
  media_url?: string;
  media_path?: string;
  mime_type?: string;
  duration_s?: number;
  photo_images?: string[];
  photo_cover_index?: number;
  reddit_kind?: "self" | "link";
  reddit_subreddit?: string;
  reddit_url?: string;
  reddit_flair_id?: string;
  privacy_level?: string;
  disable_comment?: boolean;
  disable_duet?: boolean;
  disable_stitch?: boolean;
  brand_content_toggle?: boolean;
  brand_organic_toggle?: boolean;
  is_aigc?: boolean;
  auto_add_music?: boolean;
  publish_consent?: boolean;
  music_usage_confirmed?: boolean;
  branded_content_policy_confirmed?: boolean;
  scheduled_at?: number;
  approved_at?: number;
  published_at?: number;
  failed_at?: number;
  published_url?: string;
  remote_id?: string;
  utm_url?: string;
  publish_text?: string;
  error?: string;
  receipt?: { channel?: string; via?: string; mode?: string; origin?: string; url?: string; id?: string; photo?: boolean; phase?: string; operation_id?: string; pending?: boolean; published?: boolean; poll_after_s?: number } | null;
}

export type MarketingConnectorMode = "api" | "manual" | "file";

export interface MarketingConnector {
  channel: string;
  mode: MarketingConnectorMode | string;
  enabled: boolean;
  configured: boolean;
  ready: boolean;
  missing: string[];
  hint?: string;
  fields: Record<string, string>;
  secrets: Record<string, boolean>;
  tested_at?: number;
  account?: string;
  last_error?: string;
  bridge?: string;
}

export interface MarketingQueue {
  due: string[];
  waiting: string[];
  approved: string[];
  auto: string[];
  blocked: string[];
  ready_channels: string[];
  per_cycle: number;
  auto_publish: boolean;
}

export interface MarketingAiRouting {
  enabled: boolean;
  routed: number;
  tasks: { task: string; role: string; preset: string; model: string }[];
}

export interface MarketingPublishChannelSettings {
  enabled?: boolean;
  author?: string;
  page_id?: string;
  instagram_user_id?: string;
  auth_mode?: string;
  api_version?: string;
  subreddit?: string;
  user_agent?: string;
  flair_id?: string;
  chat_id?: string;
  to?: string;
  url?: string;
  dir?: string;
  base_url?: string;
  tested_at?: number;
  last_error?: string;
  account?: string;
}

export interface MarketingAnalyticsSource {
  provider?: "" | "plausible" | "matomo" | string;
  site_id?: string;
  base_url?: string;
  goal?: string;
  measured_at?: number;
  last_error?: string;
}

export interface MarketingSettings {
  execution_mode?: "approval" | "autonomous" | string;
  auto_publish?: boolean;
  ai_assist?: boolean;
  winner_multiple?: number;
  utm_campaign?: string;
  media_base_url?: string;
  publish?: Record<string, MarketingPublishChannelSettings | number | undefined> & { per_cycle?: number };
  analytics?: MarketingAnalyticsSource;
  channels?: Record<string, boolean | string>;
}

export interface MarketingPublishReceipt {
  id?: string;
  channel?: string;
  via?: string;
  url?: string;
  error?: string;
  ok?: boolean;
  dry_run?: boolean;
  manual?: boolean;
  pending?: boolean;
  status?: string;
  text?: string;
  link?: string;
  content?: MarketingContent;
}

export interface MarketingPublishReport {
  sent?: MarketingPublishReceipt[];
  failed?: MarketingPublishReceipt[];
  preview?: MarketingPublishReceipt[];
  pending?: MarketingPublishReceipt[];
  skipped?: string[];
  ready_channels?: string[];
}

export interface SocialConnection {
  provider: string;
  status: string;
  client_id: string;
  client_secret_set: boolean;
  redirect_uri: string;
  account: string;
  account_id: string;
  accounts: { id: string; name: string }[];
  expires_at: number;
  refreshable: boolean;
  requested_scopes: string[];
  granted_scopes: string[];
  scope_status: string;
  last_error: string;
  docs_url: string;
}

export interface SocialAuthorization extends Partial<SocialConnection> {
  authorization_url?: string;
  detail?: string;
}

export interface MarketingMeasureReport {
  measured?: number;
  engagement?: number;
  traffic?: number;
  provider?: string;
  error?: string;
}

export interface MarketingConnectionResult {
  ok: boolean;
  channel?: string;
  provider?: string;
  account?: string;
  author?: string;
  traffic?: number;
  error?: string;
}

export interface MarketingCreative {
  id: string;
  path?: string;
  kind?: string;
  placement?: string;
  prompt?: string;
  status?: string;
  preview?: string;
  source?: string;
  source_url?: string;
  asset?: string;
  aspect?: string;
  vision?: { verdict?: string; score?: number; notes?: string } | null;
}

export interface MarketingScoreRow {
  id: string;
  channel?: string;
  title?: string;
  views?: number;
  clicks?: number;
  conversions?: number;
  ctr?: number;
}

export interface MarketingKpis {
  campaigns?: number;
  content?: number;
  creatives?: number;
  winners?: number;
  published?: number;
  scheduled?: number;
  approved?: number;
  failed?: number;
  measured?: number;
  views?: number;
  clicks?: number;
  signups?: number;
  leads?: number;
  traffic?: number;
  revenue?: number;
}

export interface MarketingDesk {
  brand: MarketingBrand;
  product: MarketingProduct;
  positioning: MarketingPositioning;
  research: {
    market?: string;
    trends?: string[];
    keywords?: string[];
    competitors?: { name?: string; pricing?: string; note?: string; url?: string; angle?: string }[];
    pricing_notes?: string[];
    sources?: string[];
    queries?: string[];
    hits?: number;
    mode?: string;
    fetched_at?: number;
  };
  campaigns: MarketingCampaign[];
  content: MarketingContent[];
  creatives: MarketingCreative[];
  experiments: { id: string; kind?: string; control?: string; variant?: string; status?: string }[];
  analytics: {
    traffic?: number;
    leads?: number;
    signups?: number;
    customers?: number;
    revenue?: number;
    cac?: number;
    by_content?: Record<
      string,
      {
        views?: number;
        clicks?: number;
        conversions?: number;
        likes?: number;
        comments?: number;
        shares?: number;
        visitors?: number;
        signups?: number;
        measured_at?: number;
        source?: string;
        traffic_source?: string;
      }
    >;
  };
  scoreboard: MarketingScoreRow[];
  seo: {
    keywords?: string[];
    pages?: { url?: string; title?: string; description?: string; kind?: string }[];
    rankings?: { keyword?: string; url?: string; position?: number; source?: string }[];
  };
  ads: {
    campaigns?: {
      id?: string;
      name?: string;
      objective?: string;
      audience?: string;
      countries?: string[];
      languages?: string[];
      placements?: string[];
      copy?: string;
      status?: string;
      spend?: number;
      creative_ids?: string[];
    }[];
    spend?: number;
  };
  harvest?: {
    site?: string;
    name?: string;
    one_liner?: string;
    logo?: string;
    logo_preview?: string;
    colors?: string[];
    fonts?: string[];
    headings?: string[];
    ctas?: string[];
    images?: { url?: string; preview?: string; role?: string }[];
    pages?: { url?: string; title?: string; description?: string }[];
    social?: Record<string, string>;
    keywords?: string[];
  };
  social?: {
    posts?: {
      id: string;
      channel?: string;
      day?: number;
      hook?: string;
      body?: string;
      cta?: string;
      preview?: string;
      clip?: string;
      clip_id?: string;
      kind?: string;
      status?: string;
    }[];
  };
  produce?: { produced?: number; skipped?: string[] };
  competitors: { id: string; name?: string; pricing?: string; note?: string }[];
  launch: { status?: string; product?: string; folder?: string; items?: { id: string; label?: string; body?: string; file?: string }[] };
  settings: MarketingSettings;
  connectors?: MarketingConnector[];
  oauth_connections?: SocialConnection[];
  oauth?: SocialAuthorization;
  creator_info?: {
    creator_username?: string;
    creator_nickname?: string;
    creator_avatar_url?: string;
    privacy_level_options?: string[];
    comment_disabled?: boolean;
    duet_disabled?: boolean;
    stitch_disabled?: boolean;
    max_video_post_duration_sec?: number;
  };
  content_capabilities?: {
    channel: string;
    supported_types: string[];
    media_type: string;
    can_publish: boolean;
    errors: string[];
    requires_public_media_url: boolean;
    requires_creator_info: boolean;
    asynchronous: boolean;
  };
  secrets_set?: Record<string, boolean>;
  queue?: MarketingQueue;
  ai?: MarketingAiRouting;
  publish?: MarketingPublishReport;
  measure?: MarketingMeasureReport;
  connection?: MarketingConnectionResult;
  loop: {
    enabled?: boolean;
    phase?: string;
    next_due?: number;
    last_result?: string;
    cycle?: number;
    schedule?: MarketingLoopSchedule | null;
  };
  journal: { id?: string; t?: number; kind?: string; text?: string }[];
  kpis: MarketingKpis;
  armed?: boolean;
  skills?: { id: string; name: string }[];
}

function marketingUrl(action: string): string {
  return `/api/marketing?action=${encodeURIComponent(action)}${studioSessionQuery()}`;
}

export async function fetchMarketingDesk(token: string): Promise<MarketingDesk> {
  return apiRequest<MarketingDesk>(marketingUrl("snapshot"), token, {
    headers: apiBodyHeaders("{}"),
  });
}

export async function postMarketing(
  token: string,
  action: string,
  body: Record<string, unknown> = {},
): Promise<MarketingDesk> {
  return apiRequest<MarketingDesk>(marketingUrl(action), token, {
    headers: apiBodyHeaders(JSON.stringify(body)),
  });
}
