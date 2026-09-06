/**
 * First-launch language resolution.
 *
 * The bug these guard against: a French Windows installed Navin in French and
 * the editor opened in English anyway, because nothing ever looked at the
 * machine's language and the English default was persisted on first load,
 * after which it always won.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  detectSystemLocale,
  LOCALE_CHOICE_KEY,
  LOCALE_STORAGE_KEY,
  matchLocale,
  persistLocale,
  resolveInitialLocale,
} from "./config";

function fakeStorage(entries: Record<string, string> = {}) {
  const store = new Map(Object.entries(entries));
  return {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
  };
}

function givenBrowser({
  stored = {},
  languages = [] as string[],
}: {
  stored?: Record<string, string>;
  languages?: string[];
} = {}) {
  const storage = fakeStorage(stored);
  vi.stubGlobal("window", { localStorage: storage });
  vi.stubGlobal("navigator", { languages, language: languages[0] });
  return storage;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("matchLocale", () => {
  it("maps a regional tag to its base language", () => {
    expect(matchLocale("fr-FR")).toBe("fr");
    expect(matchLocale("FR")).toBe("fr");
  });

  it("answers null for a language we do not speak", () => {
    expect(matchLocale("de-DE")).toBeNull();
    expect(matchLocale("  ")).toBeNull();
  });
});

describe("detectSystemLocale", () => {
  it("takes the first system language we can speak", () => {
    givenBrowser({ languages: ["de-DE", "fr-FR", "en-US"] });
    expect(detectSystemLocale()).toBe("fr");
  });

  it("answers null when the machine speaks none of ours", () => {
    givenBrowser({ languages: ["de-DE", "ja"] });
    expect(detectSystemLocale()).toBeNull();
  });
});

describe("resolveInitialLocale", () => {
  it("opens in the machine's language on a fresh install", () => {
    givenBrowser({ languages: ["fr-FR"] });
    expect(resolveInitialLocale()).toBe("fr");
  });

  it("lets an explicit choice beat the system language", () => {
    givenBrowser({
      stored: { [LOCALE_STORAGE_KEY]: "en", [LOCALE_CHOICE_KEY]: "1" },
      languages: ["fr-FR"],
    });
    expect(resolveInitialLocale()).toBe("en");
  });

  it("ignores the english a previous version wrote on its own", () => {
    // The heart of the bug: old builds persisted the default on every load,
    // so "en" without the choice marker means nothing was ever chosen.
    givenBrowser({
      stored: { [LOCALE_STORAGE_KEY]: "en" },
      languages: ["fr-FR"],
    });
    expect(resolveInitialLocale()).toBe("fr");
  });

  it("keeps a legacy french, which could only come from a real switch", () => {
    givenBrowser({
      stored: { [LOCALE_STORAGE_KEY]: "fr" },
      languages: ["en-US"],
    });
    expect(resolveInitialLocale()).toBe("fr");
  });

  it("falls back to english when nothing is stored or detectable", () => {
    givenBrowser({ languages: ["de-DE"] });
    expect(resolveInitialLocale()).toBe("en");
  });
});

describe("persistLocale", () => {
  it("marks the locale as chosen so it survives future defaults", () => {
    const storage = givenBrowser();
    persistLocale("fr");
    expect(storage.getItem(LOCALE_STORAGE_KEY)).toBe("fr");
    expect(storage.getItem(LOCALE_CHOICE_KEY)).toBe("1");
  });
});
