import type { CrmRecord } from "@/lib/api";
import { countryLabel } from "@/lib/crm-catalog";
import {
  companyName,
  contactName,
  displayText,
  formatWhen,
  leadName,
  opportunityName,
} from "@/lib/crm-format";

export type CrmExportKind = "contacts" | "leads" | "opportunities";

export type CrmExportColumn = {
  key: string;
  header: string;
  value: (row: CrmRecord) => string;
};

export type CrmExportTx = (key: string, fallback: string) => string;

export type CrmExportContext = {
  companies?: CrmRecord[];
  contacts?: CrmRecord[];
  locale?: string;
  tx: CrmExportTx;
};

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i += 1) {
    let value = i;
    for (let bit = 0; bit < 8; bit += 1) {
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    }
    table[i] = value >>> 0;
  }
  return table;
})();

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i += 1) {
    crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function tagsOf(row: CrmRecord): string {
  if (Array.isArray(row.tags)) return row.tags.map((item) => displayText(item)).filter(Boolean).join(", ");
  return displayText(row.tags);
}

function companyOf(row: CrmRecord, companies: CrmRecord[] = []): string {
  const linked = companies.find((item) => item.id === row.companyId);
  return companyName(linked) || displayText(row.company);
}

function contactNamesOf(row: CrmRecord, contacts: CrmRecord[] = []): string {
  const ids = Array.isArray(row.contactIds) ? row.contactIds : [];
  return ids
    .map((id) => contactName(contacts.find((item) => item.id === String(id))))
    .filter(Boolean)
    .join(", ");
}

function langOf(locale: string): "fr" | "en" {
  return locale.toLowerCase().startsWith("en") ? "en" : "fr";
}

export function csvEscape(value: string, separator: string): string {
  const text = value.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  if (text.includes('"') || text.includes("\n") || text.includes(separator)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

export function buildCsv(headers: string[], rows: string[][], separator = ","): string {
  const lines = [headers, ...rows].map((line) => line.map((cell) => csvEscape(cell, separator)).join(separator));
  return `\uFEFF${lines.join("\r\n")}\r\n`;
}

function xmlEscape(value: string): string {
  return sanitizeXmlText(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Excel rejects XML 1.0 control chars and reports a corrupt workbook. */
export function sanitizeXmlText(value: string): string {
  // eslint-disable-next-line no-control-regex -- these exact code points are what XML 1.0 forbids
  return value.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "");
}

function labeled(tx: CrmExportTx, key: string, raw: string): string {
  const value = displayText(raw);
  if (!value) return "";
  return tx(key, value);
}

function u16(value: number): Uint8Array {
  const out = new Uint8Array(2);
  out[0] = value & 0xff;
  out[1] = (value >>> 8) & 0xff;
  return out;
}

function u32(value: number): Uint8Array {
  const out = new Uint8Array(4);
  out[0] = value & 0xff;
  out[1] = (value >>> 8) & 0xff;
  out[2] = (value >>> 16) & 0xff;
  out[3] = (value >>> 24) & 0xff;
  return out;
}

function concatBytes(parts: Uint8Array[]): Uint8Array {
  const size = parts.reduce((sum, part) => sum + part.length, 0);
  const out = new Uint8Array(size);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

function zipStore(files: Array<{ name: string; data: Uint8Array }>): Uint8Array {
  const encoder = new TextEncoder();
  const locals: Uint8Array[] = [];
  const centrals: Uint8Array[] = [];
  let offset = 0;
  for (const file of files) {
    const name = encoder.encode(file.name);
    const crc = crc32(file.data);
    const local = concatBytes([
      u32(0x04034b50),
      u16(20),
      u16(0),
      u16(0),
      u16(0),
      u16(0),
      u32(crc),
      u32(file.data.length),
      u32(file.data.length),
      u16(name.length),
      u16(0),
      name,
      file.data,
    ]);
    const central = concatBytes([
      u32(0x02014b50),
      u16(20),
      u16(20),
      u16(0),
      u16(0),
      u16(0),
      u16(0),
      u32(crc),
      u32(file.data.length),
      u32(file.data.length),
      u16(name.length),
      u16(0),
      u16(0),
      u16(0),
      u16(0),
      u32(0),
      u32(offset),
      name,
    ]);
    locals.push(local);
    centrals.push(central);
    offset += local.length;
  }
  const localBlob = concatBytes(locals);
  const centralBlob = concatBytes(centrals);
  const eocd = concatBytes([
    u32(0x06054b50),
    u16(0),
    u16(0),
    u16(files.length),
    u16(files.length),
    u32(centralBlob.length),
    u32(localBlob.length),
    u16(0),
  ]);
  return concatBytes([localBlob, centralBlob, eocd]);
}

function cellRef(col: number, row: number): string {
  let name = "";
  let index = col;
  while (index >= 0) {
    name = String.fromCharCode((index % 26) + 65) + name;
    index = Math.floor(index / 26) - 1;
  }
  return `${name}${row}`;
}

export function buildXlsxBytes(sheetName: string, headers: string[], rows: string[][]): Uint8Array {
  const encoder = new TextEncoder();
  const safeName = (sheetName || "Export").replace(/[:\\/?*[\]]/g, " ").slice(0, 31) || "Export";
  const cells = [headers, ...rows]
    .map((line, rowIndex) => {
      const xml = line
        .map((value, colIndex) => {
          const text = xmlEscape(value);
          return `<c r="${cellRef(colIndex, rowIndex + 1)}" t="inlineStr"><is><t xml:space="preserve">${text}</t></is></c>`;
        })
        .join("");
      return `<row r="${rowIndex + 1}">${xml}</row>`;
    })
    .join("");
  const sheet = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${cells}</sheetData></worksheet>`;
  const workbook = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="${xmlEscape(safeName)}" sheetId="1" r:id="rId1"/></sheets></workbook>`;
  const rels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`;
  const workbookRels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>`;
  const types = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>`;
  return zipStore([
    { name: "[Content_Types].xml", data: encoder.encode(types) },
    { name: "_rels/.rels", data: encoder.encode(rels) },
    { name: "xl/workbook.xml", data: encoder.encode(workbook) },
    { name: "xl/_rels/workbook.xml.rels", data: encoder.encode(workbookRels) },
    { name: "xl/worksheets/sheet1.xml", data: encoder.encode(sheet) },
  ]);
}

export function exportColumns(kind: CrmExportKind, ctx: CrmExportContext): CrmExportColumn[] {
  const tx = ctx.tx;
  const locale = ctx.locale || "fr-FR";
  const lang = langOf(locale);
  const companies = ctx.companies || [];
  const contacts = ctx.contacts || [];
  const country = (row: CrmRecord) => {
    const code = displayText(row.country);
    return code ? countryLabel(code, lang) || code : "";
  };
  const when = (row: CrmRecord) => formatWhen(row.createdAt as number, locale);
  if (kind === "contacts") {
    return [
      { key: "firstName", header: tx("crm.firstName", "Prenom"), value: (row) => displayText(row.firstName) },
      { key: "lastName", header: tx("crm.lastName", "Nom"), value: (row) => displayText(row.lastName) },
      { key: "title", header: tx("crm.title", "Titre"), value: (row) => displayText(row.title) },
      { key: "email", header: tx("crm.email", "Email"), value: (row) => displayText(row.email) },
      { key: "phone", header: tx("crm.phone", "Telephone"), value: (row) => displayText(row.phone) },
      { key: "whatsapp", header: tx("crm.whatsapp", "WhatsApp"), value: (row) => displayText(row.whatsapp) },
      { key: "linkedin", header: tx("crm.linkedin", "LinkedIn"), value: (row) => displayText(row.linkedin) },
      { key: "company", header: tx("crm.companyName", "Entreprise"), value: (row) => companyOf(row, companies) },
      { key: "country", header: tx("crm.country", "Pays"), value: country },
      { key: "owner", header: tx("crm.owner", "Proprietaire"), value: (row) => displayText(row.owner) },
      { key: "source", header: tx("crm.source", "Source"), value: (row) => displayText(row.source) },
      { key: "status", header: tx("crm.status", "Statut"), value: (row) => labeled(tx, `crm.statuses.${displayText(row.status)}`, displayText(row.status)) },
      { key: "tags", header: tx("crm.tag", "Tag"), value: tagsOf },
      { key: "createdAt", header: tx("crm.createdAt", "Cree le"), value: when },
    ];
  }
  if (kind === "leads") {
    return [
      { key: "name", header: tx("crm.leadName", "Nom"), value: (row) => leadName(row) },
      { key: "company", header: tx("crm.companyName", "Entreprise"), value: (row) => displayText(row.company) },
      { key: "email", header: tx("crm.email", "Email"), value: (row) => displayText(row.email) },
      { key: "phone", header: tx("crm.phone", "Telephone"), value: (row) => displayText(row.phone) },
      { key: "country", header: tx("crm.country", "Pays"), value: country },
      { key: "source", header: tx("crm.source", "Source"), value: (row) => displayText(row.source) },
      { key: "score", header: tx("crm.score", "Score"), value: (row) => displayText(row.score) },
      { key: "owner", header: tx("crm.owner", "Proprietaire"), value: (row) => displayText(row.owner) },
      { key: "status", header: tx("crm.status", "Statut"), value: (row) => labeled(tx, `crm.leadStages.${displayText(row.status)}`, displayText(row.status)) },
      { key: "convertedOpportunityId", header: tx("crm.linkDeal", "Opportunite"), value: (row) => displayText(row.convertedOpportunityId) },
      { key: "createdAt", header: tx("crm.createdAt", "Cree le"), value: when },
    ];
  }
  return [
    { key: "name", header: tx("crm.dealName", "Deal"), value: (row) => opportunityName(row) },
    { key: "amount", header: tx("crm.amount", "Montant"), value: (row) => displayText(row.amount) },
    { key: "currency", header: tx("crm.currency", "Devise"), value: (row) => displayText(row.currency) },
    { key: "probability", header: tx("crm.probability", "Probabilite"), value: (row) => displayText(row.probability) },
    { key: "stage", header: tx("crm.stage", "Etape"), value: (row) => labeled(tx, `crm.stages.${displayText(row.stage)}`, displayText(row.stage)) },
    { key: "closeDate", header: tx("crm.closeDate", "Date de cloture"), value: (row) => displayText(row.closeDate) },
    { key: "company", header: tx("crm.companyName", "Entreprise"), value: (row) => companyOf(row, companies) },
    { key: "contacts", header: tx("crm.linkContacts", "Contacts"), value: (row) => contactNamesOf(row, contacts) },
    { key: "owner", header: tx("crm.owner", "Proprietaire"), value: (row) => displayText(row.owner) },
    { key: "source", header: tx("crm.source", "Source"), value: (row) => displayText(row.source) },
    { key: "nextAction", header: tx("crm.nextAction", "Prochaine action"), value: (row) => displayText(row.nextAction) },
    { key: "lossReason", header: tx("crm.lossReason", "Raison de perte"), value: (row) => displayText(row.lossReason) },
    { key: "createdAt", header: tx("crm.createdAt", "Cree le"), value: when },
  ];
}

export function tableFromRows(
  kind: CrmExportKind,
  rows: CrmRecord[],
  ctx: CrmExportContext,
): { headers: string[]; values: string[][]; sheetName: string } {
  const columns = exportColumns(kind, ctx);
  return {
    headers: columns.map((col) => col.header),
    values: rows.map((row) => columns.map((col) => col.value(row))),
    sheetName: ctx.tx(
      `crm.tabs.${kind}`,
      kind === "contacts" ? "Contacts" : kind === "leads" ? "Leads" : "Opportunities",
    ),
  };
}

export function exportFileStem(kind: CrmExportKind, now = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
  return `crm-${kind}-${stamp}`;
}

export function downloadBytes(bytes: Uint8Array, filename: string, mime: string): void {
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  const blob = new Blob([copy], { type: mime });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function buildExportFile(
  kind: CrmExportKind,
  rows: CrmRecord[],
  format: "csv" | "xlsx",
  ctx: CrmExportContext,
  now = new Date(),
): { filename: string; mime: string; bytes: Uint8Array } | null {
  if (!rows.length) return null;
  const table = tableFromRows(kind, rows, ctx);
  const stem = exportFileStem(kind, now);
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

export function downloadCrmExport(
  kind: CrmExportKind,
  rows: CrmRecord[],
  format: "csv" | "xlsx",
  ctx: CrmExportContext,
): boolean {
  const file = buildExportFile(kind, rows, format, ctx);
  if (!file) return false;
  downloadBytes(file.bytes, file.filename, file.mime);
  return true;
}
