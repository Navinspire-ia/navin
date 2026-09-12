// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { officialLeadHref } from "@/lib/leads-api";
import { openInOsBrowser } from "@/lib/api";
import { type IContextualMenuProps } from "@fluentui/react";

export const SPRING = { type: "spring" as const, duration: 0.3, bounce: 0 };
export const LEADS_PAGE_SIZE = 20;
export const BUTTON_STYLES = {
  root: { minHeight: 40, minWidth: 0, padding: "0 12px", cursor: "pointer" as const, flexShrink: 0 },
  label: { whiteSpace: "nowrap" as const },
};
/** Header actions stay one readable word: do not shrink or clip the label. */
export const HEADER_BUTTON_STYLES = {
  root: { minHeight: 40, minWidth: "auto", padding: "0 12px", cursor: "pointer" as const, flexShrink: 0 },
  label: { whiteSpace: "nowrap" as const, overflow: "visible" as const },
};
export const ICON_BUTTON_STYLES = {
  root: {
    height: 40,
    minHeight: 40,
    width: 40,
    minWidth: 40,
    flexShrink: 0,
    cursor: "pointer" as const,
  },
};
export const ROW_ICON_BUTTON_STYLES = {
  root: { width: 40, height: 40, minWidth: 40, minHeight: 40, flexShrink: 0, cursor: "pointer" as const },
  menuIcon: { display: "none" },
};
/** Fluent menus inside overflow-auto. Same lock as Tenders on Tauri Linux. */
export const LEAD_ROW_MENU: Pick<IContextualMenuProps, "shouldFocusOnMount" | "calloutProps"> = {
  shouldFocusOnMount: true,
  calloutProps: {
    preventDismissOnScroll: true,
    preventDismissOnResize: true,
    isBeakVisible: false,
  },
};
export const SURFACE =
  "rounded-2xl bg-card shadow-[0_10px_28px_rgba(15,23,42,0.07)] outline outline-1 outline-black/10 dark:outline-white/10";

export type Tx = (key: string, fallback: string, values?: Record<string, string | number>) => string;

export function scoreTone(score: number | null | undefined): string {
  if (score == null) return "text-muted-foreground";
  if (score >= 80) return "text-emerald-700 dark:text-emerald-300";
  if (score >= 55) return "text-amber-700 dark:text-amber-300";
  return "text-rose-700 dark:text-rose-300";
}

export function openOfficialLeadUrl(token: string, href: string): void {
  const official = officialLeadHref(href);
  if (official) void openInOsBrowser(token, official);
}
