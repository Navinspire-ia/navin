// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ArrowUpRight,
  Gift,
  Megaphone,
  RefreshCw,
  AlertTriangle,
  ShieldCheck,
  Sparkles,
  Star,
  X,
  Zap,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { useAccount } from "@/hooks/useAccount";
import type { UpdateInfo, UpdateStatus } from "@/lib/api";
import { fetchRuntimeHealth, type RuntimeHealth } from "@/lib/api";
import { UpdateProgress } from "@/components/UpdateProgress";
import {
  isReleaseAnnouncement,
  isUpdateToastDismissed,
  readDismissedUpdateToast,
} from "@/lib/product-toasts";
import { cn } from "@/lib/utils";

/**
 * Cartes toast bas-gauche (style Cursor) :
 * - nouvelle version à installer ;
 * - annonces publiées depuis l'admin navin.live ;
 * - pression ressources (RAM / disque) avec Reload.
 *
 * Les annonces sont lues directement depuis /api/announcements (pas via la
 * cloche). Elles restent affichées jusqu'à dismiss explicite, même après
 * reload ou ouverture du panneau Notifications.
 */

type AnnouncementHighlight = {
  title: string;
  body?: string;
};

type AnnouncementToast = {
  id: string;
  title: string;
  detail?: string;
  url?: string;
  kind?: string;
  /** Carte riche : image de couverture + points forts + bouton dédié. */
  imageUrl?: string;
  highlights?: AnnouncementHighlight[];
  ctaLabel?: string;
};

/** Icônes des points forts, en boucle dans l'ordre de la liste. */
const HIGHLIGHT_ICONS = [Sparkles, Zap, ShieldCheck, Star] as const;

function isRichAnnouncement(item: AnnouncementToast): boolean {
  return Boolean(item.imageUrl || item.highlights?.length);
}

const DISMISSED_ANNOUNCEMENTS_KEY = "navin.toasts.dismissed-announcements";
const TOAST_SEEDED_KEY = "navin.toasts.announcements-seeded";
const HEALTH_POLL_MS = 45_000;
const ANNOUNCEMENTS_POLL_MS = 60_000;
const ANNOUNCEMENTS_FETCH_TIMEOUT_MS = 10_000;
const MAX_TOASTS = 2;
const DEFAULT_SITE_URL = "https://navin.live";

