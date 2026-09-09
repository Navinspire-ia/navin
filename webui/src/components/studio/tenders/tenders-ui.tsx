// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useMemo, type ReactNode } from "react";
import { createTheme, type IButtonStyles, type IContextualMenuProps } from "@fluentui/react";

import { openInOsBrowser } from "@/lib/api";
import { officialTenderHref, type TenderSource } from "@/lib/tenders-api";
import { cn } from "@/lib/utils";

export const SPRING = { type: "spring" as const, duration: 0.3, bounce: 0 };

export const BUTTON_STYLES = {
  root: { minHeight: 44, minWidth: 0, maxWidth: "100%", cursor: "pointer" as const },
};
export const ICON_BUTTON_STYLES: IButtonStyles = {
  root: {
    height: 44,
    minHeight: 44,
    width: 44,
    minWidth: 44,
    cursor: "pointer",
  },
};
export const FILL_BUTTON_STYLES: IButtonStyles = {
  root: {
    height: "auto",
    minHeight: 44,
    minWidth: 0,
    maxWidth: "100%",
    width: "100%",
    paddingTop: 8,
    paddingBottom: 8,
    paddingLeft: 12,
    paddingRight: 12,
    cursor: "pointer",
  },
  flexContainer: {
    minWidth: 0,
    maxWidth: "100%",
    width: "100%",
    justifyContent: "center",
    flexWrap: "wrap",
  },
  textContainer: {
    minWidth: 0,
    maxWidth: "100%",
    flex: "1 1 auto",
  },
  label: {
    display: "block",
    whiteSpace: "normal",
    lineHeight: 1.25,
    textAlign: "center",
  },
};
export const HERO_ACTIONS_CLASS =
  "grid min-w-0 grid-cols-[repeat(auto-fit,minmax(min(100%,16rem),1fr))] gap-3";
export const DASH_TILE_CLASS =
  "flex min-h-20 w-full min-w-0 cursor-pointer flex-col justify-center rounded-2xl px-4 py-3 text-left shadow-[0_10px_28px_rgba(15,23,42,0.07)] outline outline-1 outline-black/10 transition-[transform,background-color] duration-150 hover:bg-muted/30 active:scale-[0.96] dark:outline-white/10";
export const DASH_TILE_GRID_CLASS = "grid min-w-0 grid-cols-2 gap-3 xl:grid-cols-4";

/**
 * Fluent menus inside `overflow-auto`. WebKitGTK (Tauri Linux) fires a scroll
 * when the Layer mounts; without this the row options dismiss before a click.
 */
export function lastUnsentMail<T extends { sent?: boolean }>(mail: T[] | undefined): T | undefined {
  return [...(mail || [])].reverse().find((row) => !row.sent);
}

export function lastSentMail<T extends { sent?: boolean }>(mail: T[] | undefined): T | undefined {
  return [...(mail || [])].reverse().find((row) => Boolean(row.sent));
}

export const NOTICE_ROW_MENU: Pick<IContextualMenuProps, "shouldFocusOnMount" | "calloutProps"> = {
  shouldFocusOnMount: true,
  calloutProps: {
    preventDismissOnScroll: true,
    preventDismissOnResize: true,
    isBeakVisible: false,
  },
};

/** Fluent Dropdown Layer: same WebKitGTK scroll-trap as notice row menus. */
export const FILTER_CALLOUT = NOTICE_ROW_MENU.calloutProps;

export const SURFACE =
  "min-w-0 rounded-2xl shadow-[0_10px_28px_rgba(15,23,42,0.07)] outline outline-1 outline-black/10 dark:outline-white/10";

export const CHANNELS_HASH = "#/settings?section=tools";
export const MODELS_HASH = "#/settings?section=models";

/** Hash routes stay in the Tauri WebView. Never hand these to the OS opener. */
export function openInIdeHash(hash: string): void {
  if (typeof window === "undefined") return;
  const next = hash.startsWith("#") ? hash : `#${hash}`;
  window.location.hash = next;
}

export type Tx = (key: string, fallback: string, values?: Record<string, string | number>) => string;

const CHAPTER_TITLES: Record<string, Record<string, string>> = {
  fr: {
    cover: "Page de garde",
    toc: "Sommaire",
    letter: "Lettre de candidature",
    summary: "Resume executif",
    company: "Presentation de la societe",
    need: "Comprehension du besoin",
    approach: "Demarche",
    vision: "Vision",
    functional: "Reponse fonctionnelle",
    architecture: "Reponse technique",
    methodology: "Methodologie",
    followupKpi: "Suivi et indicateurs",
    raciRisks: "RACI et risques",
    governance: "Gouvernance projet",
    planning: "Planning et jalons",
    staffing: "Equipe",
    references: "References",
    matrix: "Matrice de conformite",
    financial: "Budget et bordereau",
    revisionNotes: "Vos remarques",
  },
  en: {
    cover: "Cover page",
    toc: "Contents",
    letter: "Submission letter",
    summary: "Executive summary",
    company: "Company presentation",
    need: "Understanding of the need",
    approach: "Approach",
    vision: "Vision",
    functional: "Functional response",
    architecture: "Technical response",
    methodology: "Methodology",
    followupKpi: "Follow-up and KPIs",
    raciRisks: "RACI and risks",
    governance: "Project governance",
    planning: "Schedule and milestones",
    staffing: "Team",
    references: "References",
    matrix: "Compliance matrix",
    financial: "Budget and price schedule",
    revisionNotes: "Your remarks",
  },
};

