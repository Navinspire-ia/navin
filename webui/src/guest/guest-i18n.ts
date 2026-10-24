// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * The guest page's stand-in for "@/i18n" (aliased in vite.config.guest.ts):
 * same API, but only the visitor's language is fetched, on demand, instead of
 * every locale baked into the first script.
 */
import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import {
  applyDocumentLocale,
  defaultLocale,
  fallbackLocale,
  LOCALE_STORAGE_KEY,
  normalizeLocale,
  persistLocale,
  resolveInitialLocale,
  type SupportedLocale,
} from "@/i18n/config";

// One chunk per language, fetched only for the visitor's.
const BUNDLES = import.meta.glob<{ default: Record<string, unknown> }>("../i18n/locales/*/common.json");
const LOADERS: Partial<Record<string, () => Promise<{ default: Record<string, unknown> }>>> = Object.fromEntries(
  Object.entries(BUNDLES).map(([path, load]) => [path.split("/").at(-2) ?? "", load]),
);

export function currentLocale(): SupportedLocale {
  return normalizeLocale(i18n.resolvedLanguage ?? i18n.language ?? defaultLocale);
}

async function ensureLocale(locale: SupportedLocale): Promise<void> {
  if (i18n.hasResourceBundle(locale, "common")) return;
  // Languages without a bundle fall back to each call's defaultValue (English).
  const load = LOADERS[locale] ?? LOADERS.en;
  if (!load) return;
  const bundle = await load();
  i18n.addResourceBundle(locale, "common", bundle.default, true, true);
}

export async function setAppLanguage(locale: SupportedLocale): Promise<void> {
  persistLocale(locale);
  await ensureLocale(locale);
  await i18n.changeLanguage(locale);
}

/** Run before the first render: the page never flashes another language. */
export async function loadGuestLocale(): Promise<void> {
  const locale = resolveInitialLocale();
  if (!i18n.isInitialized) {
    await i18n.use(initReactI18next).init({
      resources: {},
      lng: locale,
      fallbackLng: fallbackLocale,
      defaultNS: "common",
      ns: ["common"],
      interpolation: { escapeValue: false },
      returnNull: false,
      supportedLngs: Object.keys(LOADERS).filter(Boolean),
    });
  }
  // Missing keys fall back to each call's defaultValue (English).
  await ensureLocale(locale).catch(() => undefined);
  applyDocumentLocale(locale);
  i18n.on("languageChanged", (language) => applyDocumentLocale(normalizeLocale(language)));
}

export { LOCALE_STORAGE_KEY };
export default i18n;
