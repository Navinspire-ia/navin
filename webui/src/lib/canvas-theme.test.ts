// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  CANVAS_CSP,
  CANVAS_MARKER,
  CANVAS_THEME_VALUES,
  canvasThemeStyle,
  isCanvasHtml,
  prepareCanvasDocument,
} from "@/lib/canvas-theme";

function cssBlock(css: string, selector: string): string {
  const start = css.indexOf(`${selector} {`);
  expect(start).toBeGreaterThanOrEqual(0);
  return css.slice(start, css.indexOf("}", start));
}

describe("canvas theme", () => {
  it("mirrors the globals.css tokens for both themes", () => {
    const css = readFileSync(resolve(__dirname, "../globals.css"), "utf8");
    const blocks = { light: cssBlock(css, ":root"), dark: cssBlock(css, ".dark") };
    for (const mode of ["light", "dark"] as const) {
      for (const [name, value] of Object.entries(CANVAS_THEME_VALUES[mode])) {
        const match = new RegExp(`${name}:\\s*([^;]+);`).exec(blocks[mode]);
        expect(match?.[1]?.trim(), `${mode} ${name}`).toBe(value);
      }
    }
  });

  it("detects canvas documents by marker only", () => {
    expect(isCanvasHtml(`<head>${CANVAS_MARKER}</head>`)).toBe(true);
    expect(isCanvasHtml("<html><body>plain</body></html>")).toBe(false);
    expect(isCanvasHtml(undefined)).toBe(false);
  });

  it("puts the CSP first in head and the theme last", () => {
    const doc = `<!doctype html><html><head><meta charset="utf-8">${CANVAS_MARKER}<style>a{}</style></head><body></body></html>`;
    const out = prepareCanvasDocument(doc, "dark");
    const head = out.slice(out.indexOf("<head>") + 6);
    expect(head.startsWith(`<meta http-equiv="Content-Security-Policy" content="${CANVAS_CSP}">`)).toBe(true);
    const themeAt = out.indexOf('id="navin-host-theme"');
    expect(themeAt).toBeGreaterThan(out.indexOf("<style>a{}</style>"));
    expect(themeAt).toBeLessThan(out.indexOf("</head>"));
    expect(out).toContain("--nv-bg: hsl(0 0% 8%);");
    expect(out).toContain("color-scheme: dark");
  });

  it("emits flat tokens only", () => {
    const style = canvasThemeStyle("light");
    expect(style).not.toMatch(/gradient|shadow/i);
    expect(style).toContain("--nv-accent: hsl(214 76% 44%);");
  });
});
