// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { SelectableOptionMenuItemType, type IDropdownOption } from "@fluentui/react";
import countries from "world-countries";

import { CRM_COUNTRIES, countryLabel, normalizeCountryValue } from "@/lib/crm-catalog";

const ISO_RE = /^[A-Z]{2}$/;

/** Lowercase, no diacritics, punctuation folded to single spaces. */
export function foldText(text: string): string {
  return String(text || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

type NameEntry = { iso: string; folded: string; tokens: string[] };

/** Every spelling we know for every country: common, official, French, alt spellings, alpha-3. */
const NAMES: NameEntry[] = countries.flatMap((row) => {
  const iso = String(row.cca2 || "").toUpperCase();
  if (iso.length !== 2) return [];
  const variants = [
    row.name?.common,
    row.name?.official,
    row.translations?.fra?.common,
    row.translations?.fra?.official,
    row.cca3,
    ...(row.altSpellings || []),
  ];
  const seen = new Set<string>();
  const out: NameEntry[] = [];
  for (const variant of variants) {
    const folded = foldText(String(variant || ""));
    if (!folded || seen.has(folded)) continue;
    seen.add(folded);
    out.push({ iso, folded, tokens: folded.split(" ") });
  }
  return out;
});

/**
 * Multi-country groupings some portals (World Bank) store in the country field,
 * often cut to 16 characters ("WESTERN AND CENT"). Prefix-matched, then shown in full.
 */
const REGIONS: { en: string; fr: string }[] = [
  { en: "Western and Central Africa", fr: "Afrique de l'Ouest et centrale" },
  { en: "Eastern and Southern Africa", fr: "Afrique de l'Est et australe" },
  { en: "Middle East and North Africa", fr: "Moyen-Orient et Afrique du Nord" },
  { en: "Latin America and Caribbean", fr: "Amérique latine et Caraïbes" },
  { en: "East Asia and Pacific", fr: "Asie de l'Est et Pacifique" },
  { en: "Europe and Central Asia", fr: "Europe et Asie centrale" },
  { en: "South Asia", fr: "Asie du Sud" },
  { en: "Southwest Indian Ocean", fr: "Sud-Ouest de l'océan Indien" },
  { en: "Sub-Saharan Africa", fr: "Afrique subsaharienne" },
  { en: "Africa", fr: "Afrique" },
  { en: "World", fr: "Monde" },
];

function uniqueIso(hits: NameEntry[]): string {
  const isos = new Set(hits.map((hit) => hit.iso));
  return isos.size === 1 ? hits[0].iso : "";
}

/** True when every token of the needle is a token (or a prefix of one) of the name, in order. */
function tokensMatch(needle: string[], name: string[]): boolean {
  let at = 0;
  for (let index = 0; index < needle.length; index += 1) {
    const token = needle[index];
    const last = index === needle.length - 1;
    let found = -1;
    for (let cursor = at; cursor < name.length; cursor += 1) {
      const candidate = name[cursor];
      if (candidate === token || (last && candidate.startsWith(token))) {
        found = cursor;
        break;
      }
    }
    if (found < 0) return false;
    at = found + 1;
  }
  return true;
}

/** ISO code behind a stored country value ("FR", "HAITI", "Congo, Democrati", "UKR"), or "" when it is not a country. */
export function countryIso(code: string): string {
  const raw = String(code || "").trim();
  if (!raw) return "";
  const upper = raw.toUpperCase();
  if (ISO_RE.test(upper)) return upper;
  const normalized = normalizeCountryValue(raw);
  if (ISO_RE.test(normalized) && normalized !== upper) return normalized;
  const folded = foldText(raw);
  if (folded.length < 3) return "";
  const exact = NAMES.filter((entry) => entry.folded === folded);
  if (exact.length) return uniqueIso(exact);
  if (folded.length < 5) return "";
  // Truncated names ("CENTRAL AFRICAN", "CONGO, DEMOCRATI") match one country by prefix or token run.
  const prefix = NAMES.filter((entry) => entry.folded.startsWith(folded));
  if (prefix.length) return uniqueIso(prefix);
  const tokens = folded.split(" ");
  if (tokens.length < 2) return "";
  const loose = NAMES.filter((entry) => tokensMatch(tokens, entry.tokens));
  return loose.length ? uniqueIso(loose) : "";
}

function regionName(code: string, locale: string): string {
  const folded = foldText(code);
  if (folded.length < 5) return "";
  const hit = REGIONS.find((region) => foldText(region.en).startsWith(folded) || foldText(region.fr).startsWith(folded));
  if (!hit) return "";
  return locale.toLowerCase().startsWith("en") ? hit.en : hit.fr;
}

function sentenceCase(text: string): string {
  const lower = text.toLowerCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

/**
 * Full country name for a stored value. Regions read in full, shouting portal values
 * ("KYRGYZ REPUBLIC") calm down to sentence case, short codes (INTL, REMOTE) stay as they are.
 */
export function countryDisplayName(code: string, locale: string): string {
  const raw = String(code || "").trim();
  const iso = countryIso(raw);
  if (iso) return countryLabel(iso, locale);
  const region = regionName(raw, locale);
  if (region) return region;
  if (raw.length > 4 && raw === raw.toUpperCase() && /[A-Z]/.test(raw)) return sentenceCase(raw);
  return raw;
}

export type CountryOptionCopy = {
  /** Facet key used for rows without a country. */
  noneKey: string;
  none: string;
  inResults: string;
  all: string;
};

/**
 * Dropdown options for a country facet: the values present in the list first (full names),
 * then every country in the world so the user can filter on a place before it shows up.
 */
export function countryDropdownOptions(
  present: string[],
  locale: string,
  copy: CountryOptionCopy,
): IDropdownOption[] {
  const seen = new Set<string>();
  const top: IDropdownOption[] = [];
  for (const raw of present) {
    const key = String(raw || "").trim();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    // "HAITI" stored by a portal still filters on "HAITI", but reads as Haiti and hides the ISO twin below.
    const iso = countryIso(key);
    if (iso) seen.add(iso);
    top.push({ key, text: key === copy.noneKey ? copy.none : countryDisplayName(key, locale) });
  }
  top.sort((left, right) => {
    if (left.key === copy.noneKey) return 1;
    if (right.key === copy.noneKey) return -1;
    return String(left.text).localeCompare(String(right.text), locale);
  });
  const rest: IDropdownOption[] = CRM_COUNTRIES.filter((row) => !seen.has(row.value))
    .map((row) => ({ key: row.value, text: countryLabel(row.value, locale) }))
    .sort((left, right) => String(left.text).localeCompare(String(right.text), locale));
  const options: IDropdownOption[] = [];
  if (top.length) {
    options.push({ key: "__in_results__", text: copy.inResults, itemType: SelectableOptionMenuItemType.Header });
    options.push(...top);
    options.push({ key: "__divider__", text: "-", itemType: SelectableOptionMenuItemType.Divider });
  }
  options.push({ key: "__all__", text: copy.all, itemType: SelectableOptionMenuItemType.Header });
  options.push(...rest);
  return options;
}
