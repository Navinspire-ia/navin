// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { buildCsv, buildXlsxBytes, downloadBytes } from "@/lib/crm-export";
import type { CareerOpportunity } from "@/lib/career-api";

export type CareerExportTx = (key: string, fallback: string) => string;

export type CareerExportContext = {
  locale?: string;
  tx: CareerExportTx;
};

export type CareerExportColumn = {
  key: string;
  header: string;
  value: (row: CareerOpportunity) => string;
};

function langOf(locale: string): "fr" | "en" {
  return locale.toLowerCase().startsWith("en") ? "en" : "fr";
}

function cell(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "";
  return String(value).replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
}

function list(values: unknown): string {
  if (!Array.isArray(values)) return cell(values);
  return values.map((item) => cell(item)).filter(Boolean).join(", ");
}

export function careerExportColumns(ctx: CareerExportContext): CareerExportColumn[] {
  const tx = ctx.tx;
  return [
    { key: "id", header: tx("exportColId", "Id"), value: (row) => cell(row.id) },
    { key: "title", header: tx("exportColTitle", "Titre"), value: (row) => cell(row.title) },
    { key: "company", header: tx("filterCompany", "Societe"), value: (row) => cell(row.company) },
    { key: "country", header: tx("filterCountry", "Pays"), value: (row) => cell(row.country) },
    { key: "location", header: tx("exportColLocation", "Lieu"), value: (row) => cell(row.location) },
    { key: "source", header: tx("filterSource", "Source"), value: (row) => cell(row.source) },
    { key: "url", header: tx("exportColUrl", "URL"), value: (row) => cell(row.url) },
    { key: "posted", header: tx("exportColPosted", "Publie le"), value: (row) => cell(row.posted_at) },
    { key: "stage", header: tx("filterStage", "Etape"), value: (row) => cell(row.stage) },
    { key: "track", header: tx("filterTrack", "Piste"), value: (row) => cell(row.track) },
    { key: "bucket", header: tx("filterBucket", "Seau"), value: (row) => cell(row.bucket) },
    { key: "score", header: tx("exportColScore", "Score"), value: (row) => cell(row.match_score) },
    { key: "pay", header: tx("exportColPay", "Remuneration"), value: (row) => cell(row.compensation) },
    { key: "currency", header: tx("filterCurrency", "Devise"), value: (row) => cell(row.currency) },
    { key: "remote", header: tx("filterRemote", "Remote"), value: (row) => cell(row.remote) },
    { key: "stack", header: tx("filterStack", "Stack"), value: (row) => list(row.stack) },
    { key: "languages", header: tx("filterLanguage", "Langues"), value: (row) => list(row.languages) },
    {
      key: "favorite",
      header: tx("favoriteBadge", "Favori"),
      value: (row) => (row.favorite ? tx("exportYes", "oui") : ""),
    },
    {
      key: "archived",
      header: tx("archivedBadge", "Archivee"),
      value: (row) => (row.archived ? tx("exportYes", "oui") : ""),
    },
  ];
}

export function tableFromOffers(
  rows: CareerOpportunity[],
  ctx: CareerExportContext,
): { headers: string[]; values: string[][]; sheetName: string } {
  const columns = careerExportColumns(ctx);
  return {
    headers: columns.map((col) => col.header),
    values: rows.map((row) => columns.map((col) => col.value(row))),
    sheetName: ctx.tx("resultsTitle", "Offres").slice(0, 31) || "Offres",
  };
}

export function careerExportStem(now = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `career-offres-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
}

export function buildCareerExportFile(
  rows: CareerOpportunity[],
  format: "csv" | "xlsx",
  ctx: CareerExportContext,
  now = new Date(),
): { filename: string; mime: string; bytes: Uint8Array } | null {
  if (!rows.length) return null;
  const table = tableFromOffers(rows, ctx);
  const stem = careerExportStem(now);
  if (format === "csv") {
    const separator = langOf(ctx.locale || "fr-FR") === "fr" ? ";" : ",";
    return {
      filename: `${stem}.csv`,
      mime: "text/csv;charset=utf-8",
      bytes: new TextEncoder().encode(buildCsv(table.headers, table.values, separator)),
    };
  }
  return {
    filename: `${stem}.xlsx`,
    mime: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    bytes: buildXlsxBytes(table.sheetName, table.headers, table.values),
  };
}

export function downloadCareerExport(
  rows: CareerOpportunity[],
  format: "csv" | "xlsx",
  ctx: CareerExportContext,
): boolean {
  const file = buildCareerExportFile(rows, format, ctx);
  if (!file) return false;
  downloadBytes(file.bytes, file.filename, file.mime);
  return true;
}
