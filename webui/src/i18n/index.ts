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
} from "./config";

import enCommon from "./locales/en/common.json";
import frCommon from "./locales/fr/common.json";

export const resources = {
  en: { common: enCommon },
  fr: { common: frCommon },
} as const;

export function currentLocale(): SupportedLocale {
  return normalizeLocale(i18n.resolvedLanguage ?? i18n.language ?? defaultLocale);
}

/** Explicit user choice, the only thing that gets persisted. */
export async function setAppLanguage(locale: SupportedLocale): Promise<void> {
  persistLocale(locale);
  await i18n.changeLanguage(locale);
}

if (!i18n.isInitialized) {
  void i18n
    .use(initReactI18next)
    .init({
      resources,
      lng: resolveInitialLocale(),
      fallbackLng: fallbackLocale,
      defaultNS: "common",
      ns: ["common"],
      interpolation: {
        escapeValue: false,
      },
      returnNull: false,
      supportedLngs: Object.keys(resources),
    });
}

// Only the document attribute follows every change; persisting here as well
// is what used to freeze the install on English, because the default was
// written back on the very first load and then always found first.
const syncLocaleSideEffects = (language: string) => {
  applyDocumentLocale(normalizeLocale(language));
};

syncLocaleSideEffects(currentLocale());
i18n.on("languageChanged", syncLocaleSideEffects);

export { LOCALE_STORAGE_KEY };
export default i18n;
