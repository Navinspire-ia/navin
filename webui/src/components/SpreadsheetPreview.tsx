// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";
import type { SpreadsheetSheetPayload } from "@/lib/types";

/** Column header as a spreadsheet shows it: A, B, ..., Z, AA, AB, ... */
export function spreadsheetColumnLabel(index: number): string {
  let n = index + 1;
  let label = "";
  while (n > 0) {
    const rem = (n - 1) % 26;
    label = String.fromCharCode(65 + rem) + label;
    n = Math.floor((n - 1) / 26);
  }
  return label;
}

/** Index of the sheet to show first: the first visible one with cells. */
export function initialSheetIndex(sheets: readonly SpreadsheetSheetPayload[]): number {
  const withCells = sheets.findIndex((s) => !s.hidden && s.rows.length > 0);
  if (withCells >= 0) return withCells;
  const visible = sheets.findIndex((s) => !s.hidden);
  return visible >= 0 ? visible : 0;
}

interface SpreadsheetPreviewProps {
  sheets: SpreadsheetSheetPayload[];
  /** Cap on rows painted at once; the header line reports the rest. */
  maxRows?: number;
  className?: string;
  /** Dense rows for the editor pane, roomier ones for the chat panel. */
  density?: "compact" | "comfortable";
  testId?: string;
}

/**
 * Excel workbook as tabs of tables, read from the gateway's own cell dump:
 * opens instantly and needs nothing installed. The first row is shown as the
 * header when it looks like one (all cells filled), else as a plain row.
 */
export function SpreadsheetPreview({
  sheets,
  maxRows = 1000,
  className,
  density = "compact",
  testId = "spreadsheet-preview",
}: SpreadsheetPreviewProps) {
  const { t } = useTranslation();
  const [active, setActive] = useState(() => initialSheetIndex(sheets));
  useEffect(() => {
    setActive(initialSheetIndex(sheets));
  }, [sheets]);

  const sheet = sheets[Math.min(active, Math.max(sheets.length - 1, 0))];
  const rows = sheet?.rows ?? [];
  const width = useMemo(() => rows.reduce((max, r) => Math.max(max, r.length), 0), [rows]);
  const headerLooksNamed = rows.length > 1 && (rows[0] ?? []).every((cell) => cell.trim() !== "");
  const body = rows.slice(0, maxRows);
  const hiddenRows = Math.max(0, rows.length - body.length);
  const cellPad = density === "compact" ? "px-2.5 py-1" : "px-3 py-1.5";

  if (!sheet) {
    return (
      <div className={cn("flex flex-1 items-center justify-center p-6 text-[13px] text-muted-foreground", className)}>
        {t("filePreview.spreadsheetEmpty", { defaultValue: "This workbook has no sheets." })}
      </div>
    );
  }

  return (
    <div className={cn("flex min-h-0 flex-1 flex-col", className)} data-testid={testId}>
      <div className="flex items-center justify-between gap-3 border-b border-border/50 px-3 py-1.5">
        <div className="flex min-w-0 items-center gap-1 overflow-x-auto" role="tablist">
          {sheets.map((s, index) => (
            <button
              key={`${s.name}-${index}`}
              type="button"
              role="tab"
              aria-selected={index === active}
              onClick={() => setActive(index)}
              className={cn(
                "shrink-0 rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors",
                index === active
                  ? "bg-foreground/[0.08] text-foreground"
                  : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                s.hidden && "italic opacity-70",
              )}
              title={s.hidden ? t("filePreview.sheetHidden", { defaultValue: "Hidden sheet" }) : s.name}
            >
              {s.name}
            </button>
          ))}
        </div>
        <p className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
          {t("filePreview.sheetSize", {
            defaultValue: "{{rows}} rows x {{cols}} columns",
            rows: rows.length,
            cols: width,
          })}
          {sheet.truncated || hiddenRows > 0
            ? ` - ${t("filePreview.sheetTruncated", { defaultValue: "partial view, download for the whole file" })}`
            : ""}
        </p>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {rows.length === 0 ? (
          <p className="p-6 text-center text-[13px] text-muted-foreground">
            {t("filePreview.sheetEmpty", { defaultValue: "This sheet is empty." })}
          </p>
        ) : (
          <table className="w-max min-w-full border-collapse text-left text-xs">
            <thead className="sticky top-0 z-[1] bg-muted/90 backdrop-blur">
              <tr>
                <th className={cn("border-b border-r border-border/60 text-right font-normal text-muted-foreground/70", cellPad)}>
                  {"\u00a0"}
                </th>
                {Array.from({ length: width }, (_, index) => (
                  <th
                    key={`col-${index}`}
                    className={cn("border-b border-border/60 font-semibold text-foreground", cellPad)}
                  >
                    {headerLooksNamed ? rows[0]?.[index] || spreadsheetColumnLabel(index) : spreadsheetColumnLabel(index)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(headerLooksNamed ? body.slice(1) : body).map((row, rowIndex) => {
                const lineNo = rowIndex + (headerLooksNamed ? 2 : 1);
                return (
                  <tr key={`r-${lineNo}`} className={cn(rowIndex % 2 === 1 && "bg-muted/20")}>
                    <td className={cn("border-b border-r border-border/30 text-right font-mono text-[10px] text-muted-foreground/60", cellPad)}>
                      {lineNo}
                    </td>
                    {Array.from({ length: width }, (_, cellIndex) => {
                      const value = row[cellIndex] ?? "";
                      const numeric = /^-?\d[\d\s.,]*%?$/.test(value.trim()) && value.trim() !== "";
                      return (
                        <td
                          key={`c-${lineNo}-${cellIndex}`}
                          className={cn(
                            "max-w-[28rem] truncate border-b border-border/30 text-muted-foreground",
                            numeric && "text-right tabular-nums",
                            cellPad,
                          )}
                          title={value.length > 40 ? value : undefined}
                        >
                          {value}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
