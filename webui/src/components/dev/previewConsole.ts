import type { PreviewLogEntry } from "@/lib/api";

/** Levels the console panel treats as problems (badge + digest). */
const PROBLEM_LEVELS = new Set(["error", "pageerror", "network"]);

/** Extracts the port of a local preview URL, or null when not proxyable. */
export function localPreviewPortFromUrl(raw: string): number | null {
  const value = (raw || "").trim();
  if (!value) return null;
  let parsed: URL;
  try {
    parsed = new URL(value.includes("://") ? value : `http://${value}`);
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  const host = parsed.hostname.toLowerCase();
  const isLocal =
    host === "localhost"
    || host === "127.0.0.1"
    || host === "[::1]"
    || host === "::1"
    || host === "0.0.0.0";
  if (!isLocal) return null;
  const port = parsed.port
    ? Number(parsed.port)
    : parsed.protocol === "https:"
      ? 443
      : 80;
  if (!Number.isInteger(port) || port < 1 || port > 65535) return null;
  return port;
}

/** Appends new entries (by id) and trims the merged list to `max`. */
export function mergePreviewEntries(
  existing: PreviewLogEntry[],
  incoming: PreviewLogEntry[],
  max: number = 500,
): PreviewLogEntry[] {
  if (!incoming.length) return existing;
  const lastId = existing.length ? existing[existing.length - 1].id : 0;
  const fresh = incoming.filter((entry) => entry.id > lastId);
  if (!fresh.length) return existing;
  const merged = [...existing, ...fresh];
  return merged.length > max ? merged.slice(merged.length - max) : merged;
}

export function problemCount(entries: PreviewLogEntry[]): number {
  return entries.reduce(
    (count, entry) => count + (PROBLEM_LEVELS.has(entry.level) ? 1 : 0),
    0,
  );
}

export function isProblemLevel(level: string): boolean {
  return PROBLEM_LEVELS.has(level);
}

/** Builds the composer seed sent to the agent from a server digest. */
export function seedTextFromDigest(digest: string, port: number): string {
  const body = digest.trim();
  if (!body) return "";
  return (
    `The preview on http://localhost:${port} reported problems. `
    + "Please diagnose and fix them:\n\n"
    + body
  );
}
