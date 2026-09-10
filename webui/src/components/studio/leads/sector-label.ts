// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Human label for the sector of a lead. Registries (SIRENE, Companies House)
 * store a NAF / NACE section letter; the ICP wizard stores free text.
 */

type Bi = { en: string; fr: string };

const NACE_SECTIONS: Record<string, Bi> = {
  A: { en: "Agriculture, forestry and fishing", fr: "Agriculture, sylviculture et pêche" },
  B: { en: "Mining and quarrying", fr: "Industries extractives" },
  C: { en: "Manufacturing", fr: "Industrie manufacturière" },
  D: { en: "Electricity, gas and steam", fr: "Électricité, gaz et vapeur" },
  E: { en: "Water supply and waste", fr: "Eau, assainissement et déchets" },
  F: { en: "Construction", fr: "Construction" },
  G: { en: "Wholesale and retail trade", fr: "Commerce" },
  H: { en: "Transportation and storage", fr: "Transports et entreposage" },
  I: { en: "Accommodation and food services", fr: "Hébergement et restauration" },
  J: { en: "Information and communication", fr: "Information et communication" },
  K: { en: "Financial and insurance activities", fr: "Finance et assurance" },
  L: { en: "Real estate activities", fr: "Immobilier" },
  M: { en: "Professional, scientific and technical", fr: "Activités spécialisées, scientifiques et techniques" },
  N: { en: "Administrative and support services", fr: "Services administratifs et de soutien" },
  O: { en: "Public administration", fr: "Administration publique" },
  P: { en: "Education", fr: "Enseignement" },
  Q: { en: "Human health and social work", fr: "Santé humaine et action sociale" },
  R: { en: "Arts, entertainment and recreation", fr: "Arts, spectacles et loisirs" },
  S: { en: "Other service activities", fr: "Autres services" },
  T: { en: "Households as employers", fr: "Ménages employeurs" },
  U: { en: "Extraterritorial organisations", fr: "Organisations extraterritoriales" },
};

export function sectorLabel(value: string, locale = "en"): string {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const section = NACE_SECTIONS[raw.toUpperCase()];
  if (raw.length === 1 && section) return locale.toLowerCase().startsWith("en") ? section.en : section.fr;
  if (raw.length > 3 && raw === raw.toUpperCase() && /[A-Z]/.test(raw)) {
    const lower = raw.toLowerCase();
    return lower.charAt(0).toUpperCase() + lower.slice(1);
  }
  return raw.charAt(0).toUpperCase() + raw.slice(1);
}
