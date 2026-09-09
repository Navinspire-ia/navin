// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useState } from "react";
import { endOfDay, parseISO, startOfDay } from "date-fns";

import type { CrmMember, CrmRecord } from "@/lib/api";
import { countryLabel, optionList, type SearchableOption } from "@/lib/crm-catalog";
import {
  companyName,
  contactName,
  displayText,
  leadName,
  opportunityName,
} from "@/lib/crm-format";
import type { CrmFacetFilters } from "@/store/crm-ui";
import { useCrmUi } from "@/store/crm-ui";

export const CRM_PAGE_SIZE = 20;
export const CRM_DASH_PAGE_SIZE = 8;
const EMPTY_MEMBERS: CrmMember[] = [];

export type CrmFilterConfig = {
  extraHaystack?: (row: CrmRecord) => string;
  statusKeys?: Array<"status" | "stage">;
};

function rowTags(row: CrmRecord): string[] {
  if (Array.isArray(row.tags)) return row.tags.map((item) => displayText(item)).filter(Boolean);
  const raw = displayText(row.tags);
  if (!raw) return [];
  return raw
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function recordHaystack(row: CrmRecord, extra?: (row: CrmRecord) => string): string {
  const parts = [
    contactName(row),
    companyName(row),
    leadName(row),
    opportunityName(row),
    displayText(row.email),
    displayText(row.phone),
    displayText(row.whatsapp),
    displayText(row.title),
    displayText(row.body),
    displayText(row.company),
    displayText(row.industry),
    displayText(row.website),
    displayText(row.owner),
    extra?.(row) || "",
  ];
  return parts.filter(Boolean).join(" ").toLowerCase();
}

export function ownerAliases(owner: string, members: CrmMember[] = []): string[] {
  const needle = owner.trim().toLowerCase();
  if (!needle) return [];
  const keys = new Set<string>([needle]);
  for (const member of members) {
    const ids = [member.identity, member.email, member.handle, member.displayName, member.id]
      .map((item) => item.trim().toLowerCase())
      .filter(Boolean);
    if (ids.includes(needle)) ids.forEach((id) => keys.add(id));
  }
  return [...keys];
}

function dateBounds(value: string, end = false): number | null {
  const raw = value.trim();
  if (!raw) return null;
  try {
    const parsed = parseISO(raw);
    if (Number.isNaN(parsed.getTime())) return null;
    const stamp = end ? endOfDay(parsed) : startOfDay(parsed);
    return Math.floor(stamp.getTime() / 1000);
  } catch {
    return null;
  }
}

export function applyCrmFilters(
  rows: CrmRecord[],
  query: string,
  facets: CrmFacetFilters,
  config: CrmFilterConfig = {},
  members: CrmMember[] = [],
): CrmRecord[] {
  const needle = query.trim().toLowerCase();
  const statusKeys = config.statusKeys || ["status"];
  const aliases = ownerAliases(facets.owner, members);
  const from = dateBounds(facets.dateFrom, false);
  const to = dateBounds(facets.dateTo, true);

  return rows.filter((row) => {
    if (needle && !recordHaystack(row, config.extraHaystack).includes(needle)) return false;
    if (facets.status) {
      const current = statusKeys.map((key) => displayText(row[key])).find(Boolean) || "";
      if (current !== facets.status) return false;
    }
    if (facets.owner) {
      const value = displayText(row.owner).toLowerCase();
      if (!value || !aliases.includes(value)) return false;
    }
    if (facets.country && displayText(row.country).toUpperCase() !== facets.country.toUpperCase()) {
      return false;
    }
    if (facets.source && displayText(row.source) !== facets.source) return false;
    if (facets.tag && !rowTags(row).includes(facets.tag)) return false;
    if (facets.kind && displayText(row.kind) !== facets.kind) return false;
    if (facets.currency && displayText(row.currency).toUpperCase() !== facets.currency.toUpperCase()) {
      return false;
    }
    const at = Number(row.at || 0);
    if (from != null && (!at || at < from)) return false;
    if (to != null && (!at || at > to)) return false;
    return true;
  });
}

export function uniqueFieldValues(rows: CrmRecord[], field: keyof CrmRecord | "tag"): string[] {
  const values = new Set<string>();
  for (const row of rows) {
    if (field === "tag") {
      rowTags(row).forEach((tag) => values.add(tag));
      continue;
    }
    const text = displayText(row[field]);
    if (text) values.add(text);
  }
  return [...values].sort((a, b) => a.localeCompare(b, "fr"));
}

export function paginateRows<T>(rows: T[], page: number, pageSize: number): T[] {
  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize) || 1);
  const safe = Math.min(Math.max(1, page), totalPages);
  const start = (safe - 1) * pageSize;
  return rows.slice(start, start + pageSize);
}

