/**
 * The notification list behind the bell in the app chrome.
 *
 * Kept as pure functions over a frozen array so the interesting behaviour -
 * collapsing repeats, capping growth, tracking what has been read - can be
 * tested without React. The provider owns the array and re-renders on change.
 *
 * Nothing is persisted: notifications describe what is happening now, and a
 * reload is a fresh start.
 */

export type NotificationLevel = "info" | "success" | "warning" | "error";

/** Where a notification came from, used for filtering and for the icon. */
export type NotificationSource =
  /** Websocket transport: the link to the gateway dropped or came back. */
  | "connection"
  /** An HTTP call to the gateway failed. */
  | "request"
  /** An agent asked for the user's attention. */
  | "agent"
  /** The task board changed. */
  | "board"
  /** Runtime/session lifecycle: model switch, context compaction, retries. */
  | "session"
  /** A new Navin version is available (or is being installed). */
  | "update"
  /** News published on navin.live: new models, releases, announcements. */
  | "announcement";

/**
 * The one thing the user can do about a notification, shown as a button on the
 * entry itself.
 *
 * A notification that announces a new version and then leaves a URL to copy is
 * not telling the user what to do, it is giving them homework. The action is
 * carried on the entry so the panel can offer the obvious next step where the
 * news is read, rather than sending people to Settings to find it.
 */
export interface NotificationAction {
  label: string;
  /** Shown while `run` is in flight. Falls back to `label`. */
  busyLabel?: string;
  run: () => void | Promise<void>;
}

export interface NotificationInput {
  level: NotificationLevel;
  source: NotificationSource;
  title: string;
  detail?: string;
  action?: NotificationAction;
  /**
   * Identity used to collapse repeats. Defaults to the level, source, title
   * and detail together, which is right whenever the text already says what
   * happened. Pass an explicit key to collapse notifications whose text varies
   * (a countdown, a file name) but which are really the same event.
   */
  key?: string;
  /** Chat this belongs to, when it is not global. */
  chatId?: string | null;
  /** Vignette (https) affichée sous le texte - annonces produit riches. */
  imageUrl?: string;
  /**
   * Pop up briefly even though it is not a problem. Reserved for events the
   * user just triggered and is waiting on (a download landing), where silence
   * reads as failure. Errors and warnings always toast without this.
   */
  toast?: boolean;
}

export interface NotificationEntry {
  id: string;
  level: NotificationLevel;
  source: NotificationSource;
  title: string;
  detail?: string;
  action?: NotificationAction;
  key: string;
  chatId?: string | null;
  imageUrl?: string;
  toast?: boolean;
  firstSeenAt: number;
  lastSeenAt: number;
  /** 1 the first time; higher once repeats have been folded in. */
  repeats: number;
  read: boolean;
}

/**
 * Repeats land under the existing entry only while it is this recent. A failure
 * that comes back an hour later is news again, and deserves its own line rather
 * than silently bumping a counter on something the user already dealt with.
 */
export const COALESCE_WINDOW_MS = 2 * 60_000;

/** Past this many entries the oldest are dropped. */
export const MAX_NOTIFICATIONS = 100;

export function notificationKey(input: NotificationInput): string {
  if (input.key) return input.key;
  return `${input.source}:${input.level}:${input.title}:${input.detail ?? ""}`;
}

let sequence = 0;

/** Ids are local and short-lived; crypto.randomUUID is missing over plain HTTP. */
function nextId(now: number): string {
  sequence += 1;
  return `n${now.toString(36)}-${sequence.toString(36)}`;
}

export function addNotification(
  list: readonly NotificationEntry[],
  input: NotificationInput,
  now: number = Date.now(),
  limits: { coalesceWindowMs?: number; max?: number } = {},
): NotificationEntry[] {
  const window = limits.coalesceWindowMs ?? COALESCE_WINDOW_MS;
  const max = limits.max ?? MAX_NOTIFICATIONS;
  const key = notificationKey(input);

  const existing = list.find((entry) => entry.key === key && now - entry.lastSeenAt <= window);
  if (existing) {
    const merged: NotificationEntry = {
      ...existing,
      // The newest wording wins: a repeat often carries a fresher detail, such
      // as a different status code behind the same failing action.
      detail: input.detail ?? existing.detail,
      action: input.action ?? existing.action,
      imageUrl: input.imageUrl ?? existing.imageUrl,
      toast: input.toast === true || existing.toast === true,
      lastSeenAt: now,
      repeats: existing.repeats + 1,
      read: false,
    };
    return [merged, ...list.filter((entry) => entry !== existing)];
  }

  const entry: NotificationEntry = {
    id: nextId(now),
    level: input.level,
    source: input.source,
    title: input.title,
    detail: input.detail,
    action: input.action,
    key,
    chatId: input.chatId ?? null,
    imageUrl: input.imageUrl,
    toast: input.toast === true,
    firstSeenAt: now,
    lastSeenAt: now,
    repeats: 1,
    read: false,
  };
  return [entry, ...list].slice(0, max);
}

export function markRead(list: readonly NotificationEntry[], id: string): NotificationEntry[] {
  return list.map((entry) => (entry.id === id && !entry.read ? { ...entry, read: true } : entry));
}

export function markAllRead(list: readonly NotificationEntry[]): NotificationEntry[] {
  if (list.every((entry) => entry.read)) return [...list];
  return list.map((entry) => (entry.read ? entry : { ...entry, read: true }));
}

export function dismissNotification(
  list: readonly NotificationEntry[],
  id: string,
): NotificationEntry[] {
  return list.filter((entry) => entry.id !== id);
}

export function unreadCount(list: readonly NotificationEntry[]): number {
  return list.reduce((total, entry) => (entry.read ? total : total + 1), 0);
}

/**
 * How long ago something happened, as a translation key and its count.
 *
 * Returns the parts rather than a string so the caller owns the wording, and so
 * the rounding can be tested without a translation table.
 */
export function relativeTimeParts(
  at: number,
  now: number,
): { key: "justNow" | "minutesAgo" | "hoursAgo"; count: number } {
  const seconds = Math.max(0, Math.round((now - at) / 1000));
  if (seconds < 60) return { key: "justNow", count: 0 };
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return { key: "minutesAgo", count: minutes };
  return { key: "hoursAgo", count: Math.round(minutes / 60) };
}

/**
 * Entries worth interrupting the user with, newest first.
 *
 * Unread problems qualify, plus entries explicitly flagged `toast` (a download
 * the user is waiting on). Everything else is available in the panel but never
 * pops up: a centre that shouts about routine success is one users learn to
 * dismiss without reading.
 */
export function toastable(
  list: readonly NotificationEntry[],
  now: number = Date.now(),
  maxAgeMs: number = 10_000,
): NotificationEntry[] {
  return list.filter(
    (entry) =>
      !entry.read &&
      (entry.level === "error" || entry.level === "warning" || entry.toast === true) &&
      now - entry.lastSeenAt <= maxAgeMs,
  );
}
