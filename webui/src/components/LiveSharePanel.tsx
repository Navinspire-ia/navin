// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { intlLocale } from "@/i18n/intl";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AppWindow, Link2, Loader2, Radio, UserX } from "lucide-react";

import { ShareLinkCard } from "@/components/ShareLinkCard";
import { Button } from "@/components/ui/button";
import {
  createLiveShare,
  listLiveShares,
  revokeLiveShare,
  setLiveShareAccess,
  setLiveShareSite,
  updateLiveShareGuest,
  type LiveShare,
  type LiveShareAccess,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

const TTLS = ["1h", "24h", "7d"] as const;
const ACCESS: LiveShareAccess[] = ["read", "write", "full"];

function AccessPicker({
  value,
  disabled,
  onChange,
}: {
  value: LiveShareAccess;
  disabled?: boolean;
  onChange: (access: LiveShareAccess) => void;
}) {
  const { t } = useTranslation();
  const labels: Record<LiveShareAccess, { title: string; hint: string }> = {
    read: {
      title: t("chat.liveShare.access.read", { defaultValue: "Read only" }),
      hint: t("chat.liveShare.access.readHint", { defaultValue: "Follows live, cannot write" }),
    },
    write: {
      title: t("chat.liveShare.access.write", { defaultValue: "Read and write" }),
      hint: t("chat.liveShare.access.writeHint", { defaultValue: "Drives the agent: messages, answers" }),
    },
    full: {
      title: t("chat.liveShare.access.full", { defaultValue: "Full" }),
      hint: t("chat.liveShare.access.fullHint", { defaultValue: "Drives and downloads the session with git" }),
    },
  };
  return (
    <div className="grid grid-cols-3 gap-1.5" role="radiogroup" aria-label={t("chat.liveShare.access.label", { defaultValue: "Guest access" })}>
      {ACCESS.map((option) => (
        <button
          key={option}
          type="button"
          role="radio"
          aria-checked={value === option}
          disabled={disabled}
          onClick={() => onChange(option)}
          className={cn(
            "rounded-md border px-2 py-1.5 text-start transition-colors disabled:opacity-60",
            value === option ? "border-foreground/70 bg-muted" : "border-border/70 hover:bg-muted/50",
          )}
        >
          <span className="block text-[12.5px] font-medium">{labels[option].title}</span>
          <span className="block text-[11px] leading-tight text-muted-foreground">{labels[option].hint}</span>
        </button>
      ))}
    </div>
  );
}

function SiteToggle({
  checked,
  url,
  disabled,
  onChange,
}: {
  checked: boolean;
  url: string;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <label className="flex cursor-pointer items-start gap-2 rounded-md border border-border/70 px-2.5 py-2">
      <input
        type="checkbox"
        className="mt-0.5"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <AppWindow className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
      <span className="min-w-0 flex-1">
        <span className="block text-[12.5px] font-medium">
          {t("chat.liveShare.site.label", { defaultValue: "Share the running site" })}
          {url ? <code className="ms-1.5 font-normal text-muted-foreground">{url.replace(/^https?:\/\//, "")}</code> : null}
        </span>
        <span className="block text-[11.5px] leading-snug text-muted-foreground">
          {t("chat.liveShare.site.hint", {
            defaultValue: "Guests open and use your local site live, in their share page. A dev server also shows them its source code.",
          })}
        </span>
      </span>
    </label>
  );
}

/**
 * Live share of a chat: an end-to-end encrypted link (the key never leaves
 * the link's #fragment). Guests watch read only; the host grants, per guest,
 * driving the agent (messages, the agent's questions; approvals always stay
 * with the host) and downloading the whole session to continue locally.
 * The host may also share the project's running site (``siteUrl`` comes from
 * the Browser panel's Share button).
 */
export function LiveSharePanel({
  chatId,
  open,
  siteUrl = null,
}: {
  chatId: string | null;
  open: boolean;
  siteUrl?: string | null;
}) {
  const { t } = useTranslation();
  const { token } = useClient();
  const [shares, setShares] = useState<LiveShare[]>([]);
  const [ttl, setTtl] = useState<(typeof TTLS)[number]>("24h");
  const [access, setAccess] = useState<LiveShareAccess>("read");
  const [withSite, setWithSite] = useState(Boolean(siteUrl));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState("");
  const autoSiteRef = useRef(false);

  useEffect(() => {
    setWithSite(Boolean(siteUrl));
    autoSiteRef.current = false;
  }, [siteUrl, open]);

  const siteErrorText = useCallback(
    (code: string) => {
      if (code === "no_site") {
        return t("chat.liveShare.site.noSite", { defaultValue: "No site is running for this project. Start it (or ask the agent), then try again." });
      }
      if (code === "navin") return t("chat.liveShare.site.navin", { defaultValue: "This address is Navin itself: it is never shared." });
      return t("chat.liveShare.site.badUrl", { defaultValue: "Only a site running on this machine (localhost) can be shared." });
    },
    [t],
  );

  const refresh = useCallback(async () => {
    if (!chatId) return;
    try {
      // App links live in their own dialog (AppShareDialog).
      setShares((await listLiveShares(token, chatId)).filter((share) => share.scope !== "site"));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [chatId, token]);

  useEffect(() => {
    if (!open || !chatId) return;
    void refresh();
    // Guests join, leave and ask for control while the dialog is open.
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, [chatId, open, refresh]);

  async function act(run: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      const result = (await run()) as Partial<LiveShare> | undefined;
      if (result?.site_error) setError(siteErrorText(result.site_error));
      await refresh();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function copy(link: string) {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(link);
      window.setTimeout(() => setCopied(""), 1500);
    } catch {
      // Clipboard denied: the link stays selectable.
    }
  }

  // Opened from the Browser panel's Share button: the existing link gets the site too.
  useEffect(() => {
    if (!open || !siteUrl || autoSiteRef.current) return;
    const existing = shares[0];
    if (!existing || existing.site_url) return;
    autoSiteRef.current = true;
    void act(() => setLiveShareSite(token, existing.share_id, true, siteUrl));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per opening, when the list is known
  }, [open, shares, siteUrl, token]);

  const expiry = (seconds: number) =>
    new Date(seconds * 1000).toLocaleString(intlLocale(), { dateStyle: "short", timeStyle: "short" });

  return (
    <div className="rounded-lg border border-border/70 p-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Radio className="h-4 w-4 text-muted-foreground" aria-hidden />
        {t("chat.liveShare.title", { defaultValue: "Live share (link)" })}
      </div>

      {shares.length === 0 ? (
        <div className="mt-3 space-y-2">
        <AccessPicker value={access} disabled={busy} onChange={setAccess} />
        <SiteToggle checked={withSite} url={siteUrl ?? ""} disabled={busy} onChange={setWithSite} />
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-md border border-border/70 p-0.5" role="radiogroup" aria-label={t("chat.liveShare.duration", { defaultValue: "Link valid for" })}>
            {TTLS.map((option) => (
              <button
                key={option}
                type="button"
                role="radio"
                aria-checked={ttl === option}
                className={cn(
                  "rounded px-2 py-1 text-[12px] transition-colors",
                  ttl === option ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground",
                )}
                onClick={() => setTtl(option)}
              >
                {t(`chat.liveShare.ttl.${option}`, { defaultValue: option })}
              </button>
            ))}
          </div>
          <Button
            size="sm"
            disabled={busy || !chatId}
            onClick={() => void act(() =>
              withSite
                ? createLiveShare(token, chatId!, ttl, access, "", { url: siteUrl ?? "" })
                : createLiveShare(token, chatId!, ttl, access))}
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Link2 className="h-4 w-4" aria-hidden />}
            {t("chat.liveShare.create", { defaultValue: "Create the link" })}
          </Button>
        </div>
        </div>
      ) : null}

      {shares.map((share) => (
        <div key={share.share_id} className="mt-3 space-y-2 text-[12.5px]">
          <ShareLinkCard
            link={share.link}
            tone="session"
            label={t("chat.liveShare.linkLabel", { defaultValue: "Session link" })}
            copied={copied === share.link}
            onCopy={() => void copy(share.link)}
          />
          <AccessPicker
            value={share.access ?? "read"}
            disabled={busy}
            onChange={(next) => void act(() => setLiveShareAccess(token, share.share_id, next))}
          />
          <SiteToggle
            checked={Boolean(share.site_url)}
            url={share.site_url || siteUrl || ""}
            disabled={busy}
            onChange={(next) => void act(() => setLiveShareSite(token, share.share_id, next, next ? siteUrl ?? "" : ""))}
          />
          <p className="text-muted-foreground">
            <span className={cn("me-1 inline-block h-2 w-2 rounded-full", share.online ? "bg-emerald-500" : "bg-muted-foreground/40")} />
            {share.online
              ? t("chat.liveShare.online", { defaultValue: "Connected to the relay" })
              : t("chat.liveShare.connecting", { defaultValue: "Connecting to the relay..." })}
            {" · "}
            {t("chat.liveShare.expires", { defaultValue: "expires {{when}}", when: expiry(share.expires_at) })}
          </p>

          {share.guests.length === 0 ? (
            <p className="text-muted-foreground">{t("chat.liveShare.noGuests", { defaultValue: "Nobody has joined yet." })}</p>
          ) : (
            <ul className="space-y-1.5">
              {share.guests.map((guest) => (
                <li key={guest.guest_id} className="flex flex-wrap items-center gap-2 rounded-md border border-border/60 px-2 py-1.5">
                  <span className="min-w-0 flex-1 truncate font-medium text-foreground">{guest.name}</span>
                  {guest.requested_control && !guest.drive ? (
                    <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] text-amber-900 dark:bg-amber-500/15 dark:text-amber-200">
                      {t("chat.liveShare.asksControl", { defaultValue: "asks to drive" })}
                    </span>
                  ) : null}
                  {guest.requested_download && !guest.download ? (
                    <span className="rounded bg-sky-100 px-1.5 py-0.5 text-[11px] text-sky-900 dark:bg-sky-500/15 dark:text-sky-200">
                      {t("chat.liveShare.asksDownload", { defaultValue: "asks to download" })}
                    </span>
                  ) : null}
                  <label className="flex items-center gap-1 text-[12px]">
                    <input
                      type="checkbox"
                      checked={guest.drive}
                      disabled={busy}
                      onChange={(e) => void act(() => updateLiveShareGuest(token, share.share_id, guest.guest_id, { drive: e.target.checked }))}
                    />
                    {t("chat.liveShare.drive", { defaultValue: "May drive" })}
                  </label>
                  <label className="flex items-center gap-1 text-[12px]">
                    <input
                      type="checkbox"
                      checked={guest.download}
                      disabled={busy}
                      onChange={(e) => void act(() => updateLiveShareGuest(token, share.share_id, guest.guest_id, { download: e.target.checked }))}
                    />
                    {t("chat.liveShare.download", { defaultValue: "May download" })}
                  </label>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-6 w-6"
                    aria-label={t("chat.liveShare.remove", { defaultValue: "Remove" })}
                    disabled={busy}
                    onClick={() => void act(() => updateLiveShareGuest(token, share.share_id, guest.guest_id, { remove: true }))}
                  >
                    <UserX className="h-3.5 w-3.5" />
                  </Button>
                </li>
              ))}
            </ul>
          )}

          <Button size="sm" variant="ghost" className="text-destructive" disabled={busy} onClick={() => void act(() => revokeLiveShare(token, share.share_id))}>
            {t("chat.liveShare.stop", { defaultValue: "Stop sharing" })}
          </Button>
        </div>
      ))}

      {error ? (
        <p className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-2 text-[12.5px] text-destructive">{error}</p>
      ) : null}
    </div>
  );
}
