// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from "react";
import { Checkbox, DefaultButton, Dropdown, MessageBar, MessageBarType, PrimaryButton, TextField } from "@fluentui/react";

import type { MarketingContent, MarketingDesk } from "@/lib/marketing-api";
import { channelLabel } from "@/lib/marketing-publish";
import type { RunAction, Tx } from "./MarketingPublish";

const BUTTON = { root: { minHeight: 36, cursor: "pointer" as const } };
const CONSENT_KEYS = new Set(["publish_consent", "music_usage_confirmed", "branded_content_policy_confirmed"]);

export function PublicationOptions({ row, desk, busy, tx, run, onClose }: {
  row: MarketingContent;
  desk: MarketingDesk;
  busy: boolean;
  tx: Tx;
  run: RunAction;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<MarketingContent>({
    ...row, disable_comment: row.disable_comment ?? true, disable_duet: row.disable_duet ?? true,
    disable_stitch: row.disable_stitch ?? true, brand_content_toggle: row.brand_content_toggle ?? false,
    brand_organic_toggle: row.brand_organic_toggle ?? false, is_aigc: row.is_aigc ?? false,
  });
  const [creator, setCreator] = useState<MarketingDesk["creator_info"]>();
  const [capabilities, setCapabilities] = useState<MarketingDesk["content_capabilities"]>();
  const tiktok = row.channel === "tiktok";
  const media = desk.creatives.filter((item) => ["image", "banner", "video"].includes(item.kind || "") && Boolean(item.path || item.preview));
  const selectedMedia = media.find((item) => item.id === draft.creative_id);
  const preview = selectedMedia?.preview || draft.media_url || "";
  const isVideo = selectedMedia?.kind === "video" || draft.media_type === "video";
  const update = <K extends keyof MarketingContent>(key: K, value: MarketingContent[K]) => {
    setDraft((old) => ({
      ...old, ...(!CONSENT_KEYS.has(key) ? { publish_consent: false, music_usage_confirmed: false, branded_content_policy_confirmed: false } : {}),
      [key]: value,
    }));
    setCapabilities(undefined);
  };
  const save = async (check = false) => {
    const ok = await run("content-options", { ...draft, revision: row.updated_at }, (next) => {
      const saved = next.content.find((item) => item.id === row.id);
      if (saved) setDraft(saved);
    });
    if (ok && check) await run("content-capabilities", { id: row.id }, (next) => setCapabilities(next.content_capabilities));
    if (ok && !check) onClose();
  };
  const consentReady = !tiktok || Boolean(creator && draft.privacy_level && draft.publish_consent && draft.music_usage_confirmed && (!draft.brand_content_toggle || draft.branded_content_policy_confirmed));

  return (
    <section className="mt-3 rounded-xl border border-border/70 bg-background/80 p-4" data-testid={`marketing-options-${row.id}`}>
      <h4 className="mb-3 text-sm font-semibold">{tx("publication.edit", "Prepare this publication")}</h4>
      <div className="grid gap-3">
        <TextField label={tx("publication.title", "Title")} value={draft.title || ""} onChange={(_, value) => update("title", value || "")} />
        <TextField label={tx("publication.text", "Text or article")} multiline autoAdjustHeight value={draft.body || ""} onChange={(_, value) => update("body", value || "")} />
        <Dropdown
          label={tx("publication.media", "Studio image or video")}
          selectedKey={draft.creative_id || ""}
          options={[{ key: "", text: tx("publication.noMedia", "No studio media selected") }, ...media.map((item) => ({ key: item.id, text: `${item.kind}: ${item.placement || item.id}` }))]}
          onChange={(_, option) => {
            if (!option) return;
            const creative = media.find((item) => item.id === option.key);
            setDraft((old) => ({ ...old, creative_id: String(option.key), media_path: "", media_url: "", media_type: creative?.kind === "video" ? "video" : "image", publish_consent: false, music_usage_confirmed: false, branded_content_policy_confirmed: false }));
            setCapabilities(undefined);
          }}
          data-testid={`marketing-media-select-${row.id}`}
        />
        <TextField label={tx("publication.mediaUrl", "Public HTTPS media URL (optional when media hosting is configured)")} value={draft.media_url || ""} onChange={(_, value) => update("media_url", value || "")} />
        {!selectedMedia ? <Dropdown label={tx("publication.mediaType", "Media type")} selectedKey={draft.media_type || ""} options={[{ key: "", text: tx("publication.textOnly", "Text or link") }, { key: "image", text: tx("publication.image", "Image") }, { key: "video", text: tx("publication.video", "Video") }]} onChange={(_, option) => option && update("media_type", option.key as MarketingContent["media_type"])} /> : null}
        {preview ? (
          <div className="overflow-hidden rounded-lg border border-border/60">
            {isVideo ? <video src={preview} controls preload="metadata" className="max-h-80 w-full object-contain" aria-label={tx("publication.preview", "Media preview")} /> : <img src={preview} alt={tx("publication.preview", "Media preview")} className="max-h-80 w-full object-contain" />}
          </div>
        ) : null}
        {isVideo && !selectedMedia ? <TextField label={tx("publication.duration", "Video duration in seconds")} type="number" min={0} value={draft.duration_s ? String(draft.duration_s) : ""} onChange={(_, value) => update("duration_s", Number(value) || 0)} /> : null}
        {row.channel === "reddit" ? <>
          <TextField label={tx("publication.subreddit", "Reddit community (without r/)")} value={draft.reddit_subreddit || ""} placeholder={String(desk.settings?.publish?.reddit && typeof desk.settings.publish.reddit !== "number" ? desk.settings.publish.reddit.subreddit || "" : "")} onChange={(_, value) => update("reddit_subreddit", value || "")} />
          <Dropdown label={tx("publication.redditKind", "Reddit post type")} selectedKey={draft.reddit_kind || "self"} options={[{ key: "self", text: tx("publication.redditText", "Text post") }, { key: "link", text: tx("publication.redditLink", "Link post") }]} onChange={(_, option) => option && update("reddit_kind", option.key as MarketingContent["reddit_kind"])} />
          {draft.reddit_kind === "link" ? <TextField label={tx("publication.redditUrl", "Link destination")} value={draft.reddit_url || ""} onChange={(_, value) => update("reddit_url", value || "")} /> : null}
          <TextField label={tx("publication.flair", "Community flair ID (if required)")} value={draft.reddit_flair_id || ""} onChange={(_, value) => update("reddit_flair_id", value || "")} />
        </> : null}
        {tiktok ? <>
          <DefaultButton text={tx("publication.loadCreator", "Load TikTok account and available audiences")} disabled={busy} styles={BUTTON} onClick={() => void run("creator-info", { channel: "tiktok" }, (next) => {
            setCreator(next.creator_info);
            update("publish_consent", false);
          })} data-testid={`marketing-creator-${row.id}`} />
          {creator ? <>
            <p className="text-sm font-medium">{tx("publication.account", "Publishing as {{name}}", { name: creator.creator_nickname || creator.creator_username || channelLabel(row.channel) })}</p>
            {creator.max_video_post_duration_sec ? <p className="text-xs text-muted-foreground">{tx("publication.maxDuration", "This account accepts videos up to {{n}} seconds.", { n: creator.max_video_post_duration_sec })}</p> : null}
          </> : null}
          <Dropdown label={tx("publication.audience", "Who can see this post")} placeholder={tx("publication.chooseAudience", "Choose an audience")} selectedKey={draft.privacy_level || null} disabled={busy || !creator} options={(creator?.privacy_level_options || []).map((option) => ({ key: option, text: tx(`publication.privacy.${option}`, option.replace(/_/g, " ")) }))} onChange={(_, option) => option && update("privacy_level", String(option.key))} data-testid={`marketing-privacy-${row.id}`} />
          <Checkbox label={tx("publication.comments", "Allow comments")} disabled={!creator || creator.comment_disabled} checked={Boolean(creator && !creator.comment_disabled && !draft.disable_comment)} onChange={(_, value) => update("disable_comment", !value)} />
          {isVideo ? <>
            <Checkbox label={tx("publication.duet", "Allow Duet")} disabled={!creator || creator.duet_disabled} checked={Boolean(creator && !creator.duet_disabled && !draft.disable_duet)} onChange={(_, value) => update("disable_duet", !value)} />
            <Checkbox label={tx("publication.stitch", "Allow Stitch")} disabled={!creator || creator.stitch_disabled} checked={Boolean(creator && !creator.stitch_disabled && !draft.disable_stitch)} onChange={(_, value) => update("disable_stitch", !value)} />
          </> : null}
          <Checkbox label={tx("publication.ownBrand", "This post promotes my own brand")} checked={Boolean(draft.brand_organic_toggle)} onChange={(_, value) => update("brand_organic_toggle", Boolean(value))} />
          <Checkbox label={tx("publication.paidBrand", "This post promotes another brand for compensation")} checked={Boolean(draft.brand_content_toggle)} onChange={(_, value) => update("brand_content_toggle", Boolean(value))} />
          <Checkbox label={tx("publication.aiLabel", "Label this as AI-generated content")} checked={Boolean(draft.is_aigc)} onChange={(_, value) => update("is_aigc", Boolean(value))} />
          {!isVideo ? <>
            <TextField label={tx("publication.photos", "Photo carousel URLs, one HTTPS URL per line (optional)")} multiline value={(draft.photo_images || []).join("\n")} onChange={(_, value) => update("photo_images", (value || "").split(/\n/).map((line) => line.trim()).filter(Boolean))} />
            <Checkbox label={tx("publication.addMusic", "Let TikTok add music to the photos")} checked={Boolean(draft.auto_add_music)} onChange={(_, value) => update("auto_add_music", Boolean(value))} />
          </> : null}
          <Checkbox label={tx("publication.musicConsent", "I confirm that the music is authorized and accept TikTok's Music Usage Confirmation")} checked={Boolean(draft.music_usage_confirmed)} onChange={(_, value) => update("music_usage_confirmed", Boolean(value))} />
          <a href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en" target="_blank" rel="noopener noreferrer" className="text-xs underline">{tx("publication.musicTerms", "Music Usage Confirmation")}</a>
          {draft.brand_content_toggle ? <>
            <Checkbox label={tx("publication.brandedConsent", "I accept TikTok's Branded Content Policy for this post")} checked={Boolean(draft.branded_content_policy_confirmed)} onChange={(_, value) => update("branded_content_policy_confirmed", Boolean(value))} />
            <a href="https://www.tiktok.com/legal/page/global/bc-policy/en" target="_blank" rel="noopener noreferrer" className="text-xs underline">{tx("publication.brandedTerms", "Branded Content Policy")}</a>
          </> : null}
          <Checkbox label={tx("publication.sendConsent", "I have reviewed this post, its media, account and audience, and authorize its publication")} checked={Boolean(draft.publish_consent)} disabled={!creator || !draft.privacy_level} onChange={(_, value) => update("publish_consent", Boolean(value))} data-testid={`marketing-consent-${row.id}`} />
        </> : null}
        {capabilities ? <MessageBar messageBarType={capabilities.can_publish ? MessageBarType.success : MessageBarType.warning}>{capabilities.can_publish ? tx("publication.ready", "Format and account checks passed. The post is ready for approval.") : capabilities.errors.join(" ")}</MessageBar> : null}
        <div className="flex flex-wrap gap-2">
          <PrimaryButton text={tx("publication.save", "Save draft")} disabled={busy} onClick={() => void save()} styles={BUTTON} data-testid={`marketing-options-save-${row.id}`} />
          <DefaultButton text={tx("publication.check", "Save and check publication")} disabled={busy || !consentReady} onClick={() => void save(true)} styles={BUTTON} />
          <DefaultButton text={tx("cancel", "Cancel")} disabled={busy} onClick={onClose} styles={BUTTON} />
        </div>
      </div>
    </section>
  );
}
