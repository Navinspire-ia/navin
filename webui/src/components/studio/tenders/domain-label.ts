// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Human label for the "domain" facet of a notice. Portals store very different
 * things in that field: CPV codes ("72000000"), TED form types ("cn-standard"),
 * shouting categories ("FOURNITURES") or already readable text.
 */

type Bi = { en: string; fr: string };

/** CPV divisions (first two digits of the code). */
const CPV_DIVISIONS: Record<string, Bi> = {
  "03": { en: "Agricultural products", fr: "Produits agricoles" },
  "09": { en: "Petroleum products and energy", fr: "Produits pétroliers et énergie" },
  "14": { en: "Mining and basic metals", fr: "Produits miniers et métaux" },
  "15": { en: "Food and beverages", fr: "Alimentation et boissons" },
  "16": { en: "Agricultural machinery", fr: "Machines agricoles" },
  "18": { en: "Clothing and footwear", fr: "Vêtements et chaussures" },
  "19": { en: "Leather and textile", fr: "Cuir et textile" },
  "22": { en: "Printed matter", fr: "Imprimés" },
  "24": { en: "Chemical products", fr: "Produits chimiques" },
  "30": { en: "Office and computing machinery", fr: "Machines de bureau et informatique" },
  "31": { en: "Electrical machinery", fr: "Machines électriques" },
  "32": { en: "Radio, TV and telecom equipment", fr: "Équipements radio, TV et télécom" },
  "33": { en: "Medical equipment", fr: "Équipements médicaux" },
  "34": { en: "Transport equipment", fr: "Équipements de transport" },
  "35": { en: "Security and defence equipment", fr: "Équipements de sécurité et défense" },
  "37": { en: "Musical and sports goods", fr: "Articles de musique et de sport" },
  "38": { en: "Laboratory and optical equipment", fr: "Équipements de laboratoire et optique" },
  "39": { en: "Furniture and household goods", fr: "Mobilier et équipements ménagers" },
  "41": { en: "Water", fr: "Eau" },
  "42": { en: "Industrial machinery", fr: "Machines industrielles" },
  "43": { en: "Mining and construction machinery", fr: "Machines de mine et de construction" },
  "44": { en: "Construction materials", fr: "Matériaux de construction" },
  "45": { en: "Construction work", fr: "Travaux de construction" },
  "48": { en: "Software", fr: "Logiciels" },
  "50": { en: "Repair and maintenance", fr: "Réparation et maintenance" },
  "51": { en: "Installation services", fr: "Services d'installation" },
  "55": { en: "Hotel and restaurant services", fr: "Hôtellerie et restauration" },
  "60": { en: "Transport services", fr: "Services de transport" },
  "63": { en: "Transport support and travel", fr: "Services annexes de transport et voyages" },
  "64": { en: "Postal and telecom services", fr: "Services postaux et télécom" },
  "65": { en: "Public utilities", fr: "Services publics (eau, énergie)" },
  "66": { en: "Financial and insurance services", fr: "Services financiers et assurance" },
  "70": { en: "Real estate services", fr: "Services immobiliers" },
  "71": { en: "Architecture and engineering", fr: "Architecture et ingénierie" },
  "72": { en: "IT services", fr: "Services informatiques" },
  "73": { en: "Research and development", fr: "Recherche et développement" },
  "75": { en: "Public administration and defence", fr: "Administration publique et défense" },
  "76": { en: "Oil and gas services", fr: "Services pétroliers et gaziers" },
  "77": { en: "Agricultural and forestry services", fr: "Services agricoles et forestiers" },
  "79": { en: "Business services", fr: "Services aux entreprises" },
  "80": { en: "Education and training", fr: "Éducation et formation" },
  "85": { en: "Health and social work", fr: "Santé et action sociale" },
  "90": { en: "Environmental services", fr: "Services environnementaux" },
  "92": { en: "Recreation, culture and sport", fr: "Loisirs, culture et sport" },
  "98": { en: "Other community services", fr: "Autres services collectifs" },
};

/** TED eForms notice subtypes, by prefix. */
const TED_FORMS: { prefix: string; label: Bi }[] = [
  { prefix: "pin", label: { en: "Prior information notice", fr: "Avis de préinformation" } },
  { prefix: "qu", label: { en: "Qualification system", fr: "Système de qualification" } },
  { prefix: "cn", label: { en: "Contract notice", fr: "Avis de marché" } },
  { prefix: "can", label: { en: "Contract award notice", fr: "Avis d'attribution" } },
  { prefix: "veat", label: { en: "Voluntary ex ante notice", fr: "Avis de transparence ex ante" } },
  { prefix: "corr", label: { en: "Corrigendum", fr: "Rectificatif" } },
  { prefix: "cm", label: { en: "Contract modification", fr: "Modification de marché" } },
];

const CPV_RE = /^\d{8}(?:-\d)?(?:[\s,;]+\d{8}(?:-\d)?)*$/;

function pick(label: Bi, locale: string): string {
  return locale.toLowerCase().startsWith("en") ? label.en : label.fr;
}

function sentenceCase(text: string): string {
  const lower = text.toLowerCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

export function domainLabel(value: string, locale = "fr"): string {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (CPV_RE.test(raw)) {
    const codes = raw.split(/[\s,;]+/).filter(Boolean);
    const division = codes[0].slice(0, 2);
    const known = CPV_DIVISIONS[division];
    const head = known ? pick(known, locale) : `CPV ${codes[0]}`;
    return codes.length > 1 ? `${head} +${codes.length - 1}` : head;
  }
  const lower = raw.toLowerCase();
  if (/^[a-z]+(-[a-z]+)*$/.test(lower) && lower.includes("-")) {
    const stem = lower.split("-")[0];
    const form = TED_FORMS.find((row) => row.prefix === stem);
    if (form) return pick(form.label, locale);
  }
  if (raw.length > 3 && raw === raw.toUpperCase() && /[A-Z]/.test(raw)) return sentenceCase(raw);
  return raw;
}
