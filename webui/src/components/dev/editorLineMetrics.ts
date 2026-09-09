// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Keep CodeMirror line boxes on an integer pixel grid.
 *
 * Inherited `line-height: 1.5` (Tailwind on html) plus a 13px mono font
 * yields 19.5px lines. WKWebView / macOS then paints one box and hit-tests
 * another, so the active-line background lands on the row below the pointer.
 *
 * Gutters are a flex column. CodeMirror's first child is a hidden spacer
 * with inline `height: 0` (it only reserves width for "999"). Forcing
 * `height` / `min-height: 20px` on `.cm-gutterElement` inflates that spacer
 * by one row, so `cm-activeLineGutter` paints on N+1 while the caret stays
 * on N.
 */
import { EditorView, ViewPlugin } from "@codemirror/view";
import type { Extension } from "@codemirror/state";

export const EDITOR_FONT_SIZE_PX = 13;
export const EDITOR_LINE_HEIGHT_PX = 20;

export type LineBox = { top: number; bottom: number };

export function readDocumentCssZoom(
  styleZoom: string | null | undefined =
    typeof document === "undefined"
      ? "1"
      : getComputedStyle(document.documentElement).zoom,
): number {
  if (!styleZoom || styleZoom === "normal") return 1;
  const value = Number.parseFloat(styleZoom);
  return Number.isFinite(value) && value > 0 ? value : 1;
}

/**
 * Safari / WKWebView: `clientX/Y` ignore CSS `zoom` on `<html>`, while
 * `getBoundingClientRect` (what CodeMirror uses) includes it. Divide so
 * `posAtCoords` lands on the line under the pointer.
 */
export function pointerCoordsForEditor(
  clientX: number,
  clientY: number,
  zoom = readDocumentCssZoom(),
): { x: number; y: number } {
  if (!Number.isFinite(zoom) || zoom <= 0 || Math.abs(zoom - 1) < 0.001) {
    return { x: clientX, y: clientY };
  }
  return { x: clientX / zoom, y: clientY / zoom };
}

/**
 * Map a pointer Y onto the painted line box that contains it. Using the
 * centre of that box keeps `posAtCoords` (height map) on the same row the
 * user sees, even when the map is a fraction of a pixel off.
 */
export function snapClientYToPaintedLines(
  clientY: number,
  boxes: readonly LineBox[],
): number {
  for (const box of boxes) {
    if (clientY >= box.top && clientY < box.bottom) {
      return (box.top + box.bottom) / 2;
    }
  }
  return clientY;
}

export function collectPaintedLineBoxes(root: ParentNode | null | undefined): LineBox[] {
  if (!root) return [];
  const boxes: LineBox[] = [];
  root.querySelectorAll(".cm-line").forEach((node) => {
    const rect = (node as HTMLElement).getBoundingClientRect();
    boxes.push({ top: rect.top, bottom: rect.bottom });
  });
  return boxes;
}

type GutterStyle = {
  visibility?: string;
  height: string;
  minHeight: string;
  maxHeight: string;
  marginTop: string;
  marginBottom: string;
  paddingTop: string;
  paddingBottom: string;
  overflow: string;
  lineHeight: string;
};

type GutterNode = {
  classList: { contains: (token: string) => boolean };
  style: GutterStyle;
};

type GutterColumn = { firstElementChild: unknown };
type GutterTree = { querySelectorAll: (selectors: string) => ArrayLike<GutterColumn> };

export function isHiddenGutterSpacer(el: unknown): el is GutterNode {
  if (!el || typeof el !== "object") return false;
  const node = el as Partial<GutterNode>;
  if (typeof node.classList?.contains !== "function") return false;
  if (!node.classList.contains("cm-gutterElement")) return false;
  return node.style?.visibility === "hidden";
}

