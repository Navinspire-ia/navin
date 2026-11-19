// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * DOM side of review annotations: maps a rendered document to one flat string
 * so a selection becomes a character range, and paints annotated passages
 * with the CSS Custom Highlight API (no change to the rendered markup).
 */

import type { AnnotationRect } from "@/lib/annotations";

/** Chrome inside a document (copy buttons, ...) is not part of its text. */
const IGNORED = "button, style, script, [data-annotation-ignore]";

export interface TextIndex {
  text: string;
  nodes: Text[];
  /** Offset of each node in `text`. */
  starts: number[];
}

export function buildTextIndex(root: Element): TextIndex {
  const nodes: Text[] = [];
  const starts: number[] = [];
  let text = "";
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const value = node.nodeValue ?? "";
    if (!value || node.parentElement?.closest(IGNORED)) continue;
    nodes.push(node as Text);
    starts.push(text.length);
    text += value;
  }
  return { text, nodes, starts };
}

/** Character offset of a DOM boundary point inside the indexed text. */
export function pointOffset(index: TextIndex, root: Element, node: Node, offset: number): number {
  const probe = root.ownerDocument.createRange();
  probe.setStart(root, 0);
  try {
    probe.setEnd(node, offset);
  } catch {
    return -1;
  }
  for (let i = 0; i < index.nodes.length; i += 1) {
    const text = index.nodes[i];
    if (text === node) return index.starts[i] + Math.min(offset, text.length);
    if (probe.comparePoint(text, 0) > 0) return index.starts[i];
  }
  return index.text.length;
}

export function rangeFromOffsets(index: TextIndex, start: number, end: number): Range | null {
  if (start < 0 || end <= start || end > index.text.length) return null;
  let startNode: Text | null = null;
  let startOffset = 0;
  let endNode: Text | null = null;
  let endOffset = 0;
  for (let i = 0; i < index.nodes.length; i += 1) {
    const from = index.starts[i];
    const to = from + index.nodes[i].length;
    if (!startNode && start >= from && start < to) {
      startNode = index.nodes[i];
      startOffset = start - from;
    }
    if (end > from && end <= to) {
      endNode = index.nodes[i];
      endOffset = end - from;
      break;
    }
  }
  if (!startNode || !endNode) return null;
  const range = startNode.ownerDocument.createRange();
  range.setStart(startNode, startOffset);
  range.setEnd(endNode, endOffset);
  return range;
}

export function rectOf(target: Range | Element): AnnotationRect {
  // jsdom has no layout: a range there cannot report a box.
  const box = typeof target.getBoundingClientRect === "function"
    ? target.getBoundingClientRect()
    : { left: 0, top: 0, right: 0, bottom: 0 };
  return { left: box.left, top: box.top, right: box.right, bottom: box.bottom };
}

// ---------------------------------------------------------------------------
// Highlights
// ---------------------------------------------------------------------------

const MARK = "navin-annotation";
const KEEP = "navin-annotation-keep";

type HighlightCtor = new (...ranges: Range[]) => unknown;
interface HighlightRegistry {
  set(name: string, highlight: unknown): void;
  delete(name: string): void;
}

const layers = new Map<object, { mark: Range[]; keep: Range[] }>();

function paint(name: string, ranges: Range[]): void {
  const env = globalThis as { Highlight?: HighlightCtor; CSS?: { highlights?: HighlightRegistry } };
  if (!env.Highlight || !env.CSS?.highlights) return;
  if (ranges.length) env.CSS.highlights.set(name, new env.Highlight(...ranges));
  else env.CSS.highlights.delete(name);
}

/** Declares the passages one layer wants painted; empty lists release them. */
export function setLayerHighlights(owner: object, mark: Range[], keep: Range[]): void {
  if (!mark.length && !keep.length) {
    if (!layers.delete(owner)) return;
  } else {
    layers.set(owner, { mark, keep });
  }
  const all = [...layers.values()];
  paint(MARK, all.flatMap((layer) => layer.mark));
  paint(KEEP, all.flatMap((layer) => layer.keep));
}
