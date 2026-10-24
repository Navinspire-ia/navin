// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { intlLocale } from "@/i18n/intl";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { AppWindow, Link2, Loader2, ShieldCheck, Users } from "lucide-react";

import { ShareLinkCard } from "@/components/ShareLinkCard";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { createAppShare, listLiveShares, revokeLiveShare, type LiveShare } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

const TTLS = ["1h", "24h", "7d"] as const;

/**
 * Share the running app alone (navin/share/host.py, scope "site"): whoever
 * has the link opens and uses it live, end-to-end encrypted, and sees nothing
 * of the conversation. Opened from the Browser panel and the thread's hint.
 */
export function AppShareDialog({
  open,
  chatId,
  siteUrl,
  onOpenChange,
}: {
  open: boolean;
  chatId: string | null;
  siteUrl: string;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const { token } = useClient();
  const [shares, setShares] = useState<LiveShare[]>([]);
  const [ttl, setTtl] = useState<(typeof TTLS)[number]>("24h");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (!chatId) return;
    try {
      setShares((await listLiveShares(token, chatId)).filter((share) => share.scope === "site"));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [chatId, token]);

  useEffect(() => {
    if (!open || !chatId) return;
    setError("");
    void refresh();
    // Visitors come and go while the dialog is open.
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => window.clearInterval(timer);
  }, [chatId, open, refresh]);

  const siteError = (code: string) => {
    if (code === "no_site") {
      return t("chat.liveShare.site.noSite", { defaultValue: "No site is running for this project. Start it (or ask the agent), then try again." });
    }
    if (code === "navin") return t("chat.liveShare.site.navin", { defaultValue: "This address is Navin itself: it is never shared." });
    return t("chat.liveShare.site.badUrl", { defaultValue: "Only a site running on this machine (localhost) can be shared." });
  };

  async function run(action: () => Promise<Partial<LiveShare> | void>) {
    setBusy(true);
    setError("");
    try {
      const result = await action();
      if (result && result.site_error) setError(siteError(result.site_error));
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
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard denied: the link stays selectable.
    }
  }

  const share = shares[0];
  const visitors = share?.guests.length ?? 0;
  const expiry = (seconds: number) =>
    new Date(seconds * 1000).toLocaleString(intlLocale(), { dateStyle: "short", timeStyle: "short" });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AppWindow className="h-4 w-4 text-muted-foreground" aria-hidden />
            {t("chat.liveShare.app.title", { defaultValue: "Share the application" })}
          </DialogTitle>
          <DialogDescription>
            {t("chat.liveShare.app.description", {
              defaultValue: "Anyone with the link opens and uses your application live, from any browser. They see the application only: never the conversation.",
            })}
          </DialogDescription>
        </DialogHeader>

        {!share ? (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <div
                className="flex rounded-md border border-border/70 p-0.5"
                role="radiogroup"
                aria-label={t("chat.liveShare.duration", { defaultValue: "Link valid for" })}
              >
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
                onClick={() => void run(() => createAppShare(token, chatId!, ttl, siteUrl))}
              >
                {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Link2 className="h-4 w-4" aria-hidden />}
                {t("chat.liveShare.app.create", { defaultValue: "Create the application link" })}
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-3 text-[12.5px]">
            <ShareLinkCard
              link={share.link}
              tone="app"
              label={t("chat.liveShare.app.linkLabel", { defaultValue: "Application link" })}
              copied={copied}
              onCopy={() => void copy(share.link)}
            />
            <p className="flex items-center gap-1.5 text-muted-foreground">
              <span className={cn("h-2 w-2 rounded-full", share.online ? "bg-emerald-500" : "bg-muted-foreground/40")} />
              {share.online
                ? t("chat.liveShare.app.online", { defaultValue: "Live" })
                : t("chat.liveShare.connecting", { defaultValue: "Connecting to the relay..." })}
              {" · "}
              <Users className="h-3.5 w-3.5" aria-hidden />
              {t("chat.liveShare.app.visitors", { defaultValue: "{{count}} visitor(s) now", count: visitors })}
              {" · "}
              {t("chat.liveShare.expires", { defaultValue: "expires {{when}}", when: expiry(share.expires_at) })}
            </p>
            <p className="text-muted-foreground">
              {t("chat.liveShare.app.keepOn", {
                defaultValue: "Keep Navin open and the application running: the link pauses while your computer is off.",
              })}
            </p>
            <Button
              size="sm"
              variant="ghost"
              className="text-destructive"
              disabled={busy}
              onClick={() => void run(() => revokeLiveShare(token, share.share_id))}
            >
              {t("chat.liveShare.app.stop", { defaultValue: "Stop sharing the application" })}
            </Button>
          </div>
        )}

        <p className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
          <ShieldCheck className="h-3.5 w-3.5 shrink-0" aria-hidden />
          {t("chat.liveShare.app.e2e", { defaultValue: "End-to-end encrypted through navin.live." })}
        </p>

        {error ? (
          <p className="rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-2 text-[12.5px] text-destructive">
            {error}
          </p>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
