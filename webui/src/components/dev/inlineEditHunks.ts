// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { diffLines } from "diff";

/** One contiguous equal or changed region inside a Cmd+K rewrite. */
export type InlineEditHunk = {
  id: string;
  kind: "equal" | "change";
  oldText: string;
  newText: string;
};

function splitKeepEnds(text: string): string[] {
  if (!text) return [];
  const parts = text.split(/(?<=\n)/);
  // Drop a trailing empty slot that split can leave after a final newline.
  if (parts.length && parts[parts.length - 1] === "") parts.pop();
  return parts;
}

/**
 * Split `before` → `after` into line hunks so the user can accept/reject
 * individual changes before applying a Cmd+K edit.
 *
 * Changed regions are expanded to per-line pairs when possible so adjacent
 * edits stay independently toggleable.
 */
export function buildInlineEditHunks(before: string, after: string): InlineEditHunk[] {
  const parts = diffLines(before, after);
  const hunks: InlineEditHunk[] = [];
  let i = 0;
  let seq = 0;

  const pushEqual = (text: string) => {
    if (!text) return;
    hunks.push({
      id: `eq-${seq++}`,
      kind: "equal",
      oldText: text,
      newText: text,
    });
  };

  const pushChange = (oldText: string, newText: string) => {
    if (oldText === newText) {
      pushEqual(oldText);
      return;
    }
    const oldLines = splitKeepEnds(oldText);
    const newLines = splitKeepEnds(newText);
    const pairCount = Math.min(oldLines.length, newLines.length);
    for (let p = 0; p < pairCount; p += 1) {
      const left = oldLines[p] ?? "";
      const right = newLines[p] ?? "";
      if (left === right) pushEqual(left);
      else {
        hunks.push({
          id: `ch-${seq++}`,
          kind: "change",
          oldText: left,
          newText: right,
        });
      }
    }
    for (let p = pairCount; p < oldLines.length; p += 1) {
      hunks.push({
        id: `ch-${seq++}`,
        kind: "change",
        oldText: oldLines[p] ?? "",
        newText: "",
      });
    }
    for (let p = pairCount; p < newLines.length; p += 1) {
      hunks.push({
        id: `ch-${seq++}`,
        kind: "change",
        oldText: "",
        newText: newLines[p] ?? "",
      });
    }
  };

  while (i < parts.length) {
    const part = parts[i]!;
    if (!part.added && !part.removed) {
      pushEqual(part.value);
      i += 1;
      continue;
    }

    let oldText = "";
    let newText = "";
    while (i < parts.length && (parts[i]!.added || parts[i]!.removed)) {
      const chunk = parts[i]!;
      if (chunk.removed) oldText += chunk.value;
      if (chunk.added) newText += chunk.value;
      i += 1;
    }
    pushChange(oldText, newText);
  }

  return hunks;
}

/** Rebuild the final replacement from accepted change hunks (equals always kept). */
export function composeInlineEdit(
  hunks: InlineEditHunk[],
  acceptedIds: ReadonlySet<string>,
): string {
  let out = "";
  for (const hunk of hunks) {
    if (hunk.kind === "equal") {
      out += hunk.oldText;
      continue;
    }
    out += acceptedIds.has(hunk.id) ? hunk.newText : hunk.oldText;
  }
  return out;
}

export function defaultAcceptedHunkIds(hunks: InlineEditHunk[]): Set<string> {
  return new Set(hunks.filter((h) => h.kind === "change").map((h) => h.id));
}

export function countChangedHunks(hunks: InlineEditHunk[]): number {
  return hunks.reduce((n, h) => n + (h.kind === "change" ? 1 : 0), 0);
}
