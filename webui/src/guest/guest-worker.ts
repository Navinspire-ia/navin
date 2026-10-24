// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

const WORKER_URL = new URL("./guest-sw.js", import.meta.url);

let worker: Promise<ServiceWorkerRegistration | null> | null = null;

/**
 * The guest page's service worker (guest-sw.js), at the root of the relay:
 * it serves the host's shared site there. A relay that does not allow the
 * root yet still gets it under /assets/, for notifications only.
 */
export function guestWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!worker) {
    worker = "serviceWorker" in navigator
      ? navigator.serviceWorker
          .register(WORKER_URL, { scope: "/" })
          .catch(() => navigator.serviceWorker.register(WORKER_URL, { scope: "/assets/" }))
          .catch(() => null)
      : Promise.resolve(null);
  }
  return worker;
}

/** True once the root worker runs: the site frame may load. */
export async function siteWorkerReady(timeoutMs = 10_000): Promise<boolean> {
  const reg = await guestWorker();
  if (!reg || reg.scope !== `${window.location.origin}/`) return false;
  if (reg.active) return true;
  const pending = reg.installing ?? reg.waiting;
  if (!pending) return false;
  await new Promise<void>((resolve) => {
    const timer = window.setTimeout(resolve, timeoutMs);
    pending.addEventListener("statechange", () => {
      if (pending.state === "activated" || pending.state === "redundant") {
        window.clearTimeout(timer);
        resolve();
      }
    });
  });
  return Boolean(reg.active);
}
