import type { UIFileEdit, UIMessage } from "./types";

/**
 * Where the agent actually wrote a file the chat refers to.
 *
 * A prose chip (`schema.sql`, `supabase/schema.sql`) carries whatever the
 * model typed, while every `file_edit` event in the thread carries the
 * absolute path the tool wrote. Matching the two makes "open" work even when
 * the file sits outside the folder the editor is looking at, or when a
 * same-named file exists elsewhere: the most recent edit wins because the
 * chip sits next to the edit that produced it.
 */

function normalizeSlashes(value: string): string {
  return value.replace(/\\/g, "/");
}

export function isAbsoluteChipPath(path: string): boolean {
  const value = normalizeSlashes(path.trim());
  return (
    value.startsWith("/")
    || /^[A-Za-z]:\//.test(value)
    || value.startsWith("//")
    || /^file:\/\//i.test(value)
  );
}

function cleanRelative(path: string): string {
  return normalizeSlashes(path.trim())
    .split("?", 1)[0]
    .split("#", 1)[0]
    .replace(/:\d+(?::\d+)?$/, "")
    .replace(/^\.\//, "")
    .replace(/^\/+/, "");
}

function editTarget(edit: UIFileEdit): string {
  const abs = (edit.absolute_path ?? "").trim();
  if (abs) return normalizeSlashes(abs);
  return normalizeSlashes((edit.path ?? "").trim());
}

/** Edits from the thread, newest first, deletions and pending rows skipped. */
export function writtenFileEdits(messages: readonly UIMessage[]): UIFileEdit[] {
  const out: UIFileEdit[] = [];
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const edits = messages[i]?.fileEdits;
    if (!edits?.length) continue;
    for (let j = edits.length - 1; j >= 0; j -= 1) {
      const edit = edits[j];
      if (!edit || edit.operation === "delete") continue;
      if (edit.status === "error") continue;
      if (!editTarget(edit)) continue;
      out.push(edit);
    }
  }
  return out;
}

/**
 * Absolute path for `path` when a file edit in the thread produced it, else
 * the input unchanged. Exact display-path matches beat suffix matches, which
 * beat bare-name matches; within a tier the most recent edit wins.
 */
export function resolveChipPathFromEdits(
  path: string,
  messages: readonly UIMessage[],
): string {
  const trimmed = path.trim();
  if (!trimmed || isAbsoluteChipPath(trimmed)) return trimmed;
  const wanted = cleanRelative(trimmed);
  if (!wanted || wanted === "." || wanted === "..") return trimmed;
  const wantedLower = wanted.toLowerCase();
  const wantedName = wantedLower.slice(wantedLower.lastIndexOf("/") + 1);
  if (!wantedName) return trimmed;

  let suffixHit: string | null = null;
  let nameHit: string | null = null;
  for (const edit of writtenFileEdits(messages)) {
    const target = editTarget(edit);
    const targetLower = target.toLowerCase();
    const display = cleanRelative(edit.path ?? "").toLowerCase();
    if (display && display === wantedLower) return target;
    if (targetLower === wantedLower) return target;
    if (suffixHit === null && targetLower.endsWith(`/${wantedLower}`)) {
      suffixHit = target;
      continue;
    }
    if (nameHit === null && !wantedLower.includes("/")) {
      const targetName = targetLower.slice(targetLower.lastIndexOf("/") + 1);
      if (targetName === wantedName) nameHit = target;
    }
  }
  return suffixHit ?? nameHit ?? trimmed;
}
