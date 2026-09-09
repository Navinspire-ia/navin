// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

export const LOCALE_STORAGE_KEY = "navin.locale";
// Present only when the user picked the language by hand. Without it a stored
// locale cannot be told apart from the old code's habit of writing the
// default back on every load, which is what kept a French Windows in English.
export const LOCALE_CHOICE_KEY = "navin.locale.chosen";

export const supportedLocales = [
  { code: "en", label: "English", nativeLabel: "English" },
  { code: "fr", label: "French", nativeLabel: "Français" },
] as const;

export type SupportedLocale = (typeof supportedLocales)[number]["code"];

export const defaultLocale: SupportedLocale = "en";
export const fallbackLocale: SupportedLocale = "en";

/** The supported locale ``input`` designates, or null when it maps to none. */
export function matchLocale(
  input: string | null | undefined,
): SupportedLocale | null {
  if (!input) return null;
  const trimmed = input.trim();
  if (!trimmed) return null;

  const exact = supportedLocales.find((locale) => locale.code === trimmed);
  if (exact) return exact.code;

  const base = trimmed.toLowerCase().split("-")[0];
  const baseMatch = supportedLocales.find(
    (locale) => locale.code.toLowerCase() === base,
  );
  return baseMatch?.code ?? null;
}

export function normalizeLocale(
  input: string | null | undefined,
): SupportedLocale {
  return matchLocale(input) ?? defaultLocale;
}

export function readStoredLocale(): SupportedLocale | null {
  if (typeof window === "undefined") return null;
  try {
    const stored = matchLocale(window.localStorage.getItem(LOCALE_STORAGE_KEY));
    if (!stored) return null;
    if (window.localStorage.getItem(LOCALE_CHOICE_KEY) === "1") return stored;
    // Legacy entry, written on load rather than on choice: a stored default
    // carries no information, but a stored non-default could only have come
    // from the user actually switching, so it is honoured.
    return stored === defaultLocale ? null : stored;
  } catch {
    return null;
  }
}

/**
 * The language the machine itself is set to, when we can speak it.
 *
 * This is what makes a French Windows open in French on first launch: there
 * is nothing stored yet, and the browser inherits the OS display language.
 */
export function detectSystemLocale(): SupportedLocale | null {
  if (typeof navigator === "undefined") return null;
  for (const candidate of [...(navigator.languages ?? []), navigator.language]) {
    const match = matchLocale(candidate);
    if (match) return match;
  }
  return null;
}

export function resolveInitialLocale(): SupportedLocale {
  return readStoredLocale() ?? detectSystemLocale() ?? defaultLocale;
}

/** Record an explicit language choice; from now on it beats the system's. */
export function persistLocale(locale: SupportedLocale): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
    window.localStorage.setItem(LOCALE_CHOICE_KEY, "1");
  } catch {
    // ignore storage errors
  }
}

export function applyDocumentLocale(locale: SupportedLocale): void {
  if (typeof document === "undefined") return;
  document.documentElement.lang = locale;
}

export function localeOption(locale: SupportedLocale) {
  return supportedLocales.find((entry) => entry.code === locale) ?? supportedLocales[0];
}
