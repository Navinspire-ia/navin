// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import {
  getCountries,
  getCountryCallingCode,
  isPossiblePhoneNumber,
  isSupportedCountry,
  isValidPhoneNumber,
  parsePhoneNumberFromString,
  type CountryCode,
} from "libphonenumber-js";

const CALLING_CODES = new Set(
  getCountries().map((code) => `+${getCountryCallingCode(code)}`),
);

export type CrmPhoneStatus = "empty" | "valid" | "incomplete" | "invalid" | "garbage";

type Tx = (key: string, fallback: string) => string;

export function isCallingCodeOnly(value: string): boolean {
  const text = value.trim();
  if (!text || text === "+") return true;
  return CALLING_CODES.has(text);
}

export function resolvePhoneCountry(code?: string): CountryCode {
  const upper = String(code || "FR").toUpperCase();
  return isSupportedCountry(upper) ? (upper as CountryCode) : "FR";
}

function digitsOf(value: string): string {
  return value.replace(/\D/g, "");
}

export function isGarbageCrmPhone(value: string): boolean {
  const text = value.trim();
  if (!text || isCallingCodeOnly(text)) return false;
  if (!/\d/.test(text)) return true;
  return !/^[+\d\s()./-]+$/.test(text);
}

export function normalizeCrmPhone(value: string, defaultCountry = "FR"): string {
  const text = value.trim();
  if (!text || isCallingCodeOnly(text)) return "";
  const country = resolvePhoneCountry(defaultCountry);
  try {
    const parsed = parsePhoneNumberFromString(text, country);
    if (parsed?.number) return parsed.number;
  } catch {
    // Keep a reasonable fallback below.
  }
  const digits = digitsOf(text);
  if (!digits) return text;
  if (text.startsWith("+")) return `+${digits}`;
  try {
    const national = parsePhoneNumberFromString(digits, country);
    if (national?.number) return national.number;
  } catch {
    // Fall through to a prefixed national string.
  }
  const calling = getCountryCallingCode(country);
  return `+${calling}${digits.replace(new RegExp(`^${calling}`), "")}`;
}

export function crmPhoneStatus(value: string, defaultCountry = "FR"): CrmPhoneStatus {
  const text = value.trim();
  if (!text || isCallingCodeOnly(text)) return "empty";
  if (isGarbageCrmPhone(text)) return "garbage";
  const country = resolvePhoneCountry(defaultCountry);
  try {
    if (isValidPhoneNumber(text, country)) return "valid";
    if (isPossiblePhoneNumber(text, country)) return "invalid";
    return "incomplete";
  } catch {
    return "incomplete";
  }
}

export function crmPhoneHint(status: CrmPhoneStatus, tx: Tx): string {
  if (status === "incomplete") return tx("crm.incompletePhone", "Numero incomplet");
  if (status === "invalid" || status === "garbage") {
    return tx("crm.invalidPhone", "Numero de telephone invalide");
  }
  return "";
}

/** Settings: never blocks. Contacts/leads: only garbage is a hard fail. */
export function validCrmPhone(value: string, mode: "settings" | "record" = "settings"): boolean {
  if (mode === "settings") return true;
  return crmPhoneStatus(value) !== "garbage";
}
