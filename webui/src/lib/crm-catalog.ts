import { data as currencyData } from "currency-codes";
import countries from "world-countries";

export type CrmCountry = {
  label: string;
  labelEn: string;
  labelFr: string;
  value: string;
  callingCode: string;
  currency: string;
  flag: string;
  keywords: string;
};

export type CrmCurrency = {
  code: string;
  name: string;
  symbol: string;
  keywords: string;
};

export type SearchableOption = {
  value: string;
  label: string;
  keywords?: string;
  hint?: string;
  icon?: string;
};

function callingCodeOf(idd: { root?: string; suffixes?: string[] } | undefined): string {
  const root = String(idd?.root || "").trim();
  if (!root) return "";
  const suffixes = Array.isArray(idd?.suffixes) ? idd.suffixes : [];
  if (suffixes.length === 1) return `${root}${suffixes[0]}`;
  return root;
}

function currencyOf(raw: Record<string, unknown> | undefined): string {
  const keys = raw ? Object.keys(raw) : [];
  return (keys[0] || "").toUpperCase();
}

export const CRM_COUNTRIES: CrmCountry[] = countries
  .map((row) => {
    const value = String(row.cca2 || "").toUpperCase();
    const labelEn = String(row.name?.common || value);
    const labelFr = String(row.translations?.fra?.common || labelEn);
    const callingCode = callingCodeOf(row.idd);
    const currency = currencyOf(row.currencies as Record<string, unknown> | undefined);
    const keywords = [
      labelEn,
      labelFr,
      row.name?.official,
      row.translations?.fra?.official,
      value,
      row.cca3,
      callingCode,
      `+${callingCode.replace(/^\+/, "")}`,
      ...(row.altSpellings || []),
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return {
      label: labelFr,
      labelEn,
      labelFr,
      value,
      callingCode,
      currency,
      flag: String(row.flag || ""),
      keywords,
    };
  })
  .filter((row) => row.value.length === 2)
  .sort((a, b) => a.labelFr.localeCompare(b.labelFr, "fr"));

const COUNTRY_BY_ISO = new Map(CRM_COUNTRIES.map((row) => [row.value, row]));

export function currencySymbol(code: string): string {
  const iso = (code || "EUR").toUpperCase();
  try {
    const parts = new Intl.NumberFormat("fr-FR", {
      style: "currency",
      currency: iso,
      currencyDisplay: "narrowSymbol",
    }).formatToParts(0);
    const symbol = parts.find((part) => part.type === "currency")?.value;
    return symbol || iso;
  } catch {
    return iso === "EUR" ? "€" : iso;
  }
}

export const CRM_CURRENCIES: CrmCurrency[] = currencyData
  .map((row) => {
    const code = String(row.code || "").toUpperCase();
    const name = String(row.currency || code);
    const symbol = currencySymbol(code);
    return {
      code,
      name,
      symbol,
      keywords: `${code} ${name} ${symbol}`.toLowerCase(),
    };
  })
  .filter((row) => row.code.length === 3)
  .sort((a, b) => a.code.localeCompare(b.code));

const CURRENCY_BY_CODE = new Map(CRM_CURRENCIES.map((row) => [row.code, row]));

export const CRM_LOCALES = [
  { code: "fr-FR", label: "fr-FR" },
  { code: "en-US", label: "en-US" },
  { code: "en-GB", label: "en-GB" },
  { code: "de-DE", label: "de-DE" },
] as const;

export const LEAD_SOURCES = [
  "linkedin",
  "website",
  "salon",
  "referral",
  "cold-call",
  "email",
  "whatsapp",
  "partner",
  "other",
] as const;

export function countryLabel(code: string, locale = "fr"): string {
  const row = COUNTRY_BY_ISO.get(String(code || "").toUpperCase());
  if (!row) return String(code || "");
  return locale.toLowerCase().startsWith("en") ? row.labelEn : row.labelFr;
}

export function countryByCode(code: string): CrmCountry | undefined {
  return COUNTRY_BY_ISO.get(String(code || "").toUpperCase());
}

export function currencyByCode(code: string): CrmCurrency | undefined {
  return CURRENCY_BY_CODE.get(String(code || "").toUpperCase());
}

export function normalizeCountryValue(raw: string): string {
  const text = String(raw || "").trim();
  if (!text) return "";
  const upper = text.toUpperCase();
  if (COUNTRY_BY_ISO.has(upper)) return upper;
  const needle = text.toLowerCase();
  const found = CRM_COUNTRIES.find(
    (row) =>
      row.labelEn.toLowerCase() === needle ||
      row.labelFr.toLowerCase() === needle ||
      row.keywords.split(" ").includes(needle),
  );
  return found?.value || text;
}

export function countryOptions(locale = "fr"): SearchableOption[] {
  const english = locale.toLowerCase().startsWith("en");
  return CRM_COUNTRIES.map((row) => ({
    value: row.value,
    label: `${row.flag} ${english ? row.labelEn : row.labelFr}`,
    keywords: row.keywords,
    hint: `${row.value}${row.callingCode ? ` ${row.callingCode}` : ""}`,
    icon: row.flag,
  }));
}

export function callingCodeOptions(locale = "fr"): SearchableOption[] {
  const english = locale.toLowerCase().startsWith("en");
  return CRM_COUNTRIES.filter((row) => row.callingCode).map((row) => ({
    value: row.value,
    label: `${row.flag} ${row.callingCode}`,
    keywords: row.keywords,
    hint: english ? row.labelEn : row.labelFr,
    icon: row.flag,
  }));
}

export function currencyOptions(): SearchableOption[] {
  return CRM_CURRENCIES.map((row) => ({
    value: row.code,
    label: `${row.code} ${row.symbol} ${row.name}`,
    keywords: row.keywords,
    hint: row.symbol,
  }));
}

export function optionList(
  values: readonly string[],
  labelOf: (value: string) => string,
): SearchableOption[] {
  return values.map((value) => ({
    value,
    label: labelOf(value),
    keywords: `${value} ${labelOf(value)}`.toLowerCase(),
  }));
}
