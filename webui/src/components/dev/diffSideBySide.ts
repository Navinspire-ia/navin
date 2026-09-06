export type DiffViewMode = "unified" | "split";

export type SideBySideRow = {
  left: string | null;
  right: string | null;
  leftNo: number | null;
  rightNo: number | null;
  kind: "context" | "remove" | "add" | "change" | "meta";
};

type HunkHeader = {
  oldStart: number;
  newStart: number;
};

function parseHunkHeader(line: string): HunkHeader | null {
  const match = /^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@/.exec(line);
  if (!match) return null;
  return {
    oldStart: Number(match[1]),
    newStart: Number(match[2]),
  };
}

/**
 * Build Cursor-style side-by-side rows from a unified git diff.
 * Removals and additions inside a change block are paired when possible.
 */
export function unifiedToSideBySide(diffText: string): SideBySideRow[] {
  const raw = (diffText || "").replace(/\r\n/g, "\n");
  if (!raw.trim()) return [];

  const lines = raw.split("\n");
  const rows: SideBySideRow[] = [];
  let oldNo = 0;
  let newNo = 0;
  let inHunk = false;
  let i = 0;

  const flushChange = (removes: string[], adds: string[]) => {
    const pairCount = Math.min(removes.length, adds.length);
    for (let p = 0; p < pairCount; p += 1) {
      rows.push({
        left: removes[p] ?? "",
        right: adds[p] ?? "",
        leftNo: oldNo,
        rightNo: newNo,
        kind: "change",
      });
      oldNo += 1;
      newNo += 1;
    }
    for (let p = pairCount; p < removes.length; p += 1) {
      rows.push({
        left: removes[p] ?? "",
        right: null,
        leftNo: oldNo,
        rightNo: null,
        kind: "remove",
      });
      oldNo += 1;
    }
    for (let p = pairCount; p < adds.length; p += 1) {
      rows.push({
        left: null,
        right: adds[p] ?? "",
        leftNo: null,
        rightNo: newNo,
        kind: "add",
      });
      newNo += 1;
    }
  };

  while (i < lines.length) {
    const line = lines[i] ?? "";

    if (line.startsWith("diff ") || line.startsWith("index ")) {
      inHunk = false;
      rows.push({
        left: line,
        right: line,
        leftNo: null,
        rightNo: null,
        kind: "meta",
      });
      i += 1;
      continue;
    }
    if (line.startsWith("--- ") || line.startsWith("+++ ")) {
      inHunk = false;
      rows.push({
        left: line.startsWith("--- ") ? line : null,
        right: line.startsWith("+++ ") ? line : null,
        leftNo: null,
        rightNo: null,
        kind: "meta",
      });
      i += 1;
      continue;
    }

    const hunk = parseHunkHeader(line);
    if (hunk) {
      inHunk = true;
      oldNo = hunk.oldStart;
      newNo = hunk.newStart;
      rows.push({
        left: line,
        right: null,
        leftNo: null,
        rightNo: null,
        kind: "meta",
      });
      i += 1;
      continue;
    }

    if (!inHunk) {
      i += 1;
      continue;
    }

    if (line.startsWith("\\")) {
      i += 1;
      continue;
    }

    // Blank trailing line after the last hunk body is common in git output.
    if (line === "" && i === lines.length - 1) {
      i += 1;
      continue;
    }

    if (line.startsWith(" ") || line === "") {
      const body = line.startsWith(" ") ? line.slice(1) : "";
      rows.push({
        left: body,
        right: body,
        leftNo: oldNo,
        rightNo: newNo,
        kind: "context",
      });
      oldNo += 1;
      newNo += 1;
      i += 1;
      continue;
    }

    if (line.startsWith("-") || line.startsWith("+")) {
      const removes: string[] = [];
      const adds: string[] = [];
      while (i < lines.length && (lines[i] ?? "").startsWith("-")) {
        removes.push((lines[i] ?? "").slice(1));
        i += 1;
      }
      while (i < lines.length && (lines[i] ?? "").startsWith("+")) {
        adds.push((lines[i] ?? "").slice(1));
        i += 1;
      }
      flushChange(removes, adds);
      continue;
    }

    // Unknown hunk line: skip rather than abort the whole view.
    i += 1;
  }

  return rows;
}
