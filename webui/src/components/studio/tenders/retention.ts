/** Same clock as `navin/tenders/retention.py`. The sidecar (Tauri) reads these from profile.json. */

export const DEFAULT_ARCHIVE_AFTER_DAYS = 45;
export const DEFAULT_DELETE_AFTER_DAYS = 60;
const MAX_RETENTION_DAYS = 3650;

export function clampRetentionDays(value: unknown, fallback: number): number {
  const days = typeof value === "number" ? value : Number(String(value || "").trim());
  if (!Number.isFinite(days) || days <= 0) return fallback;
  return Math.min(Math.round(days), MAX_RETENTION_DAYS);
}

export function retentionDays(input: {
  archive_after_days?: unknown;
  delete_after_days?: unknown;
} = {}): { archive_after_days: number; delete_after_days: number } {
  const archive_after_days = clampRetentionDays(input.archive_after_days, DEFAULT_ARCHIVE_AFTER_DAYS);
  let delete_after_days = clampRetentionDays(input.delete_after_days, DEFAULT_DELETE_AFTER_DAYS);
  if (delete_after_days < archive_after_days) delete_after_days = archive_after_days;
  return { archive_after_days, delete_after_days };
}

/** Fields the Settings wizard must POST so the desktop sidecar applies them. */
export function retentionProfilePayload(input: {
  archive_after_days?: unknown;
  delete_after_days?: unknown;
}): { archive_after_days: number; delete_after_days: number } {
  return retentionDays(input);
}
