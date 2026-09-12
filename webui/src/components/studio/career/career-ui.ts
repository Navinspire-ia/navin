// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { type IButtonStyles, type IContextualMenuProps } from "@fluentui/react";

import { openInOsBrowser } from "@/lib/api";
import { officialCareerHref } from "@/lib/career-api";

export const SPRING = { type: "spring" as const, duration: 0.3, bounce: 0 };
/** Buttons keep their label on one line inside the single-row header and toolbar. */
export const BUTTON_STYLES: IButtonStyles = {
  root: { minHeight: 40, minWidth: 0, padding: "0 12px", cursor: "pointer", flexShrink: 0 },
  label: { whiteSpace: "nowrap" },
};
/** Header actions stay one readable word: do not shrink or clip the label. */
export const HEADER_BUTTON_STYLES: IButtonStyles = {
  root: { minHeight: 40, minWidth: "auto", padding: "0 12px", cursor: "pointer", flexShrink: 0 },
  label: { whiteSpace: "nowrap", overflow: "visible" },
};
export const ICON_BUTTON_STYLES: IButtonStyles = {
  root: {
    width: 40,
    height: 40,
    minWidth: 40,
    minHeight: 40,
    cursor: "pointer",
  },
};
export const SURFACE =
  "rounded-2xl bg-card shadow-[0_10px_28px_rgba(15,23,42,0.07)] outline outline-1 outline-black/10 dark:outline-white/10";
export const CAREER_HASH = "#/career";
export const TOOLS_HASH = "#/tools";

export type CareerDeskPane = "home" | "offers";
export type CareerShellView = "work" | "setup";

/** Same gate as Tenders: wizard until complete, then the desk. Setup can be reopened.
 *  If the book already has offers, the dashboard can open before the wizard is done. */
export function careerShowsWizard(input: {
  deskReady: boolean;
  wizardComplete: boolean;
  view: CareerShellView;
  hasOffers?: boolean;
}): boolean {
  if (!input.deskReady) return false;
  if (input.view === "setup") return true;
  if (input.wizardComplete) return false;
  if (input.hasOffers) return false;
  return true;
}

export function careerShowsDesk(input: {
  deskReady: boolean;
  wizardComplete: boolean;
  view: CareerShellView;
  hasOffers?: boolean;
}): boolean {
  if (!input.deskReady || input.view !== "work") return false;
  return input.wizardComplete || Boolean(input.hasOffers);
}

export function careerDeskHash(input: { pane?: CareerDeskPane; job?: string } = {}): string {
  const params = new URLSearchParams();
  const job = (input.job || "").trim();
  if (job) params.set("job", job);
  else if (input.pane === "offers") params.set("pane", "offers");
  const query = params.toString();
  return query ? `${CAREER_HASH}?${query}` : CAREER_HASH;
}

export function careerHashQuery(hash = typeof window === "undefined" ? "" : window.location.hash): URLSearchParams {
  const cut = hash.indexOf("?");
  return new URLSearchParams(cut >= 0 ? hash.slice(cut + 1) : "");
}

export function careerHashPane(hash?: string): CareerDeskPane {
  const params = careerHashQuery(hash);
  const pane = params.get("pane") || "";
  if (params.get("job") || pane === "offers" || pane === "discover") return "offers";
  return "home";
}

export function careerHashJob(hash?: string): string {
  return (careerHashQuery(hash).get("job") || "").trim();
}

/** Keep #/career?pane=offers in the address bar without a hashchange remount. */
export function replaceCareerHash(hash: string): void {
  if (typeof window === "undefined") return;
  const next = hash.startsWith("#") ? hash : `#${hash}`;
  if (window.location.hash === next) return;
  window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${next}`);
}

/** Fluent menus inside overflow-auto. Same lock as Tenders on Tauri Linux. */
export const OFFER_ROW_MENU: Pick<IContextualMenuProps, "shouldFocusOnMount" | "calloutProps"> = {
  shouldFocusOnMount: true,
  calloutProps: {
    preventDismissOnScroll: true,
    preventDismissOnResize: true,
    isBeakVisible: false,
  },
};

/** Hash routes stay in the Tauri WebView. Never hand these to the OS opener. */
export function openInIdeHash(hash: string): void {
  if (typeof window === "undefined") return;
  const next = hash.startsWith("#") ? hash : `#${hash}`;
  window.location.hash = next;
}

