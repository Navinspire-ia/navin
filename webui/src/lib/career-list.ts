import type { CareerOpportunity } from "@/lib/career-api";

export type OfferListView = "inbox" | "favorites" | "archive";
export type OfferRowAction = "view" | "favorite" | "unfavorite" | "archive" | "unarchive" | "delete";

export const CAREER_PAGE_SIZE = 10;
export const DEFAULT_ARCHIVE_AFTER_DAYS = 45;
export const DEFAULT_DELETE_AFTER_DAYS = 60;

export function isArchived(row: CareerOpportunity): boolean {
  return Boolean(row.archived);
}

export function isFavorite(row: CareerOpportunity): boolean {
  return Boolean(row.favorite) && !isArchived(row);
}

export function activeOffers(rows: CareerOpportunity[]): CareerOpportunity[] {
  return rows.filter((row) => !isArchived(row));
}

export function favoriteOffers(rows: CareerOpportunity[]): CareerOpportunity[] {
  return rows.filter(isFavorite);
}

export function archivedOffers(rows: CareerOpportunity[]): CareerOpportunity[] {
  return rows.filter(isArchived);
}

export function paginateOffers<T>(rows: T[], page: number, size = CAREER_PAGE_SIZE): T[] {
  const total = Math.max(1, size);
  const pages = Math.max(1, Math.ceil(rows.length / total));
  const safe = Math.min(Math.max(1, page), pages);
  const start = (safe - 1) * total;
  return rows.slice(start, start + total);
}

export function offerPageOf(index: number, size = CAREER_PAGE_SIZE): number {
  if (index < 0) return 1;
  return Math.floor(index / Math.max(1, size)) + 1;
}

export function offerPageCount(total: number, size = CAREER_PAGE_SIZE): number {
  return Math.max(1, Math.ceil(Math.max(0, total) / Math.max(1, size)));
}

export function retentionDays(input: {
  archive_after_days?: unknown;
  delete_after_days?: unknown;
} = {}): { archive_after_days: number; delete_after_days: number } {
  const clamp = (value: unknown, fallback: number) => {
    const days = typeof value === "number" ? value : Number(String(value || "").trim());
    if (!Number.isFinite(days) || days <= 0) return fallback;
    return Math.min(Math.round(days), 3650);
  };
  const archive_after_days = clamp(input.archive_after_days, DEFAULT_ARCHIVE_AFTER_DAYS);
  let delete_after_days = clamp(input.delete_after_days, DEFAULT_DELETE_AFTER_DAYS);
  if (delete_after_days < archive_after_days) delete_after_days = archive_after_days;
  return { archive_after_days, delete_after_days };
}
