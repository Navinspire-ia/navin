// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { buildCsv, buildXlsxBytes, downloadBytes } from "@/lib/crm-export";
import type { TenderNotice } from "@/lib/tenders-api";

export type TendersExportTx = (key: string, fallback: string) => string;

export type TendersExportContext = {
  locale?: string;
  tx: TendersExportTx;
};

export type TendersExportColumn = {
  key: string;
  header: string;
  value: (row: TenderNotice) => string;
};

function langOf(locale: string): "fr" | "en" {
  return locale.toLowerCase().startsWith("en") ? "en" : "fr";
}

function cell(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "";
  return String(value).replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
}

function goLabel(row: TenderNotice, tx: TendersExportTx): string {
  if (row.go === true) return tx("goYes", "GO");
  if (row.go === false) return tx("goNo", "NO-GO");
  return "";
}

function stageLabel(row: TenderNotice, tx: TendersExportTx): string {
  const stage = cell(row.stage);
  if (!stage) return "";
  return tx(`stage.${stage}`, stage);
}

export function noticeHasOfferPack(row: TenderNotice): boolean {
  const response = row.response;
  if (!response) return false;
  if (response.pack_ready) return true;
  if (response.exports?.docx?.file_id || response.exports?.pptx?.file_id) return true;
  return Boolean(cell(response.letter) || cell(response.executive_summary));
}

export function readyOfferNotices(rows: TenderNotice[]): TenderNotice[] {
  return rows.filter(noticeHasOfferPack);
}

export function tendersExportColumns(ctx: TendersExportContext): TendersExportColumn[] {
  const tx = ctx.tx;
  return [
    { key: "id", header: tx("exportColId", "Id"), value: (row) => cell(row.id) },
    { key: "title", header: tx("exportColTitle", "Titre"), value: (row) => cell(row.title) },
    { key: "reference", header: tx("exportColReference", "Reference"), value: (row) => cell(row.reference) },
    { key: "country", header: tx("filterCountry", "Pays"), value: (row) => cell(row.country) },
    { key: "buyer", header: tx("filterBuyer", "Acheteur"), value: (row) => cell(row.buyer) },
    { key: "source", header: tx("filterSource", "Source"), value: (row) => cell(row.source_id) },
    { key: "sourceUrl", header: tx("exportColUrl", "URL officielle"), value: (row) => cell(row.source_url) },
    { key: "published", header: tx("exportColPublished", "Publie le"), value: (row) => cell(row.publication_date) },
    { key: "deadline", header: tx("deadline", "Deadline"), value: (row) => cell(row.deadline) },
    { key: "stage", header: tx("filterStage", "Etape"), value: (row) => stageLabel(row, tx) },
    { key: "score", header: tx("exportColScore", "Score"), value: (row) => cell(row.score) },
    { key: "go", header: tx("filterGoPick", "GO / NO-GO"), value: (row) => goLabel(row, tx) },
    { key: "goPct", header: tx("exportColGoPct", "GO %"), value: (row) => cell(row.go_pct) },
    { key: "goReason", header: tx("exportColGoReason", "Motif"), value: (row) => cell(row.go_reason) },
    { key: "budget", header: tx("exportColBudget", "Budget"), value: (row) => cell(row.budget) },
    { key: "currency", header: tx("filterCurrency", "Devise"), value: (row) => cell(row.currency) },
    { key: "sector", header: tx("filterDomain", "Domaine"), value: (row) => cell(row.sector) },
    { key: "cpv", header: tx("exportColCpv", "CPV"), value: (row) => cell(row.cpv) },
    {
      key: "favorite",
      header: tx("favoriteBadge", "Favori"),
      value: (row) => (row.favorite ? tx("exportYes", "oui") : ""),
    },
    {
      key: "archived",
      header: tx("archivedBadge", "Archive"),
      value: (row) => (row.archived ? tx("exportYes", "oui") : ""),
    },
  ];
}

export function tableFromNotices(
  rows: TenderNotice[],
  ctx: TendersExportContext,
): { headers: string[]; values: string[][]; sheetName: string } {
  const columns = tendersExportColumns(ctx);
  return {
    headers: columns.map((col) => col.header),
    values: rows.map((row) => columns.map((col) => col.value(row))),
    sheetName: ctx.tx("noticesTitle", "Avis").slice(0, 31) || "Avis",
  };
}

export function tendersExportStem(now = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `tenders-avis-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
}

export function buildTendersExportFile(
  rows: TenderNotice[],
  format: "csv" | "xlsx",
  ctx: TendersExportContext,
  now = new Date(),
): { filename: string; mime: string; bytes: Uint8Array } | null {
  if (!rows.length) return null;
  const table = tableFromNotices(rows, ctx);
  const stem = tendersExportStem(now);
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

export function downloadTendersExport(
  rows: TenderNotice[],
  format: "csv" | "xlsx",
  ctx: TendersExportContext,
): boolean {
  const file = buildTendersExportFile(rows, format, ctx);
  if (!file) return false;
  downloadBytes(file.bytes, file.filename, file.mime);
  return true;
}
