export type UnifiedLineKind = "meta" | "context" | "add" | "remove" | "gap";

export type UnifiedDiffLine = {
  kind: UnifiedLineKind;
  text: string;
  oldNo: number | null;
  newNo: number | null;
  /** Present on `gap` rows: how many omitted unmodified lines sit here. */
  gapCount?: number;
};

export type ParsedUnifiedDiff = {
  lines: UnifiedDiffLine[];
  binary: boolean;
  renamed: boolean;
  renameFrom: string | null;
  renameTo: string | null;
};

export type DiffBlock =
  | { type: "line"; line: UnifiedDiffLine }
  | {
      type: "unmodified";
      id: string;
      count: number;
      lines: UnifiedDiffLine[];
      omitted: boolean;
    };

export const DEFAULT_DIFF_KEEP_ENDS = 3;

const HUNK_RE = /^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@/;

export function parseHunkHeader(line: string): { oldStart: number; newStart: number } | null {
  const match = HUNK_RE.exec(line);
  if (!match) return null;
  return { oldStart: Number(match[1]), newStart: Number(match[2]) };
}

export function isBinaryDiffText(text: string): boolean {
  return /^(?:Binary files |GIT binary patch)/m.test(text || "");
}

/**
 * Parse a unified git diff into numbered lines, including omitted-gap markers
 * between hunks so the UI can show "N unmodified lines" like Cursor.
 */
export function parseUnifiedDiff(diffText: string): ParsedUnifiedDiff {
  const raw = (diffText || "").replace(/\r\n/g, "\n");
  const binary = isBinaryDiffText(raw);
  const result: ParsedUnifiedDiff = {
    lines: [],
    binary,
    renamed: false,
    renameFrom: null,
    renameTo: null,
  };
  if (!raw.trim()) return result;

  const rows = raw.split("\n");
  let oldNo = 1;
  let newNo = 1;
  let inHunk = false;
  let i = 0;

  const pushGap = (count: number, fromOld: number, fromNew: number) => {
    if (count <= 0) return;
    result.lines.push({
      kind: "gap",
      text: "",
      oldNo: fromOld,
      newNo: fromNew,
      gapCount: count,
    });
  };

  while (i < rows.length) {
    const line = rows[i] ?? "";

    if (line.startsWith("rename from ")) {
      result.renamed = true;
      result.renameFrom = line.slice("rename from ".length);
      result.lines.push({ kind: "meta", text: line, oldNo: null, newNo: null });
      i += 1;
      continue;
    }
    if (line.startsWith("rename to ")) {
      result.renamed = true;
      result.renameTo = line.slice("rename to ".length);
      result.lines.push({ kind: "meta", text: line, oldNo: null, newNo: null });
      i += 1;
      continue;
    }

    if (
      line.startsWith("diff ") ||
      line.startsWith("index ") ||
      line.startsWith("new file mode") ||
      line.startsWith("deleted file mode") ||
      line.startsWith("similarity index") ||
      line.startsWith("dissimilarity index") ||
      line.startsWith("Binary files ") ||
      line.startsWith("GIT binary patch")
    ) {
      inHunk = false;
      result.lines.push({ kind: "meta", text: line, oldNo: null, newNo: null });
      i += 1;
      continue;
    }

    if (line.startsWith("--- ") || line.startsWith("+++ ")) {
      inHunk = false;
      result.lines.push({ kind: "meta", text: line, oldNo: null, newNo: null });
      i += 1;
      continue;
    }

    const hunk = parseHunkHeader(line);
    if (hunk) {
      const skip = Math.max(hunk.oldStart - oldNo, hunk.newStart - newNo, 0);
      if (skip > 0) pushGap(skip, oldNo, newNo);
      inHunk = true;
      oldNo = hunk.oldStart;
      newNo = hunk.newStart;
      result.lines.push({ kind: "meta", text: line, oldNo: null, newNo: null });
      i += 1;
      continue;
    }

    if (!inHunk) {
      if (line === "" && i === rows.length - 1) {
        i += 1;
        continue;
      }
      result.lines.push({ kind: "meta", text: line, oldNo: null, newNo: null });
      i += 1;
      continue;
    }

    if (line.startsWith("\\")) {
      result.lines.push({ kind: "meta", text: line, oldNo: null, newNo: null });
      i += 1;
      continue;
    }

    if (line === "" && i === rows.length - 1) {
      i += 1;
      continue;
    }

    if (line.startsWith(" ") || line === "") {
      const body = line.startsWith(" ") ? line.slice(1) : "";
      result.lines.push({
        kind: "context",
        text: body,
        oldNo,
        newNo,
      });
      oldNo += 1;
      newNo += 1;
      i += 1;
      continue;
    }

    if (line.startsWith("-")) {
      result.lines.push({
        kind: "remove",
        text: line.slice(1),
        oldNo,
        newNo: null,
      });
      oldNo += 1;
      i += 1;
      continue;
    }

    if (line.startsWith("+")) {
      result.lines.push({
        kind: "add",
        text: line.slice(1),
        oldNo: null,
        newNo,
      });
      newNo += 1;
      i += 1;
      continue;
    }

    result.lines.push({ kind: "meta", text: line, oldNo: null, newNo: null });
    i += 1;
  }

  return result;
}

