// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import i18n from "i18next";

import { normalizeLocale, type SupportedLocale } from "./config";

/**
 * Intl tags per app language. Arabic keeps Latin digits: the rest of the UI
 * (translations, timers, code, logs) writes numbers that way, and mixing
 * ١٢ with 12 on one screen reads as two systems.
 */
const INTL_TAGS: Record<SupportedLocale, string> = {
  en: "en-US",
  fr: "fr-FR",
  ar: "ar-u-nu-latn",
};

/**
 * The BCP 47 tag to hand to Intl / toLocale*String for the app language.
 * A regional tag passes through untouched, except Arabic, which always gets
 * Latin digits; an empty value means the active app language.
 */
export function intlLocale(language?: string | readonly string[] | null): string {
  const value = Array.isArray(language) ? language[0] : language;
  const raw =
    typeof value === "string" && value.trim()
      ? value.trim()
      : (i18n.resolvedLanguage ?? i18n.language ?? "en");
  if (raw.includes("-") && !/^ar\b/i.test(raw)) return raw;
  return INTL_TAGS[normalizeLocale(raw)];
}
