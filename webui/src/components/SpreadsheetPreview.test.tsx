// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { detectCsvDelimiter, parseCsv } from "./FilePreviewBody";
import { SpreadsheetPreview, initialSheetIndex, spreadsheetColumnLabel } from "./SpreadsheetPreview";

describe("SpreadsheetPreview", () => {
  it("renders the first sheet with cells as a table", () => {
    const html = renderToStaticMarkup(
      <SpreadsheetPreview
        sheets={[
          { name: "Vide", rows: [] },
          {
            name: "Catalogue",
            rows: [
              ["id", "nom", "prix"],
              ["1", "Oud Royal", "120"],
              ["2", "Jasmin", "85.5"],
            ],
          },
        ]}
      />,
    );
    expect(html).toContain("Oud Royal");
    expect(html).toContain("3 rows x 3 columns");
    expect(html).toContain('aria-selected="true"');
    expect(html).toContain(">Catalogue<");
    expect(html).toContain(">Vide<");
  });

  it("uses letters as headers when the first row is not a header", () => {
    const html = renderToStaticMarkup(
      <SpreadsheetPreview sheets={[{ name: "S", rows: [["1", ""], ["2", "x"]] }]} />,
    );
    expect(html).toContain(">A<");
    expect(html).toContain(">B<");
  });

  it("flags partial views and empty sheets", () => {
    expect(
      renderToStaticMarkup(<SpreadsheetPreview sheets={[{ name: "S", rows: [["a"]], truncated: true }]} />),
    ).toContain("partial view");
    expect(renderToStaticMarkup(<SpreadsheetPreview sheets={[{ name: "S", rows: [] }]} />)).toContain(
      "This sheet is empty.",
    );
  });

  it("labels columns like a spreadsheet", () => {
    expect(spreadsheetColumnLabel(0)).toBe("A");
    expect(spreadsheetColumnLabel(25)).toBe("Z");
    expect(spreadsheetColumnLabel(26)).toBe("AA");
    expect(spreadsheetColumnLabel(27)).toBe("AB");
  });

  it("starts on the first visible sheet that has cells", () => {
    expect(initialSheetIndex([{ name: "a", rows: [], hidden: true }, { name: "b", rows: [["x"]] }])).toBe(1);
    expect(initialSheetIndex([{ name: "a", rows: [] }])).toBe(0);
    expect(initialSheetIndex([])).toBe(0);
  });
});

describe("CSV delimiter detection", () => {
  it("reads semicolon and tab separated files", () => {
    expect(detectCsvDelimiter("id;nom;prix\n1;Oud;12,5\n")).toBe(";");
    expect(detectCsvDelimiter("id\tnom\n1\tOud\n")).toBe("\t");
    expect(detectCsvDelimiter("id,nom\n")).toBe(",");
    expect(parseCsv("id;nom;prix\n1;Oud Royal;12,5\n")).toEqual([
      ["id", "nom", "prix"],
      ["1", "Oud Royal", "12,5"],
    ]);
  });

  it("ignores delimiters inside quotes", () => {
    expect(detectCsvDelimiter('"a;b",c\n')).toBe(",");
  });
});
