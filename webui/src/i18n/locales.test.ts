// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only
// Locale parity: every supported language must carry the full English key set,
// keep placeholders intact, stay free of long dashes, and (for Arabic) ship the
// six plural forms i18next expects. Extra keys are tolerated only when they are
// plural siblings of an English key - that is how Arabic and French express
// counts the English file folds into two forms.

import { describe, expect, it } from "vitest";

import en from "./locales/en/common.json";
import fr from "./locales/fr/common.json";
import ar from "./locales/ar/common.json";

type Tree = Record<string, unknown>;

const PLURAL_SUFFIXES = ["zero", "one", "two", "few", "many", "other"] as const;

function leaves(tree: unknown, prefix = ""): Map<string, string> {
  const out = new Map<string, string>();
  if (tree === null || tree === undefined) return out;
  if (typeof tree !== "object") {
    out.set(prefix, String(tree));
    return out;
  }
  for (const [key, value] of Object.entries(tree as Tree)) {
    const path = prefix ? `${prefix}/${key}` : key;
    if (value !== null && typeof value === "object") {
      for (const [p, v] of leaves(value, path)) out.set(p, v);
    } else if (value !== undefined) {
      out.set(path, String(value));
    }
  }
  return out;
}

function placeholders(value: string): string[] {
  return [...value.matchAll(/\{\{[^{}]+\}\}/g)].map((m) => m[0]);
}

function pluralBase(key: string): string | null {
  const idx = key.lastIndexOf("_");
  if (idx === -1) return null;
  const suffix = key.slice(idx + 1);
  return (PLURAL_SUFFIXES as readonly string[]).includes(suffix)
    ? key.slice(0, idx)
    : null;
}

const enLeaves = leaves(en);
const enPluralBases = new Set(
  [...enLeaves.keys()].map(pluralBase).filter((b): b is string => b !== null),
);

function expectParity(locale: Tree, name: string): void {
  const got = leaves(locale);

  const missing: string[] = [];
  for (const [path, enValue] of enLeaves) {
    if (got.has(path)) continue;
    // Accept plural siblings in place of the English pair: ar ships six forms
    // where en has _one/_other, so the exact path may legitimately be absent
    // as long as every plural sibling of the same base key is there.
    const base = pluralBase(path);
    if (base && [...got.keys()].some((k) => pluralBase(k) === base)) continue;
    missing.push(`${path} (en: ${enValue})`);
  }
  expect(missing, `${name} is missing keys`).toEqual([]);

  const unexpected: string[] = [];
  for (const path of got.keys()) {
    if (enLeaves.has(path)) continue;
    const base = pluralBase(path);
    if (!base || !(enLeaves.has(base) || enPluralBases.has(base))) unexpected.push(path);
  }
  expect(unexpected).toEqual([]);

  const brokenPlaceholders: string[] = [];
  for (const [path, enValue] of enLeaves) {
    const value = got.get(path);
    if (value === undefined) continue;
    // Plural forms may spell the number out ("ملف واحد", "ملفان") and drop
    // {{count}}; every other placeholder must survive.
    const isPlural = pluralBase(path) !== null;
    const keep = (ph: string) => !(isPlural && ph.includes("count"));
    const want = [...new Set(placeholders(enValue).filter(keep))].sort().join("|");
    const have = [...new Set(placeholders(value).filter(keep))].sort().join("|");
    if (want !== have) brokenPlaceholders.push(`${path}: ${have} != ${want}`);
  }
  expect(brokenPlaceholders).toEqual([]);

  const longDashes: string[] = [];
  for (const [path, value] of got) {
    if (/[\u2014\u2013]/.test(value)) longDashes.push(path);
  }
  expect(longDashes).toEqual([]);
}

describe("locale parity with en", () => {
  it("english reference is non-trivial", () => {
    expect(enLeaves.size).toBeGreaterThan(8000);
  });

  it("french keeps full parity", () => expectParity(fr, "fr"));
  it("arabic keeps full parity", () => expectParity(ar, "ar"));
});

describe("arabic plurals", () => {
  const arLeaves = leaves(ar);

  it("ships all six cardinal forms for every english plural pair", () => {
    const bases = new Set<string>();
    for (const path of enLeaves.keys()) {
      const base = pluralBase(path);
      if (base) bases.add(base);
    }
    const incomplete: string[] = [];
    for (const base of bases) {
      for (const suffix of PLURAL_SUFFIXES) {
        if (!arLeaves.has(`${base}_${suffix}`)) incomplete.push(`${base}_${suffix}`);
      }
    }
    expect(incomplete).toEqual([]);
  });
});

// A key present in fr but still carrying the English sentence is invisible to
// the missing-key handler, so "New Agent" shipped on a French desktop. Brand
// names, acronyms and paths legitimately read the same in every language.
const SAME_IN_EVERY_LANGUAGE = new Set([
  "Application", "Branches", "Bonus +{{amount}}", "Code", "Commits", "CRM", "Documents",
  "Flash", "Leads", "Loop", "Marketing", "Mcp", "Montage", "Notes", "Ops", "Plus", "Pro",
  "Prompts", "RiskLens", "Scraping", "SEO", "Site", "Skills & Loop", "Source", "Stack",
  "Studio", "Team", "Terminal", "Trading", "Ultra", "agent", "sessions",
]);

function untranslatedLeaves(locale: Tree, prefixes: readonly string[]): string[] {
  const got = leaves(locale);
  const out: string[] = [];
  for (const [path, enValue] of enLeaves) {
    if (!prefixes.some((prefix) => path.startsWith(`${prefix}/`))) continue;
    if (got.get(path) !== enValue) continue;
    if (SAME_IN_EVERY_LANGUAGE.has(enValue)) continue;
    // Paths, placeholders-only strings, slugs and glyphs carry no language.
    const letters = enValue.replace(/\{\{[^{}]+\}\}/g, "").replace(/^\/.*/, "");
    if (!/[a-zA-Z]{3,}/.test(letters) || /^[a-z]+(-[a-z]+)+$/.test(letters)) continue;
    out.push(`${path} (${enValue})`);
  }
  return out;
}

describe("french sidebar and chat list are translated", () => {
  it("carries no english string under sidebar/ and chat/", () => {
    expect(untranslatedLeaves(fr, ["sidebar", "chat"])).toEqual([]);
  });

  it("arabic carries no english string under sidebar/ and chat/", () => {
    expect(untranslatedLeaves(ar, ["sidebar", "chat"])).toEqual([]);
  });

  // The rest of the French file is translated in batches: this ratchet only
  // forbids new English strings from landing while that work is under way.
  // Lower the ceiling as batches land; never raise it.
  it("does not grow the backlog of english strings elsewhere", () => {
    const got = leaves(fr);
    let identical = 0;
    for (const [path, enValue] of enLeaves) {
      if (got.get(path) !== enValue) continue;
      if (/[a-zA-Z]{3,}/.test(enValue.replace(/\{\{[^{}]+\}\}/g, ""))) identical += 1;
    }
    expect(identical).toBeLessThanOrEqual(2640);
  });
});