export function openToolsHash(): void {
  openInIdeHash(TOOLS_HASH);
}

/** LinkedIn, Remotive, ATS and catalog boards leave the IDE on every OS. */
export function openOfficialCareerUrl(token: string, href: string): void {
  const official = officialCareerHref(href);
  if (!official) return;
  void openInOsBrowser(token, official);
}

export type Tx = (key: string, fallback: string, values?: Record<string, string | number>) => string;

/** Same ISO list as `navin.career.sources.MARKETS`. Extra countries stay addable. */
export const MARKET_PRESETS = [
  "FR",
  "BE",
  "CH",
  "GB",
  "US",
  "CA",
  "DE",
  "NL",
  "AE",
  "SA",
  "QA",
  "KW",
  "OM",
  "BH",
  "MA",
  "TN",
  "ES",
  "IT",
  "PT",
  "IE",
  "SE",
  "PL",
] as const;

/** Search starts here when the profile is empty or too narrow. */
export const DEFAULT_SEARCH_COUNTRIES = [
  "AE",
  "QA",
  "SA",
  "OM",
  "BH",
  "KW",
  "MA",
  "TN",
  "FR",
  "DE",
  "GB",
  "ES",
  "IT",
  "NL",
  "BE",
  "CH",
] as const;

const MARKET_SET = new Set<string>(MARKET_PRESETS);

export function expandSearchCountries(
  selected: string[] = [],
  excluded: string[] = [],
  extra: string[] = [],
): string[] {
  const blocked = new Set(excluded.map((item) => item.trim().toUpperCase()).filter(Boolean));
  const out: string[] = [];
  const seen = new Set<string>();
  const push = (iso: string) => {
    const key = iso.trim().toUpperCase();
    if (key.length !== 2 || seen.has(key) || blocked.has(key) || !MARKET_SET.has(key)) return;
    seen.add(key);
    out.push(key);
  };
  for (const item of [...selected, ...extra]) push(item);
  for (const item of DEFAULT_SEARCH_COUNTRIES) push(item);
  return out;
}

export function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

/** Wizard sentinel: every live family is off. Keyed boards stay as-is. */
export const CAREER_LIVE_OFF = "live-off";

export const CAREER_API_CONNECTORS = [
  {
    id: "adzuna",
    env: ["ADZUNA_APP_ID", "ADZUNA_APP_KEY"] as const,
    docs: "https://developer.adzuna.com/",
  },
  {
    id: "jooble",
    env: ["JOOBLE_API_KEY"] as const,
    docs: "https://jooble.org/api/about",
  },
  {
    id: "usajobs",
    env: ["USAJOBS_API_KEY", "USAJOBS_USER_AGENT"] as const,
    docs: "https://developer.usajobs.gov/",
  },
] as const;

export const CAREER_API_SOURCE_IDS = CAREER_API_CONNECTORS.map((row) => row.id);

export function careerLiveSourceOn(sourceIds: string[], liveIds: string[], rowId: string): boolean {
  const liveOff = sourceIds.includes(CAREER_LIVE_OFF);
  const livePicked = sourceIds.filter((id) => liveIds.includes(id));
  const allLiveOn = !sourceIds.length || (!livePicked.length && !liveOff);
  return allLiveOn || sourceIds.includes(rowId);
}

export function toggleCareerLiveSource(current: string[], liveIds: string[], rowId: string): string[] {
  const keyed = current.filter((id) => !liveIds.includes(id) && id !== CAREER_LIVE_OFF);
  const active = current.filter((id) => liveIds.includes(id));
  const liveOff = current.includes(CAREER_LIVE_OFF);
  const base = active.length ? active : liveOff ? [] : liveIds;
  const next = base.includes(rowId) ? base.filter((id) => id !== rowId) : [...base, rowId];
  return [...keyed, ...(next.length ? next : [CAREER_LIVE_OFF])];
}
