/** In-conversation find: match terms against message text. */

export function searchTerms(query: string): string[] {
  return query.trim().toLowerCase().split(/\s+/).filter(Boolean);
}

export function haystackMatches(haystack: string, terms: string[]): boolean {
  if (terms.length === 0) return false;
  const low = (haystack || "").toLowerCase();
  return terms.every((term) => low.includes(term));
}

export function teamMessageHaystack(row: {
  text?: string | null;
  author?: string | null;
  file?: { name?: string | null } | null;
}): string {
  return [row.author, row.text, row.file?.name].filter(Boolean).join(" ");
}

export function uiMessageHaystack(row: {
  content?: string | null;
  role?: string | null;
  kind?: string | null;
}): string {
  if (row.kind === "trace") return "";
  return [row.role, row.content].filter(Boolean).join(" ");
}

export function matchingRowIds<T extends { id: string }>(
  rows: T[],
  query: string,
  haystack: (row: T) => string,
): string[] {
  const terms = searchTerms(query);
  if (!terms.length) return [];
  return rows.filter((row) => haystackMatches(haystack(row), terms)).map((row) => row.id);
}

export function stepMatchIndex(current: number, total: number, delta: number): number {
  if (total <= 0) return 0;
  return (current + delta + total) % total;
}

export function splitHighlight(text: string, query: string): Array<{ text: string; hit: boolean }> {
  const needle = query.trim();
  if (!needle || !text) return [{ text, hit: false }];
  const lower = text.toLowerCase();
  const find = needle.toLowerCase();
  const out: Array<{ text: string; hit: boolean }> = [];
  let cursor = 0;
  while (cursor < text.length) {
    const at = lower.indexOf(find, cursor);
    if (at < 0) {
      out.push({ text: text.slice(cursor), hit: false });
      break;
    }
    if (at > cursor) out.push({ text: text.slice(cursor, at), hit: false });
    out.push({ text: text.slice(at, at + needle.length), hit: true });
    cursor = at + needle.length;
  }
  return out;
}
