/**
 * Conversions between note property values and the editable cell text used by
 * the database views (Table / Board).
 */

import type { NotePropValue } from "@/lib/notes-api";

export function propToText(value: NotePropValue | undefined): string {
  if (value === undefined || value === null) return "";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

/** Empty text deletes the property (null); "a, b" becomes a multi-select list. */
export function textToProp(text: string): NotePropValue | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  if (/^-?\d+(\.\d+)?$/.test(trimmed)) return Number(trimmed);
  if (trimmed.includes(",")) {
    const parts = trimmed.split(",").map((part) => part.trim()).filter(Boolean);
    if (parts.length > 1) return parts;
  }
  return trimmed;
}