/** Collapse CodeMirror's width-only gutter spacer so it cannot eat a line. */
export function collapseGutterSpacers(root: GutterTree | null | undefined): number {
  if (!root) return 0;
  let collapsed = 0;
  const gutters = Array.from(root.querySelectorAll(".cm-gutter"));
  for (const gutter of gutters) {
    const spacer = gutter.firstElementChild;
    if (!isHiddenGutterSpacer(spacer)) continue;
    spacer.style.height = "0px";
    spacer.style.minHeight = "0px";
    spacer.style.maxHeight = "0px";
    spacer.style.marginTop = "0px";
    spacer.style.marginBottom = "0px";
    spacer.style.paddingTop = "0px";
    spacer.style.paddingBottom = "0px";
    spacer.style.overflow = "hidden";
    spacer.style.lineHeight = "0";
    collapsed += 1;
  }
  return collapsed;
}

const LINE = `${EDITOR_LINE_HEIGHT_PX}px`;
const FONT = `${EDITOR_FONT_SIZE_PX}px`;

const metricsTheme = EditorView.theme({
  "&": {
    fontSize: FONT,
    isolation: "isolate",
  },
  ".cm-scroller": {
    fontFamily:
      'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
    lineHeight: LINE,
  },
  ".cm-content": {
    fontSize: FONT,
    lineHeight: LINE,
    padding: "0",
  },
  ".cm-line": {
    fontSize: FONT,
    lineHeight: LINE,
    minHeight: LINE,
    padding: "0 4px 0 8px",
  },
  ".cm-gutters": {
    fontSize: FONT,
    lineHeight: LINE,
    padding: "0",
  },
  // Do not set height / min-height to the line box. That inflates the
  // hidden spacer (see file comment) and shifts cm-activeLineGutter by 1.
  ".cm-gutterElement": {
    lineHeight: LINE,
    minHeight: "0",
    paddingTop: "0",
    paddingBottom: "0",
    boxSizing: "border-box",
  },
});

const measureAfterFonts = ViewPlugin.fromClass(
  class {
    constructor(view: EditorView) {
      const measure = () => {
        collapseGutterSpacers(view.dom);
        view.requestMeasure();
      };
      void document.fonts?.ready.then(measure);
      requestAnimationFrame(measure);
    }
  },
);

const gutterSpacerFix = ViewPlugin.fromClass(
  class {
    constructor(readonly view: EditorView) {
      collapseGutterSpacers(view.dom);
    }

    update() {
      collapseGutterSpacers(this.view.dom);
    }
  },
);

const webkitZoomPointerFix = ViewPlugin.fromClass(
  class {
    private readonly onPointer: (event: MouseEvent) => void;

    constructor(readonly view: EditorView) {
      this.onPointer = (event: MouseEvent) => {
        const zoom = readDocumentCssZoom();
        const coords = pointerCoordsForEditor(event.clientX, event.clientY, zoom);
        const snappedY = snapClientYToPaintedLines(
          coords.y,
          collectPaintedLineBoxes(view.contentDOM),
        );
        if (
          Math.abs(coords.x - event.clientX) < 0.001 &&
          Math.abs(snappedY - event.clientY) < 0.001
        ) {
          return;
        }
        try {
          Object.defineProperty(event, "clientX", {
            configurable: true,
            value: coords.x,
          });
          Object.defineProperty(event, "clientY", {
            configurable: true,
            value: snappedY,
          });
        } catch {
          // Some WebKit builds freeze clientX/Y. Line metrics still apply.
        }
      };
      view.scrollDOM.addEventListener("mousedown", this.onPointer, true);
      view.scrollDOM.addEventListener("mousemove", this.onPointer, true);
      view.scrollDOM.addEventListener("mouseup", this.onPointer, true);
    }

    destroy() {
      this.view.scrollDOM.removeEventListener("mousedown", this.onPointer, true);
      this.view.scrollDOM.removeEventListener("mousemove", this.onPointer, true);
      this.view.scrollDOM.removeEventListener("mouseup", this.onPointer, true);
    }
  },
);

export function editorLineMetrics(): Extension {
  return [metricsTheme, measureAfterFonts, gutterSpacerFix, webkitZoomPointerFix];
}
