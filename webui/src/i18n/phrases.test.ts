// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import i18n from "./index";
import { intlLocale } from "./intl";
import arPhrases from "./locales/ar/phrases.json";
import { bilingual, fillPlaceholders, phrase } from "./phrases";

const book = arPhrases as Record<string, string>;
const placeholders = (text: string) =>
  [...new Set(text.match(/\{\{[^{}]+\}\}/g) ?? [])].sort().join("|");

describe("arabic phrasebook file", () => {
  it("keeps every placeholder and never ships a long dash", () => {
    const broken = Object.entries(book).filter(
      ([english, arabic]) =>
        placeholders(english) !== placeholders(arabic) || /[–—]/.test(arabic) || !arabic.trim(),
    );
    expect(broken).toEqual([]);
  });

  it("covers every English literal of the two-language helpers", () => {
    // c("français", "english") / text(...) / copy(...) / bilingual(...) and
    // phrase("..."): a new call without an Arabic entry shows English in ar.
    const call = /\b(?:c|text|copy|bilingual)\(\s*"(?:[^"\\]|\\.)*"\s*,\s*"((?:[^"\\]|\\.)*)"|\bphrase\(\s*"((?:[^"\\]|\\.)*)"/g;
    const missing = new Set<string>();
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) {
          walk(path);
        } else if (/\.tsx?$/.test(name) && !/\.test\./.test(name)) {
          for (const match of readFileSync(path, "utf8").matchAll(call)) {
            const english = JSON.parse(`"${match[1] ?? match[2]}"`) as string;
            if (/[A-Za-z]{2}/.test(english) && !(english in book)) missing.add(english);
          }
        }
      }
    };
    walk(join(__dirname, "..", "components"));
    walk(join(__dirname, "..", "lib"));
    expect([...missing]).toEqual([]);
  });
});

describe("phrase lookups", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("ar");
  });
  afterAll(async () => {
    await i18n.changeLanguage("en");
  });

  it("translates known English text and falls back to it otherwise", () => {
    const [english, arabic] = Object.entries(book).find(([en, ar]) => en !== ar && !en.includes("{{"))!;
    expect(phrase(english)).toBe(arabic);
    expect(phrase("A sentence nobody wrote")).toBe("A sentence nobody wrote");
    expect(phrase(english, "en")).toBe(english);
  });

  it("keeps French copy for French and fills placeholders everywhere", () => {
    expect(bilingual("Bonjour {{name}}", "Hello {{name}}", { name: "Aymen" }, "fr")).toBe("Bonjour Aymen");
    expect(bilingual("Bonjour {{name}}", "Hello {{name}}", { name: "Aymen" }, "en")).toBe("Hello Aymen");
    expect(fillPlaceholders("{{a}} / {{b}}", { a: 1 })).toBe("1 / {{b}}");
  });

  it("answers missing keys from the phrasebook, interpolated", () => {
    const [english, arabic] = Object.entries(book).find(
      ([en, ar]) => en !== ar && /\{\{\w+\}\}/.test(en) && (en.match(/\{\{/g) ?? []).length === 1,
    )!;
    const name = english.match(/\{\{(\w+)\}\}/)![1];
    const out = i18n.t("phrasebook.test.neverDefined", { defaultValue: english, [name]: "42" });
    expect(out).toBe(arabic.replace(`{{${name}}}`, "42"));
  });
});

describe("intlLocale", () => {
  it("maps app languages to Intl tags and keeps Latin digits in Arabic", () => {
    expect(intlLocale("fr")).toBe("fr-FR");
    expect(intlLocale("en")).toBe("en-US");
    expect(intlLocale("ar")).toBe("ar-u-nu-latn");
    expect(intlLocale("ar-EG")).toBe("ar-u-nu-latn");
    expect(intlLocale("en-GB")).toBe("en-GB");
    expect(new Intl.NumberFormat(intlLocale("ar")).format(1234)).toMatch(/1.?234/);
  });
});
