// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import { useAccount } from "@/hooks/useAccount";
import { useNotifications } from "@/providers/NotificationProvider";
import type { UpdateInfo } from "@/lib/api";

/**
 * Product news in the notification center, Cursor-style:
 *
 * - a new Navin version detected by the update checker raises one "update"
 *   notification per version (the install banner stays the primary CTA);
 * - announcements published on navin.live (/api/announcements: new managed
 *   models, releases, news) are polled and raised once per machine.
 *
 * Seen state lives in localStorage so a reload does not re-announce, and the
 * very first poll seeds silently instead of dumping the whole history.
 */

/** Polling court pour que les annonces admin arrivent vite sans spammer. */
const ANNOUNCEMENTS_POLL_MS = 5 * 60 * 1000;
const ANNOUNCEMENTS_FETCH_TIMEOUT_MS = 10_000;
const MAX_NOTIFIED_PER_POLL = 3;
const SEEN_KEY = "navin.announcements.seen";
const NOTIFIED_UPDATE_KEY = "navin.update.notified-version";
const DEFAULT_SITE_URL = "https://navin.live";

/** Outcome of the install that caused the last restart, reported once. */
export interface JustInstalled {
  version: string;
  ok: boolean;
  currentVersion?: string;
}

interface Announcement {
  id: string;
  date?: string;
  kind?: string;
  title: string;
  body?: string;
  url?: string;
  imageUrl?: string;
  highlights?: Array<{
    title?: string;
    body?: string;
  }>;
}

function readSeenIds(): string[] | null {
  try {
    const raw = window.localStorage.getItem(SEEN_KEY);
    if (raw === null) return null;
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((v) => typeof v === "string") : [];
  } catch {
    return [];
  }
}

function writeSeenIds(ids: Iterable<string>) {
  try {
    // Keep the list bounded; old ids never come back anyway.
    window.localStorage.setItem(SEEN_KEY, JSON.stringify([...ids].slice(-200)));
  } catch {
    // Storage unavailable: worst case the same announcement shows again.
  }
}

