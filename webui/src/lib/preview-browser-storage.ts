/** Persistence for the Code Preview simple browser (recents, bookmarks). */

export const PREVIEW_RECENTS_KEY = "navin.dev.previewRecents";
export const PREVIEW_BOOKMARKS_KEY = "navin.dev.previewBookmarks";
export const PREVIEW_BOOKMARK_BAR_KEY = "navin.dev.previewBookmarkBar";

export const MAX_PREVIEW_RECENTS = 15;
export const MAX_PREVIEW_BOOKMARKS = 30;

export type PreviewBookmark = { url: string; title: string };

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // quota / private mode
  }
}

export function normalizePreviewHref(raw: string): string {
  const value = raw.trim();
  if (!value) return "";
  return /^https?:\/\//i.test(value) ? value : `http://${value}`;
}

/** Cursor-style recents label: host + path, no protocol. */
export function displayPreviewHref(raw: string): string {
  const value = raw.trim();
  if (!value) return "";
  try {
    const parsed = new URL(normalizePreviewHref(value));
    const path = `${parsed.pathname}${parsed.search}${parsed.hash}`;
    const trimmedPath = path === "/" ? "" : path;
    return `${parsed.host}${trimmedPath}`;
  } catch {
    return value.replace(/^https?:\/\//i, "");
  }
}

export function bookmarkTitleFromUrl(raw: string): string {
  const label = displayPreviewHref(raw);
  if (!label) return raw.trim();
  try {
    const parsed = new URL(normalizePreviewHref(raw));
    const last = parsed.pathname.split("/").filter(Boolean).pop();
    if (last && last !== parsed.hostname) return last;
  } catch {
    // keep host+path
  }
  return label;
}

export function readPreviewRecents(): string[] {
  const rows = readJson<unknown>(PREVIEW_RECENTS_KEY, []);
  if (!Array.isArray(rows)) return [];
  return rows
    .filter((row): row is string => typeof row === "string" && row.trim().length > 0)
    .slice(0, MAX_PREVIEW_RECENTS);
}

export function rememberPreviewRecent(url: string): string[] {
  const href = normalizePreviewHref(url);
  if (!href || !/^https?:\/\//i.test(href)) return readPreviewRecents();
  const next = [href, ...readPreviewRecents().filter((row) => row !== href)].slice(
    0,
    MAX_PREVIEW_RECENTS,
  );
  writeJson(PREVIEW_RECENTS_KEY, next);
  return next;
}

export function clearPreviewRecents(): void {
  try {
    localStorage.removeItem(PREVIEW_RECENTS_KEY);
  } catch {
    // ignore
  }
}

export function readPreviewBookmarks(): PreviewBookmark[] {
  const rows = readJson<unknown>(PREVIEW_BOOKMARKS_KEY, []);
  if (!Array.isArray(rows)) return [];
  const out: PreviewBookmark[] = [];
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const url = typeof (row as PreviewBookmark).url === "string"
      ? (row as PreviewBookmark).url.trim()
      : "";
    if (!url) continue;
    const title =
      typeof (row as PreviewBookmark).title === "string"
        && (row as PreviewBookmark).title.trim()
        ? (row as PreviewBookmark).title.trim()
        : bookmarkTitleFromUrl(url);
    out.push({ url, title });
    if (out.length >= MAX_PREVIEW_BOOKMARKS) break;
  }
  return out;
}

export function upsertPreviewBookmark(url: string): PreviewBookmark[] {
  const href = normalizePreviewHref(url);
  if (!href) return readPreviewBookmarks();
  const current = readPreviewBookmarks().filter((row) => row.url !== href);
  const next = [{ url: href, title: bookmarkTitleFromUrl(href) }, ...current].slice(
    0,
    MAX_PREVIEW_BOOKMARKS,
  );
  writeJson(PREVIEW_BOOKMARKS_KEY, next);
  return next;
}

export function removePreviewBookmark(url: string): PreviewBookmark[] {
  const href = normalizePreviewHref(url);
  const next = readPreviewBookmarks().filter((row) => row.url !== href);
  writeJson(PREVIEW_BOOKMARKS_KEY, next);
  return next;
}

export function readPreviewBookmarkBar(): boolean {
  try {
    return localStorage.getItem(PREVIEW_BOOKMARK_BAR_KEY) === "1";
  } catch {
    return false;
  }
}

export function persistPreviewBookmarkBar(on: boolean): void {
  try {
    localStorage.setItem(PREVIEW_BOOKMARK_BAR_KEY, on ? "1" : "0");
  } catch {
    // ignore
  }
}

export function appendCacheBust(src: string, token: number): string {
  if (!src || !token) return src;
  try {
    const parsed = new URL(src, typeof window !== "undefined" ? window.location.href : "http://127.0.0.1");
    parsed.searchParams.set("_navin_reload", String(token));
    return parsed.toString();
  } catch {
    const join = src.includes("?") ? "&" : "?";
    return `${src}${join}_navin_reload=${token}`;
  }
}