function unmodifiedId(lines: UnifiedDiffLine[], count: number): string {
  const first = lines[0];
  return `um:${first?.oldNo ?? "x"}:${first?.newNo ?? "x"}:${count}`;
}

function pushOmittedGap(line: UnifiedDiffLine, out: DiffBlock[]): void {
  const count = line.gapCount ?? 0;
  if (count <= 0) return;
  const id = unmodifiedId([line], count);
  out.push({
    type: "unmodified",
    id,
    count,
    lines: [],
    omitted: true,
  });
}

function flushUnmodified(
  collected: UnifiedDiffLine[],
  keepBefore: number,
  keepAfter: number,
  expandedIds: ReadonlySet<string>,
  out: DiffBlock[],
): void {
  if (!collected.length) return;

  const keepHead = Math.max(0, keepBefore);
  const keepTail = Math.max(0, keepAfter);
  if (collected.length <= keepHead + keepTail) {
    for (const line of collected) out.push({ type: "line", line });
    return;
  }

  const hideStart = keepHead;
  const hideEnd = collected.length - keepTail;
  const head = collected.slice(0, hideStart);
  const middle = collected.slice(hideStart, hideEnd);
  const tail = collected.slice(hideEnd);

  for (const line of head) out.push({ type: "line", line });

  if (middle.length) {
    const id = unmodifiedId(middle, middle.length);
    if (expandedIds.has(id)) {
      for (const line of middle) out.push({ type: "line", line });
    } else {
      out.push({
        type: "unmodified",
        id,
        count: middle.length,
        lines: middle,
        omitted: false,
      });
    }
  }

  for (const line of tail) out.push({ type: "line", line });
}

/**
 * Collapse long unmodified runs. `keepEnds` context lines stay visible on each
 * side of a change (0 = Collapse All). Gaps that git omitted stay as a single
 * "N unmodified lines" row.
 */
export function buildDiffBlocks(
  lines: UnifiedDiffLine[],
  options: {
    keepEnds?: number;
    expandedIds?: ReadonlySet<string>;
  } = {},
): DiffBlock[] {
  const keepEnds = options.keepEnds ?? DEFAULT_DIFF_KEEP_ENDS;
  const expandedIds = options.expandedIds ?? new Set<string>();
  const out: DiffBlock[] = [];
  let buffer: UnifiedDiffLine[] = [];
  let beforeChange = true;

  const flush = (hitChange: boolean) => {
    const keepBefore = beforeChange ? 0 : keepEnds;
    const keepAfter = hitChange ? keepEnds : 0;
    flushUnmodified(buffer, keepBefore, keepAfter, expandedIds, out);
    buffer = [];
    if (hitChange) beforeChange = false;
  };

  for (const line of lines) {
    if (line.kind === "gap") {
      flush(false);
      pushOmittedGap(line, out);
      continue;
    }
    if (line.kind === "context") {
      buffer.push(line);
      continue;
    }
    if (line.kind === "meta") {
      flush(false);
      out.push({ type: "line", line });
      continue;
    }
    // add / remove
    flush(true);
    out.push({ type: "line", line });
    beforeChange = false;
  }
  flush(false);
  return out;
}

export function visibleLinesFromBlocks(blocks: DiffBlock[]): UnifiedDiffLine[] {
  const lines: UnifiedDiffLine[] = [];
  for (const block of blocks) {
    if (block.type === "line") lines.push(block.line);
  }
  return lines;
}

export function findMatchOffsets(
  text: string,
  query: string,
): { start: number; end: number }[] {
  const needle = query.trim();
  if (!needle) return [];
  const hay = text;
  const lowerHay = hay.toLowerCase();
  const lowerNeedle = needle.toLowerCase();
  const hits: { start: number; end: number }[] = [];
  let from = 0;
  while (from <= lowerHay.length - lowerNeedle.length) {
    const at = lowerHay.indexOf(lowerNeedle, from);
    if (at < 0) break;
    hits.push({ start: at, end: at + needle.length });
    from = at + Math.max(needle.length, 1);
  }
  return hits;
}

export function collectFindHits(
  blocks: DiffBlock[],
  query: string,
): { blockIndex: number; line: UnifiedDiffLine }[] {
  const needle = query.trim();
  if (!needle) return [];
  const hits: { blockIndex: number; line: UnifiedDiffLine }[] = [];
  blocks.forEach((block, blockIndex) => {
    if (block.type !== "line") return;
    if (block.line.kind === "meta" || block.line.kind === "gap") return;
    if (findMatchOffsets(block.line.text, needle).length) {
      hits.push({ blockIndex, line: block.line });
    }
  });
  return hits;
}
