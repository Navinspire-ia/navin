// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  EDITOR_LINE_HEIGHT_PX,
  collapseGutterSpacers,
  isHiddenGutterSpacer,
  pointerCoordsForEditor,
  readDocumentCssZoom,
  snapClientYToPaintedLines,
} from "./editorLineMetrics";

const source = readFileSync(
  path.join(path.dirname(fileURLToPath(import.meta.url)), "editorLineMetrics.ts"),
  "utf8",
);

describe("readDocumentCssZoom", () => {
  it("treats missing and normal as 100 %", () => {
    expect(readDocumentCssZoom(null)).toBe(1);
    expect(readDocumentCssZoom("")).toBe(1);
    expect(readDocumentCssZoom("normal")).toBe(1);
  });

  it("parses the CSS zoom factor", () => {
    expect(readDocumentCssZoom("1.25")).toBe(1.25);
    expect(readDocumentCssZoom("0.8")).toBe(0.8);
  });

  it("rejects garbage", () => {
    expect(readDocumentCssZoom("abc")).toBe(1);
    expect(readDocumentCssZoom("0")).toBe(1);
  });
});

describe("pointerCoordsForEditor", () => {
  it("leaves coordinates alone at 100 %", () => {
    expect(pointerCoordsForEditor(100, 200, 1)).toEqual({ x: 100, y: 200 });
  });

  it("undoes CSS zoom so CodeMirror maps the line under the pointer", () => {
    expect(pointerCoordsForEditor(125, 250, 1.25)).toEqual({ x: 100, y: 200 });
  });

  it("keeps line boxes on whole pixels", () => {
    expect(EDITOR_LINE_HEIGHT_PX).toBe(20);
  });
});

describe("snapClientYToPaintedLines", () => {
  const lines = [
    { top: 0, bottom: 20 },
    { top: 20, bottom: 40 },
    { top: 40, bottom: 60 },
    { top: 60, bottom: 80 },
    { top: 80, bottom: 100 },
  ];

  it("snaps a click on visual line 5 to that line's centre, not line 6", () => {
    expect(snapClientYToPaintedLines(81, lines)).toBe(90);
    expect(snapClientYToPaintedLines(99, lines)).toBe(90);
  });

  it("leaves a click between lines unchanged when no box contains it", () => {
    expect(snapClientYToPaintedLines(-4, lines)).toBe(-4);
    expect(snapClientYToPaintedLines(400, lines)).toBe(400);
  });
});

describe("gutter spacer", () => {
  it("does not force a 20px box on every gutter element", () => {
    const block = source.split('".cm-gutterElement"')[1]?.split("}")[0] ?? "";
    expect(block).toMatch(/minHeight:\s*"0"/);
    expect(block).not.toMatch(/height:\s*LINE/);
    expect(block).not.toMatch(/minHeight:\s*LINE/);
  });

  it("collapses the hidden width spacer so it cannot eat a row", () => {
    const spacer = {
      classList: { contains: (token: string) => token === "cm-gutterElement" },
      style: {
        visibility: "hidden",
        height: "20px",
        minHeight: "20px",
        maxHeight: "",
        marginTop: "",
        marginBottom: "",
        paddingTop: "",
        paddingBottom: "",
        overflow: "",
        lineHeight: "20px",
      },
    };
    const line = {
      classList: { contains: (token: string) => token === "cm-gutterElement" },
      style: { visibility: "", height: "" },
    };
    const root = {
      querySelectorAll: (selectors: string) =>
        selectors === ".cm-gutter" ? [{ firstElementChild: spacer }] : [],
    };
    expect(isHiddenGutterSpacer(spacer)).toBe(true);
    expect(isHiddenGutterSpacer(line)).toBe(false);
    expect(collapseGutterSpacers(root)).toBe(1);
    expect(spacer.style.height).toBe("0px");
    expect(spacer.style.minHeight).toBe("0px");
    expect(line.style.height).toBe("");
  });
});
