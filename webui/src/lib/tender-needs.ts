import type { SearchableOption } from "@/lib/crm-catalog";

export type NeedOption = {
  id: string;
  label: string;
  label_fr: string;
  keywords?: string;
  cpv?: string;
};

export type NeedsCatalog = {
  tender_types?: NeedOption[];
  domains?: NeedOption[];
  project_types?: NeedOption[];
};

export function needLabel(row: NeedOption, locale: string): string {
  return locale.toLowerCase().startsWith("fr") ? row.label_fr : row.label;
}

export function sameNeedToken(left: string, right: string, catalog: NeedOption[] = []): boolean {
  const a = left.trim().toLowerCase();
  const b = right.trim().toLowerCase();
  if (!a || !b) return false;
  if (a === b) return true;
  const match = (value: string) =>
    catalog.find((row) => {
      const id = row.id.toLowerCase();
      return value === id || value === row.label.toLowerCase() || value === row.label_fr.toLowerCase();
    });
  const leftRow = match(a);
  const rightRow = match(b);
  return Boolean(leftRow && rightRow && leftRow.id === rightRow.id);
}

export function tokenAlreadyPicked(value: string, list: string[], catalog: NeedOption[] = []): boolean {
  return list.some((item) => sameNeedToken(item, value, catalog));
}

export function addNeedToken(value: string, list: string[], catalog: NeedOption[] = []): string[] {
  const next = value.trim();
  if (!next || tokenAlreadyPicked(next, list, catalog)) return list;
  return [...list, next];
}

export function needSelectOptions(
  rows: NeedOption[] | undefined,
  locale: string,
  selected: string[],
): SearchableOption[] {
  return (rows || [])
    .filter((row) => !tokenAlreadyPicked(needLabel(row, locale), selected, rows || []))
    .map((row) => ({
      value: row.id,
      label: needLabel(row, locale),
      keywords: `${row.keywords || ""} ${row.cpv || ""} ${row.label} ${row.label_fr}`,
      hint: row.cpv || undefined,
    }));
}
