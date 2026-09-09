// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

export function formatMoney(
  value: number,
  currency = "EUR",
  locale = "fr-FR",
): string {
  const amount = Number.isFinite(value) ? value : 0;
  try {
    return new Intl.NumberFormat(locale || "fr-FR", {
      style: "currency",
      currency: currency || "EUR",
      maximumFractionDigits: 2,
      minimumFractionDigits: 0,
    }).format(amount);
  } catch {
    return `${new Intl.NumberFormat(locale || "fr-FR").format(amount)} ${currency || "EUR"}`;
  }
}

export const CRM_CURRENCIES = [
  { code: "EUR", symbol: "€" },
  { code: "USD", symbol: "$" },
  { code: "GBP", symbol: "£" },
  { code: "CHF", symbol: "CHF" },
  { code: "CAD", symbol: "CA$" },
  { code: "AUD", symbol: "A$" },
  { code: "JPY", symbol: "¥" },
  { code: "TND", symbol: "DT" },
  { code: "MAD", symbol: "DH" },
  { code: "DZD", symbol: "DA" },
] as const;

export const CRM_LOCALES = [
  { code: "fr-FR", label: "fr-FR" },
  { code: "en-US", label: "en-US" },
  { code: "en-GB", label: "en-GB" },
  { code: "de-DE", label: "de-DE" },
] as const;
