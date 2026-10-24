// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import i18n from "i18next";

import arPhrases from "./locales/ar/phrases.json";
import { normalizeLocale, type SupportedLocale } from "./config";

/**
 * Phrasebook for copy that has no translation key of its own: two-language
 * helpers (`c("français", "english")`), `t(key, { defaultValue })` calls
 * whose key never made it into en.json, and catalog text served by the
 * gateway (skill, MCP preset and app template descriptions). Entries are
 * keyed by the English text, gettext style, so a string that changes
 * upstream falls back to English instead of showing a stale translation.
 */
const PHRASEBOOKS: Partial<Record<SupportedLocale, Readonly<Record<string, string>>>> = {
  ar: arPhrases,
};

type MissingPhraseLog = Set<string>;

declare global {
  // Dev-only: the English strings the active phrasebook could not answer.
  var __navinMissingPhrases: MissingPhraseLog | undefined;
}

function activeLocale(locale?: string): SupportedLocale {
  return normalizeLocale(locale ?? i18n.resolvedLanguage ?? i18n.language);
}

function noteMissing(english: string): void {
  if (!import.meta.env.DEV) return;
  (globalThis.__navinMissingPhrases ??= new Set()).add(english);
}

function lookup(english: string, locale: SupportedLocale): string | undefined {
  const book = PHRASEBOOKS[locale];
  if (!book) return undefined;
  const hit = book[english] ?? book[english.trim()];
  if (hit === undefined) noteMissing(english);
  return hit;
}

/** `english` in the active language when the phrasebook knows it. */
export function phrase(english: string, locale?: string): string {
  if (!english) return english;
  return lookup(english, activeLocale(locale)) ?? english;
}

/** Replace `{{name}}` placeholders, the same syntax as the JSON resources. */
export function fillPlaceholders(
  text: string,
  values?: Readonly<Record<string, string | number>>,
): string {
  if (!values) return text;
  return text.replace(/\{\{\s*([\w.]+)\s*\}\}/g, (match, name: string) =>
    name in values ? String(values[name]) : match,
  );
}

/**
 * For the two-language helpers: French keeps its own copy, every other
 * language goes through the phrasebook with English as the fallback.
 */
export function bilingual(
  french: string,
  english: string,
  values?: Readonly<Record<string, string | number>>,
  locale?: string,
): string {
  const active = activeLocale(locale);
  return fillPlaceholders(active === "fr" ? french : phrase(english, active), values);
}

/**
 * i18next `parseMissingKeyHandler`: a key absent from every resource falls
 * back to its English `defaultValue`, already interpolated. Look the raw
 * template up instead and interpolate the translation with the same values.
 */
export function translateMissingDefault(
  key: string,
  interpolated: string | undefined,
  options?: Record<string, unknown>,
): string {
  if (interpolated === undefined) return key;
  const locale = activeLocale(typeof options?.lng === "string" ? options.lng : undefined);
  if (!PHRASEBOOKS[locale] || !options) return interpolated;
  const raw = rawDefaultValue(options, locale);
  if (!raw) return interpolated;
  const translated = lookup(raw, locale);
  if (translated === undefined) return interpolated;
  const interpolator = i18n.services?.interpolator;
  return interpolator
    ? interpolator.interpolate(translated, options, locale, options)
    : fillPlaceholders(translated, options as Record<string, string | number>);
}

function rawDefaultValue(options: Record<string, unknown>, locale: SupportedLocale): string | undefined {
  // i18next prefers `defaultValue_<plural suffix>` when a count is passed.
  if (typeof options.count === "number") {
    const suffix = i18n.services?.pluralResolver?.getSuffix(locale, options.count, options);
    const plural = suffix ? options[`defaultValue${suffix}`] : undefined;
    if (typeof plural === "string") return plural;
  }
  return typeof options.defaultValue === "string" ? options.defaultValue : undefined;
}