export function pageRange(page: number, pageSize: number, total: number): { from: number; to: number } {
  if (total === 0) return { from: 0, to: 0 };
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return { from, to };
}

export function visiblePages(current: number, total: number): Array<number | "ellipsis"> {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  const marks = new Set([1, total, current, current - 1, current + 1, current - 2, current + 2]);
  const nums = [...marks].filter((item) => item >= 1 && item <= total).sort((a, b) => a - b);
  const out: Array<number | "ellipsis"> = [];
  for (const num of nums) {
    const prev = out[out.length - 1];
    if (typeof prev === "number" && num - prev > 1) out.push("ellipsis");
    out.push(num);
  }
  return out;
}

export function countLabel(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

export function countryFilterOptions(rows: CrmRecord[], locale = "fr"): SearchableOption[] {
  return uniqueFieldValues(rows, "country").map((code) => ({
    value: code,
    label: countryLabel(code, locale) || code,
    keywords: `${code} ${countryLabel(code, locale)}`.toLowerCase(),
  }));
}

export function sourceFilterOptions(
  rows: CrmRecord[],
  known: readonly string[],
  labelOf: (value: string) => string,
): SearchableOption[] {
  const extras = uniqueFieldValues(rows, "source").filter((item) => !known.includes(item));
  return [...optionList(known, labelOf), ...optionList(extras, (value) => value)];
}

export function tagFilterOptions(rows: CrmRecord[]): SearchableOption[] {
  return optionList(uniqueFieldValues(rows, "tag"), (value) => value);
}

export function ownerFilterOptions(
  rows: CrmRecord[],
  members: CrmMember[],
): { value: string; label: string; keywords?: string }[] {
  const seen = new Set<string>();
  const options: { value: string; label: string; keywords?: string }[] = [];
  const push = (value: string, label: string, keywords = "") => {
    const key = value.trim().toLowerCase();
    if (!key || seen.has(key)) return;
    seen.add(key);
    options.push({ value, label, keywords: `${label} ${value} ${keywords}`.toLowerCase() });
  };
  for (const member of members) {
    const value = member.identity || member.email || member.id;
    push(value, member.displayName || member.identity || member.email || member.id, member.email);
  }
  for (const owner of uniqueFieldValues(rows, "owner")) {
    push(owner, owner);
  }
  return options.sort((a, b) => a.label.localeCompare(b.label, "fr"));
}

export function useCrmFilteredPage(
  rows: CrmRecord[],
  config: CrmFilterConfig = {},
  members: CrmMember[] = EMPTY_MEMBERS,
  pageSize = CRM_PAGE_SIZE,
) {
  const filter = useCrmUi((state) => state.filter);
  const facets = useCrmUi((state) => state.facets);
  const setFilter = useCrmUi((state) => state.setFilter);
  const setFacet = useCrmUi((state) => state.setFacet);
  const [page, setPage] = useState(1);
  const extraHaystack = config.extraHaystack;
  const statusKey = (config.statusKeys || ["status"]).join("|");

  const filtered = useMemo(
    () =>
      applyCrmFilters(
        rows,
        filter,
        facets,
        { extraHaystack, statusKeys: statusKey.split("|") as Array<"status" | "stage"> },
        members,
      ),
    [extraHaystack, facets, filter, members, rows, statusKey],
  );

  useEffect(() => {
    setPage(1);
  }, [filter, facets]);

  const total = filtered.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize) || 1);
  const safePage = Math.min(page, totalPages);

  useEffect(() => {
    if (page !== safePage) setPage(safePage);
  }, [page, safePage]);

  const slice = useMemo(() => paginateRows(filtered, safePage, pageSize), [filtered, pageSize, safePage]);
  const range = pageRange(safePage, pageSize, total);

  return {
    filter,
    facets,
    setFilter,
    setFacet,
    filtered,
    slice,
    page: safePage,
    setPage,
    total,
    totalPages,
    pageSize,
    from: range.from,
    to: range.to,
    showPager: total > pageSize,
  };
}

export function usePagedRows<T>(rows: T[], pageSize: number) {
  const [page, setPage] = useState(1);
  const total = rows.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize) || 1);
  const safePage = Math.min(page, totalPages);

  useEffect(() => {
    if (page !== safePage) setPage(safePage);
  }, [page, safePage]);

  const slice = useMemo(() => paginateRows(rows, safePage, pageSize), [pageSize, rows, safePage]);
  const range = pageRange(safePage, pageSize, total);

  return {
    page: safePage,
    setPage,
    slice,
    total,
    totalPages,
    pageSize,
    from: range.from,
    to: range.to,
    showPager: total > pageSize,
  };
}
