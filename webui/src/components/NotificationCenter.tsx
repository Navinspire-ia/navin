// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  Bell,
  CheckCircle2,
  Download,
  Info,
  Megaphone,
  Plug,
  Server,
  X,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  relativeTimeParts,
  toastable,
  type NotificationEntry,
  type NotificationLevel,
} from "@/lib/notifications";
import { useNotifications } from "@/providers/NotificationProvider";

/**
 * Legacy gutter kept so call sites that still import it compile; the bell now
 * lives in the sidebar next to Settings, so no top-right padding is required.
 */
export const NOTIFICATION_GUTTER = "";

const PANEL_WIDTH = 340;
const PANEL_GAP = 8;

const LEVEL_ICON = {
  info: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  error: XCircle,
} as const satisfies Record<NotificationLevel, typeof Info>;

const LEVEL_TONE: Record<NotificationLevel, string> = {
  info: "text-muted-foreground",
  success: "text-emerald-500",
  warning: "text-amber-500",
  error: "text-red-500",
};

function NotificationRow({
  entry,
  now,
  onDismiss,
}: {
  entry: NotificationEntry;
  now: number;
  onDismiss: () => void;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const age = relativeTimeParts(entry.lastSeenAt, now);
  const Icon =
    entry.source === "connection"
      ? Plug
      : entry.source === "request"
        ? Server
        : entry.source === "update"
          ? Download
          : entry.source === "announcement"
            ? Megaphone
            : LEVEL_ICON[entry.level];
  return (
    <li
      className={cn(
        "group relative flex gap-2.5 border-b border-border/40 px-3 py-2.5 last:border-b-0",
        !entry.read && "bg-muted/40",
      )}
    >
      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", LEVEL_TONE[entry.level])} strokeWidth={1.75} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-1.5">
          <span className="min-w-0 break-words text-[13px] font-medium leading-snug">
            {entry.title}
          </span>
          {entry.repeats > 1 ? (
            <span
              className="shrink-0 rounded-full bg-muted px-1.5 text-[10px] font-semibold text-muted-foreground"
              title={t("notifications.repeated", { count: entry.repeats })}
            >
              ×{entry.repeats}
            </span>
          ) : null}
        </div>
        {entry.detail ? (
          <p className="mt-0.5 whitespace-pre-line break-words text-[12px] leading-snug text-muted-foreground">
            {entry.detail}
          </p>
        ) : null}
        {entry.imageUrl ? (
          <img
            src={entry.imageUrl}
            alt=""
            loading="lazy"
            draggable={false}
            className="mt-1.5 max-h-28 w-full select-none rounded-lg border border-border/50 object-cover"
            onError={(event) => {
              event.currentTarget.style.display = "none";
            }}
          />
        ) : null}
        {entry.action ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              // The row stays put while the work runs: an update takes long
              // enough that a button which does nothing visible reads as broken
              // and gets clicked again.
              setBusy(true);
              void Promise.resolve(entry.action?.run()).finally(() => setBusy(false));
            }}
            className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-primary px-2.5 py-1 text-[11.5px] font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
          >
            {busy && entry.action.busyLabel ? entry.action.busyLabel : entry.action.label}
          </button>
        ) : null}
        <span className="mt-1 block text-[10px] uppercase tracking-wide text-muted-foreground/70">
          {t(`notifications.${age.key}`, { count: age.count })}
        </span>
      </div>
      <button
        type="button"
        aria-label={t("notifications.dismiss")}
        onClick={onDismiss}
        className="absolute right-1.5 top-1.5 rounded p-1 text-muted-foreground/0 transition-colors hover:bg-muted hover:text-foreground group-hover:text-muted-foreground"
      >
        <X className="h-3.5 w-3.5" strokeWidth={2} />
      </button>
    </li>
  );
}

type PanelCoords = { top: number; left: number; maxHeight: number };

/**
 * Place the panel fully on-screen next to the bell. Prefer opening upward and
 * to the right (into the main workspace) so a narrow sidebar never clips it.
 */
function computePanelCoords(anchor: DOMRect): PanelCoords {
  const width = Math.min(PANEL_WIDTH, window.innerWidth - 24);
  const margin = 12;
  let left = anchor.left;
  if (left + width > window.innerWidth - margin) {
    left = Math.max(margin, anchor.right - width);
  }
  if (left < margin) left = margin;

  const spaceAbove = anchor.top - margin;
  const spaceBelow = window.innerHeight - anchor.bottom - margin;
  const openUp = spaceAbove >= 180 || spaceAbove >= spaceBelow;
  const maxHeight = Math.max(160, Math.min(window.innerHeight * 0.7, openUp ? spaceAbove - PANEL_GAP : spaceBelow - PANEL_GAP));

  if (openUp) {
    return {
      top: Math.max(margin, anchor.top - PANEL_GAP - maxHeight),
      left,
      maxHeight,
    };
  }
  return {
    top: anchor.bottom + PANEL_GAP,
    left,
    maxHeight,
  };
}