export function useProductNews(
  availableUpdate: UpdateInfo | null,
  onInstallUpdate?: () => void | Promise<void>,
  justInstalled?: JustInstalled | null,
): void {
  const { t } = useTranslation();
  const { notify } = useNotifications();
  const { account } = useAccount();
  const siteUrl = (account?.server_url || DEFAULT_SITE_URL).replace(/\/+$/, "");

  // -- new version -> one notification per version --------------------------

  // The notification is raised once and then lives for the session, so it must
  // not capture today's handler: a token refresh would leave the button calling
  // a stale one.
  const installRef = useRef(onInstallUpdate);
  installRef.current = onInstallUpdate;

  useEffect(() => {
    const version = availableUpdate?.latestVersion?.trim();
    if (!version || !availableUpdate?.available) return;
    try {
      if (window.localStorage.getItem(NOTIFIED_UPDATE_KEY) === version) return;
      window.localStorage.setItem(NOTIFIED_UPDATE_KEY, version);
    } catch {
      // Without storage, fall back to the merge key to avoid duplicates.
    }
    // An install the app cannot replace on its own (a .deb, an .rpm) still
    // deserves the news, but not a button: one that cannot keep its promise is
    // worse than none.
    const installable = availableUpdate.supported !== false;
    notify({
      level: "info",
      source: "update",
      title: t("notifications.updateAvailable", {
        version,
        defaultValue: "Navin {{version}} is available",
      }),
      detail: installable
        ? availableUpdate?.notes?.trim()
          || t("notifications.updateAvailableDetail", {
            defaultValue: "Click Update: Navin installs it and restarts itself.",
          })
        : availableUpdate?.reason
          || t("notifications.updateManual", {
            defaultValue: "Update it with your package manager.",
          }),
      action: installable
        ? {
            label: t("notifications.updateAction", { defaultValue: "Update" }),
            busyLabel: t("notifications.updateActionBusy", {
              defaultValue: "Installing…",
            }),
            run: () => installRef.current?.(),
          }
        : undefined,
      key: `update:${version}`,
    });
  }, [availableUpdate, notify, t]);

  // -- the restart the user was promised -------------------------------------

  useEffect(() => {
    if (!justInstalled?.version) return;
    if (justInstalled.ok) {
      notify({
        level: "success",
        source: "update",
        title: t("notifications.updateInstalled", {
          version: justInstalled.version,
          defaultValue: "Navin {{version}} is installed",
        }),
        detail: t("notifications.updateInstalledDetail", {
          defaultValue: "The update is done, you are on the latest version.",
        }),
        key: `update:installed:${justInstalled.version}`,
      });
      return;
    }
    // The app came back on the version it started from. Saying so is the whole
    // point of the note: a silent rollback looks exactly like a silent success.
    notify({
      level: "warning",
      source: "update",
      title: t("notifications.updateFailed", {
        version: justInstalled.version,
        defaultValue: "Navin {{version}} was not installed",
      }),
      detail: t("notifications.updateFailedDetail", {
        version: justInstalled.currentVersion ?? "",
        defaultValue: "You are still running {{version}}. Try again from Settings.",
      }),
      key: `update:failed:${justInstalled.version}`,
    });
  }, [justInstalled, notify, t]);

  // -- navin.live announcements ---------------------------------------------

  const pollingRef = useRef(false);
  useEffect(() => {
    if (pollingRef.current) return;
    pollingRef.current = true;
    let cancelled = false;

    const poll = async () => {
      let list: Announcement[];
      try {
        const response = await fetch(`${siteUrl}/api/announcements`, {
          signal: AbortSignal.timeout(ANNOUNCEMENTS_FETCH_TIMEOUT_MS),
        });
        if (!response.ok) return;
        const data = (await response.json()) as { announcements?: unknown };
        list = Array.isArray(data?.announcements)
          ? (data.announcements as Announcement[]).filter(
              (a) => a && typeof a.id === "string" && typeof a.title === "string",
            )
          : [];
      } catch {
        return; // offline or blocked: try again next cycle
      }
      if (cancelled) return;

      const seen = readSeenIds();
      if (seen === null) {
        // First contact ever: seed silently, do not replay the archive.
        writeSeenIds(list.map((a) => a.id));
        return;
      }
      const seenSet = new Set(seen);
      const unseen = list.filter((a) => !seenSet.has(a.id));
      if (!unseen.length) return;
      for (const item of unseen.slice(0, MAX_NOTIFIED_PER_POLL)) {
        // Release notes are the update toast's job. Raising them here as NEWS
        // produced a second card whose Open button did nothing in the WebView.
        if (item.kind === "release" || item.id.startsWith("release-")) continue;
        const title = item.title;
        const body = item.body;
        // Les points forts d'une annonce riche deviennent des lignes de détail
        // dans la cloche : lisible sans la carte visuelle du toast.
        const highlights = (item.highlights ?? [])
          .filter((h) => h && typeof h.title === "string" && h.title.trim())
          .slice(0, 4)
          .map((h) => `- ${h.title}`)
          .join("\n");
        const detail = [body, highlights, item.url]
          .filter(Boolean)
          .join("\n");
        notify({
          level: "info",
          source: "announcement",
          title,
          detail: detail || undefined,
          imageUrl:
            typeof item.imageUrl === "string" && /^https:\/\//i.test(item.imageUrl)
              ? item.imageUrl
              : undefined,
          key: `announcement:${item.id}`,
        });
      }
      writeSeenIds([...seenSet, ...unseen.map((a) => a.id)]);
    };

    void poll();
    const timer = window.setInterval(() => void poll(), ANNOUNCEMENTS_POLL_MS);
    return () => {
      cancelled = true;
      pollingRef.current = false;
      window.clearInterval(timer);
    };
  }, [siteUrl, notify]);
}
