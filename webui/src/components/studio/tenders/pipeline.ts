// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { TenderNotice } from "@/lib/tenders-api";

export type PipelineFilter = "play" | "go" | "urgent" | "draft" | "nogo" | "all";
export type NoticeListView = "pipeline" | "favorites" | "archive";
export type NoticeRowAction =
  | "view"
  | "qualify"
  | "write"
  | "go"
  | "nogo"
  | "favorite"
  | "unfavorite"
  | "archive"
  | "unarchive"
  | "delete";

export const TENDERS_PAGE_SIZE = 10;

export function isArchived(row: TenderNotice): boolean {
  return Boolean(row.archived);
}

export function isFavorite(row: TenderNotice): boolean {
  return Boolean(row.favorite) && !isArchived(row);
}

export function activeNotices(rows: TenderNotice[]): TenderNotice[] {
  return rows.filter((row) => !isArchived(row));
}

export function favoriteNotices(rows: TenderNotice[]): TenderNotice[] {
  return rows.filter(isFavorite);
}

export function archivedNotices(rows: TenderNotice[]): TenderNotice[] {
  return rows.filter(isArchived);
}

export function paginateNotices<T>(rows: T[], page: number, size = TENDERS_PAGE_SIZE): T[] {
  const total = Math.max(1, size);
  const pages = Math.max(1, Math.ceil(rows.length / total));
  const safe = Math.min(Math.max(1, page), pages);
  const start = (safe - 1) * total;
  return rows.slice(start, start + total);
}

export function noticePageCount(total: number, size = TENDERS_PAGE_SIZE): number {
  return Math.max(1, Math.ceil(Math.max(0, total) / Math.max(1, size)));
}

const CLOSED = new Set(["won", "lost"]);
const DRAFTING = new Set(["drafting", "validating", "submitted", "clarification", "shortlisted", "negotiation"]);

export function daysLeft(row: TenderNotice): number | null {
  const raw = row.score_breakdown?.days_left;
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

export function isClosed(row: TenderNotice): boolean {
  return CLOSED.has(String(row.stage || ""));
}

export function isNoGo(row: TenderNotice): boolean {
  if (isArchived(row) || isDrafting(row) || row.stage === "go") return false;
  return row.stage === "no-go" || row.go === false;
}

export function isGo(row: TenderNotice): boolean {
  if (isArchived(row) || row.stage === "no-go") return false;
  return row.go === true || row.stage === "go";
}

export function noticeGoMark(row: Pick<TenderNotice, "go" | "stage">): "go" | "nogo" | null {
  if (row.stage === "go") return "go";
  if (row.stage === "no-go") return "nogo";
  if (row.go === true) return "go";
  if (row.go === false) return "nogo";
  return null;
}

export function isInPlay(row: TenderNotice): boolean {
  return !isArchived(row) && !isNoGo(row) && !isClosed(row);
}

export function isUrgent(row: TenderNotice): boolean {
  const days = daysLeft(row);
  return days != null && days >= 0 && days < 7 && isInPlay(row);
}

export function isDrafting(row: TenderNotice): boolean {
  return !isArchived(row) && DRAFTING.has(String(row.stage || ""));
}

export function pipelineCounts(rows: TenderNotice[]): Record<PipelineFilter, number> {
  return {
    play: rows.filter(isInPlay).length,
    go: rows.filter(isGo).length,
    urgent: rows.filter(isUrgent).length,
    draft: rows.filter(isDrafting).length,
    nogo: rows.filter(isNoGo).length,
    all: rows.length,
  };
}

export function filterNotices(
  rows: TenderNotice[],
  filter: PipelineFilter,
  query = "",
): TenderNotice[] {
  const needle = query.trim().toLowerCase();
  return rows.filter((row) => {
    if (filter === "play" && !isInPlay(row)) return false;
    if (filter === "go" && !isGo(row)) return false;
    if (filter === "urgent" && !isUrgent(row)) return false;
    if (filter === "draft" && !isDrafting(row)) return false;
    if (filter === "nogo" && !isNoGo(row)) return false;
    if (!needle) return true;
    const hay = [
      row.id,
      row.title,
      row.country,
      row.buyer,
      row.stage,
      row.reference,
      row.description,
      row.source_id,
      row.sector,
      row.cpv,
      row.currency,
      row.publication_date,
      row.go_reason,
      row.go_note,
      typeof row.score === "number" ? String(row.score) : "",
      row.analysis && typeof row.analysis === "object"
        ? Object.values(row.analysis).filter((value) => typeof value === "string").join(" ")
        : "",
      row.response && typeof row.response === "object"
        ? Object.values(row.response).filter((value) => typeof value === "string").join(" ")
        : "",
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return hay.includes(needle);
  });
}

export function sortNotices(rows: TenderNotice[]): TenderNotice[] {
  return [...rows].sort((left, right) => {
    const leftGo = isGo(left) ? 1 : 0;
    const rightGo = isGo(right) ? 1 : 0;
    if (leftGo !== rightGo) return rightGo - leftGo;
    const leftScore = left.score ?? -1;
    const rightScore = right.score ?? -1;
    if (leftScore !== rightScore) return rightScore - leftScore;
    const leftDays = daysLeft(left);
    const rightDays = daysLeft(right);
    if (leftDays != null && rightDays != null && leftDays !== rightDays) return leftDays - rightDays;
    if (leftDays != null && rightDays == null) return -1;
    if (leftDays == null && rightDays != null) return 1;
    return (left.title || "").localeCompare(right.title || "");
  });
}
