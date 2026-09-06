import type { TenderSource, TenderZoneGroup } from "@/lib/tenders-api";

export function filterSourcesByQuery(rows: TenderSource[], query: string): TenderSource[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return rows;
  return rows.filter((row) => {
    const hay = `${row.name} ${row.country} ${row.id} ${row.zone || ""} ${row.url || ""} ${row.access || ""} ${row.coverage || ""}`.toLowerCase();
    return hay.includes(needle);
  });
}

export const ZONE_ORDER = [
  "europe",
  "africa",
  "gcc",
  "maghreb",
  "americas",
  "international",
  "oceania",
  "asia",
  "mena",
] as const;

export function groupSourcesByZone(rows: TenderSource[]): TenderZoneGroup[] {
  const buckets = new Map<string, TenderSource[]>();
  for (const zone of ZONE_ORDER) buckets.set(zone, []);
  const extra: TenderZoneGroup[] = [];
  const extraSeen = new Map<string, TenderSource[]>();
  for (const row of rows) {
    const zone = String(row.zone || "international").trim().toLowerCase();
    if (buckets.has(zone)) {
      buckets.get(zone)!.push(row);
      continue;
    }
    const list = extraSeen.get(zone) || [];
    list.push(row);
    extraSeen.set(zone, list);
  }
  const grouped: TenderZoneGroup[] = [];
  for (const zone of ZONE_ORDER) {
    const sources = buckets.get(zone) || [];
    if (sources.length) grouped.push({ zone, sources });
  }
  for (const [zone, sources] of extraSeen) extra.push({ zone, sources });
  return grouped.concat(extra);
}