export function chapterTitle(lang: string | undefined, key: string, fallback: string): string {
  const pack = String(lang || "").toLowerCase().startsWith("fr") ? CHAPTER_TITLES.fr : CHAPTER_TITLES.en;
  return pack[key] || fallback;
}

export function Surface({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn(SURFACE, className)}>{children}</div>;
}

export function useTendersTheme(mode: "light" | "dark") {
  return useMemo(() => createTheme({
    palette:
      mode === "dark"
        ? {
            themePrimary: "#4ADE80",
            themeLighterAlt: "#0B1C1A",
            themeLighter: "#134E4A",
            themeLight: "#0F766E",
            themeTertiary: "#14B8A6",
            themeSecondary: "#2DD4BF",
            themeDarkAlt: "#5EEAD4",
            themeDark: "#99F6E4",
            themeDarker: "#CCFBF1",
            neutralLighterAlt: "#0F172A",
            neutralLighter: "#172033",
            neutralLight: "#1E293B",
            neutralQuaternaryAlt: "#273449",
            neutralQuaternary: "#334155",
            neutralTertiaryAlt: "#475569",
            neutralTertiary: "#94A3B8",
            neutralSecondary: "#CBD5E1",
            neutralPrimaryAlt: "#E2E8F0",
            neutralPrimary: "#F8FAFC",
            neutralDark: "#F8FAFC",
            black: "#F8FAFC",
            white: "#0F172A",
          }
        : {
            themePrimary: "#15803D",
            themeLighterAlt: "#F0FDFA",
            themeLighter: "#CCFBF1",
            themeLight: "#99F6E4",
            themeTertiary: "#2DD4BF",
            themeSecondary: "#14B8A6",
            themeDarkAlt: "#0D9488",
            themeDark: "#0F766E",
            themeDarker: "#115E59",
            neutralLighterAlt: "#F8FAFC",
            neutralLighter: "#F1F5F9",
            neutralLight: "#E2E8F0",
            neutralQuaternaryAlt: "#CBD5E1",
            neutralQuaternary: "#94A3B8",
            neutralTertiaryAlt: "#64748B",
            neutralTertiary: "#475569",
            neutralSecondary: "#334155",
            neutralPrimaryAlt: "#1E293B",
            neutralPrimary: "#0F172A",
            neutralDark: "#020617",
            black: "#020617",
            white: "#FFFFFF",
          },
    defaultFontStyle: { fontFamily: '"DM Sans", Inter, sans-serif' },
  }), [mode]);
}

export function scoreTone(score: number | null | undefined): string {
  if (score == null) return "text-muted-foreground";
  if (score >= 70) return "text-teal-700 dark:text-teal-300";
  if (score <= 40) return "text-red-600 dark:text-red-400";
  return "text-amber-700 dark:text-amber-300";
}

export function asLines(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).filter(Boolean);
  if (typeof value === "string" && value.trim()) return [value];
  return [];
}

export function friendlyError(raw: string, tx: Tx): string {
  const text = raw.trim();
  if (!text) return tx("actionError", "Action failed.");
  if (/API route not found/i.test(text) || /404/.test(text)) {
    return tx(
      "offlineBody",
      "The Tenders desk is not connected. Retry, or restart the Navin gateway on this project.",
    );
  }
  return text;
}

export function coverageLabel(row: TenderSource, tx: Tx): string {
  const coverage = row.coverage || row.ingest;
  if (coverage === "api") return tx("coverageApi", "live API");
  if (coverage === "opendata") return tx("coverageOpen", "official open data");
  if (coverage === "covered") {
    return tx("coverageCovered", "covered by {{src}}", { src: row.covered_by || "TED" });
  }
  if (coverage === "key") return tx("coverageKey", "API key required");
  return tx("coverageSearch", "scrape + web search");
}

export function accessLabel(row: TenderSource, tx: Tx): string {
  const access = row.access || row.coverage || row.ingest;
  if (access === "api") return tx("accessApi", "API");
  if (access === "opendata") return tx("accessOpen", "Open Data");
  if (access === "api_key" || access === "key") return tx("accessKey", "API + key");
  if (access === "covered") {
    return tx("accessCovered", "TED");
  }
  return tx("accessScrape", "Scraping");
}

export function OfficialLink({
  href,
  token,
  className,
  children,
}: {
  href: string;
  token: string;
  className?: string;
  children: ReactNode;
}) {
  const official = officialTenderHref(href);
  if (!official) return null;
  return (
    <a
      href={official}
      data-open-url={official}
      className={className}
      onClick={(event) => {
        event.preventDefault();
        void openInOsBrowser(token, official);
      }}
    >
      {children}
    </a>
  );
}

export function TagList({
  values,
  onRemove,
}: {
  values: string[];
  onRemove: (value: string) => void;
}) {
  if (!values.length) return null;
  return (
    <ul className="flex flex-wrap gap-2">
      {values.map((value) => (
        <li key={value}>
          <button
            type="button"
            onClick={() => onRemove(value)}
            className="min-h-10 cursor-pointer rounded-full bg-muted/60 px-3 text-[13px] transition-[transform,background-color] duration-150 hover:bg-muted active:scale-[0.96]"
          >
            {value} ×
          </button>
        </li>
      ))}
    </ul>
  );
}
