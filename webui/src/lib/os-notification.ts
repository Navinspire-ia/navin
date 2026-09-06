/**
 * Raise a notification the user will see even when Navin is not the focused
 * window, and always leave a trace inside the app.
 *
 * The Web Notification API is not a given in a desktop webview: WKWebView on
 * macOS does not define `Notification` at all, and WebKitGTK only grants
 * permission when the host application implements the permission-request
 * signal, which the shell does not. A meeting reminder that only ever called
 * `new Notification` therefore fired on Windows and nowhere else, silently.
 *
 * So the in-app notification centre is the floor, not the fallback: it always
 * gets the entry. The OS banner is the bonus, attempted when the platform
 * actually offers one.
 */

import { publishNotification } from "@/lib/notification-bus";

export interface OsNotificationRequest {
  title: string;
  body?: string;
  /** Dedupe key, also used by the in-app centre to collapse repeats. */
  key?: string;
  /** Run when the user activates the OS banner. */
  onActivate?: () => void;
}

/** True when this runtime can show an OS-level banner at all. */
export function osNotificationsSupported(): boolean {
  return typeof window !== "undefined" && typeof Notification !== "undefined";
}

/**
 * Show the notification. Resolves to true when an OS banner was raised; the
 * in-app entry is published either way, so a false is not a failure.
 */
export async function notifyUser(request: OsNotificationRequest): Promise<boolean> {
  publishNotification({
    level: "info",
    source: "session",
    toast: true,
    ...(request.key ? { key: request.key } : {}),
    title: request.title,
    ...(request.body ? { detail: request.body } : {}),
  });

  if (!osNotificationsSupported()) return false;

  let permission = Notification.permission;
  if (permission === "default") {
    try {
      permission = await Notification.requestPermission();
    } catch {
      return false;
    }
  }
  if (permission !== "granted") return false;

  try {
    const notification = new Notification(request.title, {
      ...(request.body ? { body: request.body } : {}),
      ...(request.key ? { tag: request.key } : {}),
    });
    if (request.onActivate) {
      notification.onclick = () => request.onActivate?.();
    }
    return true;
  } catch {
    return false;
  }
}
