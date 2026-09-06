/**
 * Recent-edit tracking for Cursor-grade Tab completion.
 *
 * The editor records each user edit as a compact line diff; the last few are
 * sent with every completion request so the model can predict the *next*
 * edit (renames, call-site updates, symmetrical branches...) instead of only
 * continuing the text at the caret.
 */

export type RecentEdit = {
  path: string;
  /** 1-based line where the edit starts. */
  line: number;
  removed: string;
  inserted: string;
};

const MAX_EDITS = 8;
const MAX_TEXT = 400;

/** Compact line diff between two versions of a document. */
export function computeLineEdit(
  oldText: string,
  newText: string,
): { line: number; removed: string; inserted: string } | null {
  if (oldText === newText) return null;
  const oldLines = oldText.split("\n");
  const newLines = newText.split("\n");

  let start = 0;
  const maxStart = Math.min(oldLines.length, newLines.length);
  while (start < maxStart && oldLines[start] === newLines[start]) start += 1;

  let oldEnd = oldLines.length;
  let newEnd = newLines.length;
  while (
    oldEnd > start
    && newEnd > start
    && oldLines[oldEnd - 1] === newLines[newEnd - 1]
  ) {
    oldEnd -= 1;
    newEnd -= 1;
  }

  const removed = oldLines.slice(start, oldEnd).join("\n");
  const inserted = newLines.slice(start, newEnd).join("\n");
  if (!removed && !inserted) return null;
  return {
    line: start + 1,
    removed: removed.slice(0, MAX_TEXT),
    inserted: inserted.slice(0, MAX_TEXT),
  };
}

export class RecentEditsTracker {
  private edits: RecentEdit[] = [];

  /** Record one document transition; consecutive edits in the same area merge. */
  record(path: string, oldText: string, newText: string): void {
    const diff = computeLineEdit(oldText, newText);
    if (!diff) return;
    const last = this.edits[this.edits.length - 1];
    if (last && last.path === path && Math.abs(last.line - diff.line) <= 2) {
      // Typing burst on the same spot: keep the original "removed" (the
      // state the burst started from) and the latest "inserted".
      last.line = Math.min(last.line, diff.line);
      last.inserted = diff.inserted;
      if (!last.removed) last.removed = diff.removed;
      return;
    }
    this.edits.push({ path, ...diff });
    if (this.edits.length > MAX_EDITS) {
      this.edits.splice(0, this.edits.length - MAX_EDITS);
    }
  }

  snapshot(): RecentEdit[] {
    return this.edits.map((edit) => ({ ...edit }));
  }

  clear(): void {
    this.edits = [];
  }
}
