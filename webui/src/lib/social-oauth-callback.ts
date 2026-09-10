// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import {
  currentPageLocation,
  isDesktopAppHttpHost,
  isTauriAppHost,
  isTauriCustomScheme,
  VITE_DEV_PORT,
  type PageLocation,
} from "@/lib/desktop";

export const OAUTH_CALLBACK_PATH = "/api/marketing/oauth/callback";
const IDE_LOOPBACK_PORT = "8766";

export function ideOauthCallbackUrl(
  provider: string,
  options: {
    saved?: string;
    suggested?: string;
    mediaBaseUrl?: string;
    location?: PageLocation | null;
  } = {},
): string {
  const saved = realCallback(options.saved);
  if (saved) return saved;
  const suggested = realCallback(options.suggested);
  if (suggested) return suggested;
  const publicOrigin = httpsInstallOrigin(options.mediaBaseUrl);
  if (publicOrigin) return `${publicOrigin}${OAUTH_CALLBACK_PATH}`;

  const page = options.location === undefined ? currentPageLocation() : options.location;
  if (page && !isViteOrTauriPage(page)) {
    const loopback = isDesktopAppHttpHost(page.hostname);
    if (page.protocol === "https:" || (provider === "reddit" && loopback)) {
      return `${page.protocol}//${page.host}${OAUTH_CALLBACK_PATH}`;
    }
  }
  if (provider === "reddit") {
    return `http://127.0.0.1:${IDE_LOOPBACK_PORT}${OAUTH_CALLBACK_PATH}`;
  }
  return "";
}

function realCallback(raw?: string): string {
  const value = (raw || "").trim();
  if (!value) return "";
  try {
    const host = new URL(value).hostname.toLowerCase();
    if (host === "example.com" || host.endsWith(".example.com") || host === "navin.example.com") {
      return "";
    }
  } catch {
    return "";
  }
  return value;
}

function httpsInstallOrigin(raw?: string): string {
  const value = (raw || "").trim().replace(/\/+$/, "");
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "https:" || parsed.username || parsed.password) return "";
    const host = parsed.hostname.toLowerCase();
    if (host === "example.com" || host.endsWith(".example.com")) return "";
    return parsed.origin;
  } catch {
    return "";
  }
}

function isViteOrTauriPage(page: PageLocation): boolean {
  return page.port === VITE_DEV_PORT || isTauriAppHost(page.hostname) || isTauriCustomScheme(page.protocol);
}