function readDismissed(): Set<string> {
  try {
    const raw = window.localStorage.getItem(DISMISSED_ANNOUNCEMENTS_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return new Set(Array.isArray(parsed) ? parsed.filter((v) => typeof v === "string") : []);
  } catch {
    return new Set();
  }
}

function writeDismissed(ids: Set<string>) {
  try {
    window.localStorage.setItem(
      DISMISSED_ANNOUNCEMENTS_KEY,
      JSON.stringify([...ids].slice(-100)),
    );
  } catch {
    // ignore
  }
}

const cardEnter =
  "animate-in fade-in-0 slide-in-from-left-4 slide-in-from-bottom-2 duration-300 motion-reduce:animate-none";

export function ProductToastStack({
  availableUpdate,
  updateBusy,
  updateError,
  updateStatus,
  onInstallUpdate,
  onDismissUpdate,
  onSkipUpdate,
  onOpenAnnouncement,
  onReload,
  token,
}: {
  availableUpdate: UpdateInfo | null;
  updateBusy: boolean;
  updateError?: string | null;
  updateStatus?: UpdateStatus | null;
  onInstallUpdate: () => void;
  onDismissUpdate: () => void;
  onSkipUpdate: () => void;
  onOpenAnnouncement: (item: AnnouncementToast) => void | Promise<void>;
  onReload: () => void;
  token: string;
}) {
  const { t } = useTranslation();
  const { account } = useAccount();
  const siteUrl = (account?.server_url || DEFAULT_SITE_URL).replace(/\/+$/, "");

  const [announcementToasts, setAnnouncementToasts] = useState<AnnouncementToast[]>([]);
  const [health, setHealth] = useState<RuntimeHealth | null>(null);
  const [healthDismissed, setHealthDismissed] = useState(false);
  const [openingId, setOpeningId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const payload = await fetchRuntimeHealth(token);
        if (!cancelled) {
          setHealth(payload);
          if (!payload.pressure) setHealthDismissed(false);
        }
      } catch {
        if (!cancelled) setHealth(null);
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), HEALTH_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [token]);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const response = await fetch(`${siteUrl}/api/announcements`, {
          signal: AbortSignal.timeout(ANNOUNCEMENTS_FETCH_TIMEOUT_MS),
        });
        if (!response.ok || cancelled) return;
        type WireAnnouncement = {
          id?: string;
          kind?: string;
          title?: string;
          body?: string;
          url?: string;
          imageUrl?: string;
          highlights?: Array<{
            title?: string;
            body?: string;
          }>;
          ctaLabel?: string;
        };
        const data = (await response.json()) as {
          announcements?: WireAnnouncement[];
        };
        const list = Array.isArray(data?.announcements) ? data.announcements : [];
        const valid = list.filter(
          (a): a is WireAnnouncement & { id: string; title: string } =>
            !!a && typeof a.id === "string" && typeof a.title === "string",
        );

        // Premier contact toast : ne pas rejouer tout l'historique, mais
        // toujours laisser les 2 plus récentes (sinon une annonce déjà en
        // prod au moment du 1er lancement n'apparaît jamais).
        let dismissed = readDismissed();
        const seeded = window.localStorage.getItem(TOAST_SEEDED_KEY);
        if (seeded === null) {
          const keep = new Set(valid.slice(0, MAX_TOASTS).map((a) => a.id));
          dismissed = new Set(valid.filter((a) => !keep.has(a.id)).map((a) => a.id));
          writeDismissed(dismissed);
          try {
            window.localStorage.setItem(TOAST_SEEDED_KEY, "1");
          } catch {
            // ignore
          }
        }

        const toasts: AnnouncementToast[] = [];
        for (const item of valid) {
          if (dismissed.has(item.id)) continue;
          const highlights = Array.isArray(item.highlights)
            ? item.highlights
                .filter((h) => h && typeof h.title === "string" && h.title.trim())
                .slice(0, 6)
                .map((h) => ({
                  title: h.title as string,
                  body: h.body,
                }))
            : undefined;
          toasts.push({
            id: item.id,
            title: item.title,
            detail: item.body,
            url: item.url,
            kind: item.kind,
            imageUrl:
              typeof item.imageUrl === "string" && /^https:\/\//i.test(item.imageUrl)
                ? item.imageUrl
                : undefined,
            highlights: highlights?.length ? highlights : undefined,
            ctaLabel: item.ctaLabel || undefined,
          });
          if (toasts.length >= MAX_TOASTS) break;
        }
        if (!cancelled) setAnnouncementToasts(toasts);
      } catch {
        // offline: keep last known toasts
      }
    };

    void poll();
    const timer = window.setInterval(() => void poll(), ANNOUNCEMENTS_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [siteUrl]);

  const dismissAnnouncement = useCallback((id: string) => {
    const next = readDismissed();
    next.add(id);
    writeDismissed(next);
    setAnnouncementToasts((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const updateToast =
    availableUpdate
    && !isUpdateToastDismissed(
      availableUpdate.latestVersion,
      readDismissedUpdateToast(),
    )
      ? availableUpdate
      : null;

  const visibleAnnouncements = announcementToasts.filter((item) => {
    // The update card already says the same thing with Install Now. A second
    // NEWS / Open toast for the same version is what people click, and it used
    // to do nothing in the desktop WebView.
    if (updateToast && isReleaseAnnouncement(item)) return false;
    return true;
  });

  const showHealth =
    !!health?.pressure && !healthDismissed && !updateToast;

  if (!updateToast && visibleAnnouncements.length === 0 && !showHealth) {
    return null;
  }

  return (
    <div className="pointer-events-none fixed bottom-5 left-5 z-50 flex w-[min(22.5rem,calc(100vw-2.5rem))] flex-col gap-2.5">
      {updateToast ? (
        <div
          key={updateToast.latestVersion || "update"}
          role="status"
          className={cn(
            "pointer-events-auto overflow-hidden rounded-2xl border border-border/60 bg-popover/95 shadow-[0_12px_40px_-12px_rgba(0,0,0,0.45)] backdrop-blur-md",
            cardEnter,
          )}
        >
          <div className="flex items-start gap-3 px-4 pt-4">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-sky-500/15 text-sky-400">
              <Gift className="h-4 w-4" aria-hidden />
            </div>
            <div className="min-w-0 flex-1 pt-0.5">
              <p className="text-[11px] font-medium uppercase tracking-wide text-sky-400/90">
                {t("updates.badge", { defaultValue: "Update" })}
              </p>
              <p className="mt-0.5 text-[14px] font-semibold leading-snug text-foreground">
                {t("updates.cardTitle", {
                  defaultValue: "Navin {{version}} is available",
                  version: updateToast.latestVersion,
                })}
              </p>
              {updateToast.supported === false && updateToast.reason ? (
                <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted-foreground">
                  {updateToast.reason}
                </p>
              ) : updateToast.notes?.trim() ? (
                <p className="mt-1.5 line-clamp-3 text-[12.5px] leading-relaxed text-muted-foreground">
                  {updateToast.notes.trim()}
                </p>
              ) : (
                <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted-foreground">
                  {t("updates.cardBody", {
                    defaultValue: "A new version is ready. Install now to get the latest fixes and features.",
                  })}
                </p>
              )}
              {updateError ? (
                <p className="mt-1.5 text-[12.5px] leading-relaxed text-red-400">
                  {t("updates.installFailed", {
                    defaultValue: "Install failed: {{error}}",
                    error: updateError,
                  })}
                </p>
              ) : null}
              {updateBusy ? <UpdateProgress status={updateStatus ?? null} /> : null}
            </div>
            <button
              type="button"
              onClick={onDismissUpdate}
              disabled={updateBusy}
              className="rounded-md p-1 text-muted-foreground/70 transition-colors hover:bg-muted hover:text-foreground"
              aria-label={t("common.dismiss", { defaultValue: "Dismiss" })}
            >
              <X className="h-3.5 w-3.5" strokeWidth={2} />
            </button>
          </div>
          <div className="mt-3 flex items-center justify-end gap-2 border-t border-border/50 px-4 py-3">
            <button
              type="button"
              onClick={onDismissUpdate}
              disabled={updateBusy}
              className="rounded-lg px-3 py-1.5 text-[12.5px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              {t("updates.later", { defaultValue: "Later" })}
            </button>
            {updateToast.supported ? (
              <button
                type="button"
                onClick={onInstallUpdate}
                disabled={updateBusy}
                className="inline-flex items-center gap-1.5 rounded-lg bg-sky-400 px-3.5 py-1.5 text-[12.5px] font-semibold text-slate-950 transition-opacity disabled:opacity-60"
              >
                <Sparkles className="h-3.5 w-3.5" aria-hidden />
                {updateBusy
                  ? t("updates.installing", { defaultValue: "Installing…" })
                  : updateError
                    ? t("updates.retry", { defaultValue: "Retry" })
                    : t("updates.installNow", { defaultValue: "Install Now" })}
              </button>
            ) : (
              <button
                type="button"
                onClick={onSkipUpdate}
                className="rounded-lg bg-muted px-3.5 py-1.5 text-[12.5px] font-semibold text-foreground"
              >
                {t("updates.skip", { defaultValue: "Skip" })}
              </button>
            )}
          </div>
        </div>
      ) : null}

      {visibleAnnouncements.map((item, index) => {
        const release = isReleaseAnnouncement(item);
        const busy = release ? updateBusy : openingId === item.id;
        if (!release && isRichAnnouncement(item)) {
          // Carte d'annonce riche (nouvelle version, nouveau modèle...) :
          // image de couverture, points forts et bouton dédié, façon
          // annonce produit - au lieu du toast texte minimal.
          return (
            <div
              key={item.id}
              role="status"
              style={{ animationDelay: `${80 + index * 60}ms` }}
              className={cn(
                "pointer-events-auto overflow-hidden rounded-2xl border border-border/60 bg-popover/95 shadow-[0_16px_48px_-12px_rgba(0,0,0,0.55)] backdrop-blur-md",
                cardEnter,
              )}
            >
              <div className="relative">
                {item.imageUrl ? (
                  <img
                    src={item.imageUrl}
                    alt=""
                    loading="lazy"
                    draggable={false}
                    className="h-40 w-full select-none object-cover"
                    onError={(event) => {
                      // Image morte : on garde la carte, sans bandeau cassé.
                      event.currentTarget.style.display = "none";
                    }}
                  />
                ) : null}
                <button
                  type="button"
                  onClick={() => dismissAnnouncement(item.id)}
                  className={cn(
                    "absolute right-2.5 top-2.5 rounded-full p-1.5 transition-colors",
                    item.imageUrl
                      ? "bg-black/45 text-white/90 hover:bg-black/65"
                      : "text-muted-foreground/70 hover:bg-muted hover:text-foreground",
                  )}
                  aria-label={t("common.dismiss", { defaultValue: "Dismiss" })}
                >
                  <X className="h-3.5 w-3.5" strokeWidth={2.25} />
                </button>
              </div>
              <div className="px-4 pt-3.5">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-violet-400/90">
                  {t("notifications.availableNow", { defaultValue: "Available now" })}
                </p>
                <p className="mt-1 text-[16px] font-semibold leading-snug tracking-[-0.01em] text-foreground">
                  {item.title}
                </p>
                {item.detail ? (
                  <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted-foreground">
                    {item.detail}
                  </p>
                ) : null}
                {item.highlights?.length ? (
                  <ul className="mt-3.5 space-y-3">
                    {item.highlights.map((highlight, i) => {
                      const Icon = HIGHLIGHT_ICONS[i % HIGHLIGHT_ICONS.length];
                      return (
                        <li key={i} className="flex items-start gap-2.5">
                          <span className="mt-px grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-violet-500/12 text-violet-400">
                            <Icon className="h-3.5 w-3.5" aria-hidden />
                          </span>
                          <span className="min-w-0">
                            <span className="block text-[12.5px] font-semibold leading-5 text-foreground">
                              {highlight.title}
                            </span>
                            {highlight.body ? (
                              <span className="block text-[12px] leading-5 text-muted-foreground">
                                {highlight.body}
                              </span>
                            ) : null}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
              </div>
              <div className="px-4 pb-4 pt-3.5">
                {item.url ? (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setOpeningId(item.id);
                      void Promise.resolve(onOpenAnnouncement(item)).finally(() => {
                        setOpeningId(null);
                      });
                    }}
                    className="inline-flex w-full items-center justify-center gap-1.5 rounded-xl bg-foreground py-2 text-[13px] font-semibold text-background transition-opacity hover:opacity-90 disabled:opacity-60"
                  >
                    {busy
                      ? t("notifications.openingLink", { defaultValue: "Opening…" })
                      : item.ctaLabel
                        || t("notifications.discover", { defaultValue: "Discover" })}
                    <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => dismissAnnouncement(item.id)}
                    className="inline-flex w-full items-center justify-center rounded-xl bg-muted py-2 text-[13px] font-semibold text-foreground transition-colors hover:bg-muted/80"
                  >
                    {item.ctaLabel
                      || t("notifications.gotIt", { defaultValue: "Got it" })}
                  </button>
                )}
              </div>
            </div>
          );
        }
        return (
        <div
          key={item.id}
          role="status"
          style={{ animationDelay: `${80 + index * 60}ms` }}
          className={cn(
            "pointer-events-auto overflow-hidden rounded-2xl border border-border/60 bg-popover/95 shadow-[0_12px_40px_-12px_rgba(0,0,0,0.45)] backdrop-blur-md",
            cardEnter,
          )}
        >
          <div className="flex items-start gap-3 px-4 pt-4">
            <div
              className={cn(
                "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl",
                release
                  ? "bg-sky-500/15 text-sky-400"
                  : "bg-violet-500/15 text-violet-400",
              )}
            >
              {release ? (
                <Gift className="h-4 w-4" aria-hidden />
              ) : (
                <Megaphone className="h-4 w-4" aria-hidden />
              )}
            </div>
            <div className="min-w-0 flex-1 pt-0.5">
              <p
                className={cn(
                  "text-[11px] font-medium uppercase tracking-wide",
                  release ? "text-sky-400/90" : "text-violet-400/90",
                )}
              >
                {release
                  ? t("updates.badge", { defaultValue: "Update" })
                  : t("notifications.announcementBadge", { defaultValue: "News" })}
              </p>
              <p className="mt-0.5 text-[14px] font-semibold leading-snug text-foreground">
                {item.title}
              </p>
              {item.detail ? (
                <p className="mt-1.5 line-clamp-3 text-[12.5px] leading-relaxed text-muted-foreground">
                  {item.detail}
                </p>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() => dismissAnnouncement(item.id)}
              className="rounded-md p-1 text-muted-foreground/70 transition-colors hover:bg-muted hover:text-foreground"
              aria-label={t("common.dismiss", { defaultValue: "Dismiss" })}
            >
              <X className="h-3.5 w-3.5" strokeWidth={2} />
            </button>
          </div>
          <div className="mt-3 flex items-center justify-end gap-2 border-t border-border/50 px-4 py-3">
            <button
              type="button"
              onClick={() => dismissAnnouncement(item.id)}
              className="rounded-lg px-3 py-1.5 text-[12.5px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              {t("common.dismiss", { defaultValue: "Dismiss" })}
            </button>
            {item.url || release ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  // Never <a target=_blank>: the desktop WebView drops it.
                  if (!release) setOpeningId(item.id);
                  void Promise.resolve(onOpenAnnouncement(item)).finally(() => {
                    if (!release) setOpeningId(null);
                  });
                }}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-[12.5px] font-semibold transition-opacity hover:opacity-90 disabled:opacity-60",
                  release
                    ? "bg-sky-400 text-slate-950"
                    : "bg-foreground text-background",
                )}
              >
                {release ? (
                  <>
                    <Sparkles className="h-3.5 w-3.5" aria-hidden />
                    {busy
                      ? t("updates.installing", { defaultValue: "Installing…" })
                      : t("updates.installNow", { defaultValue: "Install Now" })}
                  </>
                ) : (
                  <>
                    {busy
                      ? t("notifications.openingLink", { defaultValue: "Opening…" })
                      : t("notifications.openLink", { defaultValue: "Open" })}
                    <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
                  </>
                )}
              </button>
            ) : null}
          </div>
        </div>
        );
      })}

      {showHealth && health ? (
        <div
          role="alert"
          className={cn(
            "pointer-events-auto overflow-hidden rounded-2xl border shadow-[0_12px_40px_-12px_rgba(0,0,0,0.45)] backdrop-blur-md",
            cardEnter,
            health.level === "critical"
              ? "border-amber-500/40 bg-amber-950/95 text-amber-50"
              : "border-border/60 bg-popover/95 text-foreground",
          )}
        >
          <div className="flex items-start gap-3 px-4 pt-4">
            <div
              className={cn(
                "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl",
                health.level === "critical"
                  ? "bg-amber-400/20 text-amber-200"
                  : "bg-amber-500/15 text-amber-500",
              )}
            >
              <AlertTriangle className="h-4 w-4" aria-hidden />
            </div>
            <div className="min-w-0 flex-1 pt-0.5">
              <p
                className={cn(
                  "text-[11px] font-medium uppercase tracking-wide",
                  health.level === "critical" ? "text-amber-200/80" : "text-amber-500/90",
                )}
              >
                {t("runtime.badge", { defaultValue: "Resources" })}
              </p>
              <p className="mt-0.5 text-[13.5px] font-semibold leading-snug">
                {(() => {
                  const reasons = health.reasons ?? [];
                  if (reasons.includes("memory") && reasons.includes("disk")) {
                    return t("runtime.both", {
                      defaultValue: "Low memory and disk space - reload recommended",
                    });
                  }
                  if (reasons.includes("memory")) {
                    return t("runtime.memory", {
                      defaultValue: "High memory use - reload recommended",
                    });
                  }
                  if (reasons.includes("disk")) {
                    return t("runtime.disk", {
                      defaultValue: "Low disk space - free space or reload",
                    });
                  }
                  return (
                    health.message
                    || t("runtime.pressure", {
                      defaultValue: "High resource use - reload recommended",
                    })
                  );
                })()}
              </p>
            </div>
            <button
              type="button"
              onClick={() => setHealthDismissed(true)}
              className="rounded-md p-1 opacity-70 transition-opacity hover:opacity-100"
              aria-label={t("common.dismiss", { defaultValue: "Dismiss" })}
            >
              <X className="h-3.5 w-3.5" strokeWidth={2} />
            </button>
          </div>
          <div className="mt-3 flex items-center justify-end gap-2 border-t border-white/10 px-4 py-3">
            <button
              type="button"
              onClick={() => setHealthDismissed(true)}
              className="rounded-lg px-3 py-1.5 text-[12.5px] font-medium opacity-80 transition-opacity hover:opacity-100"
            >
              {t("updates.later", { defaultValue: "Later" })}
            </button>
            <button
              type="button"
              onClick={onReload}
              className="inline-flex items-center gap-1.5 rounded-lg bg-sky-400 px-3.5 py-1.5 text-[12.5px] font-semibold text-slate-950"
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden />
              {t("runtime.reload", { defaultValue: "Reload" })}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
