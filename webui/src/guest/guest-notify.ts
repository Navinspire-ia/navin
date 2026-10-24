// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { guestWorker } from "./guest-worker";

const PREF_KEY = "navin.guest.notify";
const BASE_TITLE = "Navin";

let unread = 0;

/** Android Chrome and friends show notifications from a service worker only. */
const registration = guestWorker;

function paintTitle(): void {
  document.title = unread > 0 ? `(${unread}) ${BASE_TITLE}` : BASE_TITLE;
}

if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      unread = 0;
      paintTitle();
    }
  });
}

export function notificationsSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

export function notificationsEnabled(): boolean {
  return notificationsSupported() && Notification.permission === "granted" && localStorage.getItem(PREF_KEY) !== "off";
}

/** Must run from a click (browsers refuse the prompt otherwise). */
export async function toggleNotifications(): Promise<boolean> {
  if (!notificationsSupported()) return false;
  if (notificationsEnabled()) {
    localStorage.setItem(PREF_KEY, "off");
    return false;
  }
  const permission = Notification.permission === "default" ? await Notification.requestPermission() : Notification.permission;
  localStorage.setItem(PREF_KEY, permission === "granted" ? "on" : "off");
  if (permission === "granted") void registration();
  return permission === "granted";
}

/**
 * Something happened while the guest looked away: count it in the tab title,
 * buzz a phone, and show a system notification when allowed.
 */
export function guestNotify(title: string, body: string, tag: string): void {
  if (!document.hidden) return;
  unread += 1;
  paintTitle();
  navigator.vibrate?.(60);
  if (!notificationsEnabled()) return;
  const options = { body: body.slice(0, 180), tag };
  void registration().then((reg) => {
    if (reg) {
      void reg.showNotification(title, options).catch(() => undefined);
      return;
    }
    try {
      const note = new Notification(title, options);
      note.onclick = () => {
        window.focus();
        note.close();
      };
    } catch {
      // No notification API here (iOS outside a home-screen app): title and buzz remain.
    }
  });
}