/**
 * The bell, its panel, and the transient banners for problems.
 *
 * Only warnings and errors ever appear on their own; everything else waits in
 * the panel. A centre that pops up for routine success is one people learn to
 * dismiss without reading, which costs exactly the attention it was built to
 * capture.
 */
export default function NotificationCenter({
  /** Sidebar placement: panel opens next to Settings via a fixed portal. */
  placement = "sidebar",
}: {
  placement?: "sidebar" | "corner";
} = {}) {
  const { t } = useTranslation();
  const { notifications, unreadCount, markAllRead, dismiss, clear } = useNotifications();
  const [open, setOpen] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [coords, setCoords] = useState<PanelCoords | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const sidebar = placement === "sidebar";

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(id);
  }, []);

  const updateCoords = () => {
    const anchor = rootRef.current?.getBoundingClientRect();
    if (!anchor) return;
    setCoords(computePanelCoords(anchor));
  };

  useLayoutEffect(() => {
    if (!open) {
      setCoords(null);
      return;
    }
    updateCoords();
    const onResize = () => updateCoords();
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onResize, true);
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", onResize, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (rootRef.current?.contains(target) || panelRef.current?.contains(target)) {
        return;
      }
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const toasts = useMemo(
    () => (open ? [] : toastable(notifications, now).slice(0, 3)),
    [notifications, now, open],
  );

  const panel = open && coords ? (
    <div
      ref={panelRef}
      role="dialog"
      aria-label={t("notifications.title")}
      className="fixed z-[80] flex w-[min(340px,calc(100vw-1.5rem))] flex-col overflow-hidden rounded-xl border border-border/70 bg-popover shadow-xl"
      style={{
        top: coords.top,
        left: coords.left,
        maxHeight: coords.maxHeight,
      }}
    >
      <div className="flex shrink-0 items-center justify-between border-b border-border/60 px-3 py-2">
        <span className="text-[13px] font-semibold">{t("notifications.title")}</span>
        {notifications.length > 0 ? (
          <button
            type="button"
            onClick={clear}
            className="text-[11px] text-muted-foreground hover:text-foreground"
          >
            {t("notifications.clearAll")}
          </button>
        ) : null}
      </div>
      {notifications.length === 0 ? (
        <p className="px-3 py-8 text-center text-[12px] text-muted-foreground">
          {t("notifications.empty")}
        </p>
      ) : (
        <ul className="min-h-0 flex-1 overflow-y-auto">
          {notifications.map((entry) => (
            <NotificationRow
              key={entry.id}
              entry={entry}
              now={now}
              onDismiss={() => dismiss(entry.id)}
            />
          ))}
        </ul>
      )}
    </div>
  ) : null;

  return (
    <div ref={rootRef} className="relative shrink-0">
      <Button
        type="button"
        variant="ghost"
        size="icon"
        aria-label={t("notifications.open")}
        aria-expanded={open}
        data-testid="notification-bell"
        onClick={() => {
          setOpen((value) => {
            if (!value) markAllRead();
            return !value;
          });
        }}
        className={cn(
          sidebar
            ? "h-8 w-8 rounded-md text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
            : "h-7 w-7 rounded-lg bg-transparent text-muted-foreground/85 shadow-none hover:bg-transparent hover:text-foreground",
        )}
      >
        <Bell className={sidebar ? "h-4 w-4" : "h-[15px] w-[15px]"} strokeWidth={1.75} />
        {unreadCount > 0 ? (
          <span
            aria-hidden
            className="absolute -right-0.5 -top-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-red-500 px-1 text-[9px] font-bold leading-none text-white"
          >
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        ) : null}
      </Button>

      {typeof document !== "undefined" && panel
        ? createPortal(panel, document.body)
        : null}

      {toasts.length > 0 ? (
        <div
          className={cn(
            "z-40 flex w-[320px] flex-col gap-2",
            // Keep transient alerts readable above the workbench, not clipped
            // by the sidebar scroll area.
            sidebar
              ? "fixed right-3 top-[calc(0.75rem+env(safe-area-inset-top))]"
              : "absolute right-0 top-9",
          )}
        >
          {toasts.map((entry) => {
            const Icon = LEVEL_ICON[entry.level];
            return (
              <button
                key={entry.id}
                type="button"
                onClick={() => setOpen(true)}
                className="flex w-full gap-2 rounded-lg border border-border/70 bg-popover px-3 py-2 text-left shadow-lg"
              >
                <Icon
                  className={cn("mt-0.5 h-4 w-4 shrink-0", LEVEL_TONE[entry.level])}
                  strokeWidth={1.75}
                />
                <span className="min-w-0 flex-1">
                  <span className="block break-words text-[12.5px] font-medium leading-snug">
                    {entry.title}
                  </span>
                  {entry.detail ? (
                    <span className="mt-0.5 block break-words text-[11.5px] leading-snug text-muted-foreground">
                      {entry.detail}
                    </span>
                  ) : null}
                </span>
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
