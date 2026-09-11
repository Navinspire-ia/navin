// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Sidebar Studio entries, default order. */
export const STUDIO_MODULE_IDS = [
  "tenders",
  "career",
  "leads",
  "marketing",
  "trading",
  "ads",
  "seo",
  "scraping",
  "montage",
  "notes",
  "meeting",
  "crm",
  "content",
  "risklens",
] as const;

export type StudioModuleId = (typeof STUDIO_MODULE_IDS)[number];

export const STUDIO_MODULE_LABEL_KEYS: Record<StudioModuleId, string> = {
  tenders: "sidebar.tendersStudio",
  career: "sidebar.careerStudio",
  leads: "sidebar.leadsStudio",
  marketing: "sidebar.marketingStudio",
  trading: "sidebar.tradingStudio",
  ads: "sidebar.adsStudio",
  seo: "sidebar.seoStudio",
  scraping: "sidebar.scrapingStudio",
  montage: "sidebar.montageStudio",
  notes: "sidebar.notes",
  meeting: "sidebar.meetingStudio",
  crm: "sidebar.crm",
  content: "sidebar.contentStudio",
  risklens: "sidebar.risklensStudio",
};

const STUDIO_ID_SET = new Set<string>(STUDIO_MODULE_IDS);

export function isStudioModuleId(value: string | null | undefined): value is StudioModuleId {
  return Boolean(value && STUDIO_ID_SET.has(value));
}

export function normalizeStudioOrder(order: unknown): StudioModuleId[] {
  const seen = new Set<StudioModuleId>();
  const out: StudioModuleId[] = [];
  if (Array.isArray(order)) {
    for (const item of order) {
      const id = String(item || "").trim();
      if (isStudioModuleId(id) && !seen.has(id)) {
        seen.add(id);
        out.push(id);
      }
    }
  }
  for (const id of STUDIO_MODULE_IDS) {
    if (!seen.has(id)) out.push(id);
  }
  return out;
}

export function normalizeStudioHidden(hidden: unknown): StudioModuleId[] {
  if (!Array.isArray(hidden)) return [];
  const seen = new Set<StudioModuleId>();
  const out: StudioModuleId[] = [];
  for (const item of hidden) {
    const id = String(item || "").trim();
    if (isStudioModuleId(id) && !seen.has(id)) {
      seen.add(id);
      out.push(id);
    }
  }
  return out;
}

export function visibleStudioModules(
  order: readonly StudioModuleId[],
  hidden: readonly StudioModuleId[],
): StudioModuleId[] {
  const hide = new Set(hidden);
  return order.filter((id) => !hide.has(id));
}

export function moveStudioModule(
  order: readonly StudioModuleId[],
  id: StudioModuleId,
  delta: -1 | 1,
): StudioModuleId[] {
  const next = [...order];
  const index = next.indexOf(id);
  if (index < 0) return next;
  const target = index + delta;
  if (target < 0 || target >= next.length) return next;
  const [row] = next.splice(index, 1);
  next.splice(target, 0, row);
  return next;
}

export function placeStudioModule(
  order: readonly StudioModuleId[],
  sourceId: StudioModuleId,
  targetId: StudioModuleId,
): StudioModuleId[] {
  if (sourceId === targetId) return [...order];
  const from = order.indexOf(sourceId);
  const to = order.indexOf(targetId);
  if (from < 0 || to < 0) return [...order];
  const next = [...order];
  const [row] = next.splice(from, 1);
  next.splice(to, 0, row);
  return next;
}

export function toggleStudioHidden(
  hidden: readonly StudioModuleId[],
  id: StudioModuleId,
  hide: boolean,
): StudioModuleId[] {
  const set = new Set(hidden);
  if (hide) set.add(id);
  else set.delete(id);
  return STUDIO_MODULE_IDS.filter((item) => set.has(item));
}

export function studioModuleAtY(
  rows: ReadonlyArray<{ id: StudioModuleId; top: number; bottom: number }>,
  y: number,
): StudioModuleId | null {
  if (!rows.length) return null;
  if (y < rows[0].top) return rows[0].id;
  for (const row of rows) {
    if (y >= row.top && y <= row.bottom) return row.id;
  }
  return rows[rows.length - 1].id;
}

export function studioLayoutIsDefault(
  order: readonly StudioModuleId[],
  hidden: readonly StudioModuleId[],
): boolean {
  if (hidden.length > 0) return false;
  return order.length === STUDIO_MODULE_IDS.length && order.every((id, index) => id === STUDIO_MODULE_IDS[index]);
}
