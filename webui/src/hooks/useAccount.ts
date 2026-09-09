// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useRef, useState } from "react";

import { useClient } from "@/providers/ClientProvider";
import {
  claimForcedRefresh,
  markForcedRefresh,
} from "@/lib/account-refresh-throttle";
import {
  activateAccountLicense,
  fetchAccount,
  fetchAccountConnectUrl,
  logoutAccount,
  type AccountPayload,
} from "@/lib/api";

/**
 * navin.live account state, shared between the sidebar card and the
 * settings section without prop drilling: every consumer subscribes to a
 * module-level broadcast, and any successful fetch or mutation republishes
 * the fresh payload to all of them.
 */

let lastPayload: AccountPayload | null = null;
const listeners = new Set<(account: AccountPayload) => void>();

function broadcast(account: AccountPayload) {
  lastPayload = account;
  listeners.forEach((listener) => listener(account));
}

/** Publish an account payload to every `useAccount` consumer (e.g. Free setup poll). */
export function publishAccount(account: AccountPayload) {
  broadcast(account);
}

// Many components use this hook, and window focus or a fresh connection would
// otherwise fan out into one forced validation per consumer. One forced
// refresh per window is plenty, and the throttle survives a page reload.
function forceRefreshThrottled(
  reload: (options?: { refresh?: boolean }) => Promise<AccountPayload | null>,
) {
  if (!claimForcedRefresh()) return;
  void reload({ refresh: true });
}

// One gateway push fans out to every open window. Forcing a validation here
// closed a loop: the call does network I/O, can push again, and land back
// inside a two-second dedupe. The gateway already stored the fresh body
// before pushing, so a local read is enough.
const PUSH_REFRESH_MIN_MS = 10_000;
let lastPushRefreshAt = 0;
function refreshOnAccountPush(
  reload: (options?: { refresh?: boolean }) => Promise<AccountPayload | null>,
) {
  const now = Date.now();
  if (now - lastPushRefreshAt < PUSH_REFRESH_MIN_MS) return;
  lastPushRefreshAt = now;
  void reload();
}

let inFlightReload: Promise<AccountPayload | null> | null = null;
let inFlightRefresh = false;

export function useAccount(): {
  account: AccountPayload | null;
  loading: boolean;
  /** Re-read the account; `refresh` forces a validation against navin.live. */
  reload: (options?: { refresh?: boolean }) => Promise<AccountPayload | null>;
  /**
   * Mint a connect URL and ask the gateway to open it in the OS browser
   * (WebView ``window.open`` is unreliable in the desktop shell).
   */
  connectUrl: (options?: { locale?: string }) => Promise<{ url: string; opened: boolean }>;
  activate: (licenseKey: string) => Promise<AccountPayload>;
  logout: () => Promise<AccountPayload>;
} {
  const { client, token } = useClient();
  const tokenRef = useRef(token);
  tokenRef.current = token;
  const [account, setAccount] = useState<AccountPayload | null>(lastPayload);
  const [loading, setLoading] = useState(lastPayload === null);

  useEffect(() => {
    const listener = (payload: AccountPayload) => setAccount(payload);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  const reload = useCallback(
    async (options?: { refresh?: boolean }) => {
      const wantRefresh = Boolean(options?.refresh);
      // A cheap local read and a forced validation are not interchangeable.
      // Joining a cheap in-flight fetch when the caller asked for refresh=1
      // spent the throttle window and still served the cached account.
      while (inFlightReload) {
        if (inFlightRefresh || !wantRefresh) return inFlightReload;
        await inFlightReload;
      }
      // Every forced validation moves the window, whoever asked for it. Left
      // to the callers, an explicit refresh (account settings, quota card)
      // did not count, so a focus event right after one paid for a second
      // round-trip to navin.live.
      if (wantRefresh) markForcedRefresh();
      inFlightRefresh = wantRefresh;
      inFlightReload = (async () => {
        try {
          const payload = await fetchAccount(tokenRef.current, options);
          broadcast(payload);
          return payload;
        } catch {
          // Offline gateway or stale token: keep whatever we already show.
          return null;
        } finally {
          inFlightReload = null;
          inFlightRefresh = false;
          setLoading(false);
        }
      })();
      return inFlightReload;
    },
    [],
  );

  useEffect(() => {
    if (lastPayload === null) void reload();
  }, [reload]);

  // The gateway pushes ``account_updated`` the moment the browser sign-in
  // handoff activates the device, a Stripe plan change is detected, or the
  // user signs out. Reload right away so no window waits on a poll or a
  // manual refresh. The gateway just invalidated its validate cache, so a
  // plain reload already returns fresh data.
  useEffect(() => {
    const unsubscribe = client.onAccountUpdated(() => refreshOnAccountPush(reload));
    return unsubscribe;
  }, [client, reload]);

  // Keep the sidebar plan in sync with Stripe renewals / upgrades. The
  // gateway also polls license validate; this path forces a fresh check so
  // the UI does not wait on the 5-minute account cache alone.
  useEffect(() => {
    if (!account?.connected) return;
    const POLL_MS = 10 * 60 * 1000;
    const id = window.setInterval(() => {
      void reload({ refresh: true });
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [account?.connected, reload]);

  // The moment the account connects (sign-in just completed, or the app
  // booted with a stored license), pull a fresh validation so plan, managed
  // provider and usage never show a previous session's state.
  useEffect(() => {
    if (!account?.connected) return;
    forceRefreshThrottled(reload);
  }, [account?.connected, reload]);

  // Coming back to the IDE window (typically after signing in, paying or
  // changing plan on navin.live in the browser) refreshes the account right
  // away instead of waiting for the next poll - the fallback when timers were
  // suspended while the window was hidden (WebView2 throttling).
  useEffect(() => {
    const connected = Boolean(account?.connected);
    const onFocus = () => {
      if (document.visibilityState !== "visible") return;
      if (connected) {
        forceRefreshThrottled(reload);
      } else {
        // Signed out: a cheap local read is enough to notice that the
        // browser handoff finished while this window was in the background.
        void reload();
      }
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [account?.connected, reload]);

  const connectUrl = useCallback(async (options?: { locale?: string }) => {
    const { url } = await fetchAccountConnectUrl(tokenRef.current, "", {
      locale: options?.locale,
    });
    return { url, opened: false };
  }, []);

  const activate = useCallback(async (licenseKey: string) => {
    const payload = await activateAccountLicense(tokenRef.current, licenseKey);
    broadcast(payload);
    return payload;
  }, []);

  const logout = useCallback(async () => {
    const payload = await logoutAccount(tokenRef.current);
    broadcast(payload);
    return payload;
  }, []);

  return { account, loading, reload, connectUrl, activate, logout };
}
