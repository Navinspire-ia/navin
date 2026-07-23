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

export async function setAppLanguage(locale: SupportedLocale): Promise<void> {
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

const syncLocaleSideEffects = (language: string) => {
  const locale = normalizeLocale(language);
  applyDocumentLocale(locale);
  persistLocale(locale);
};

syncLocaleSideEffects(currentLocale());
i18n.on("languageChanged", syncLocaleSideEffects);

export { LOCALE_STORAGE_KEY };
export default i18n;
