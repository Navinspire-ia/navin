// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Site logins for browser capture (Settings > Sites & logins).
 *
 * Values only ever travel from the UI to the gateway; the gateway answers
 * with names and secret refs (site:<host[:port]>/<name>), never a value. The
 * agent then logs in with those refs, so a password never enters the chat.
 */

import { apiBodyHeaders, apiRequest } from "./api";

export const SECRET_REF_PREFIX = "site:";

export interface BrowserCredentialSite {
  site: string;
  names: string[];
  refs: string[];
  session: boolean;
  updated?: number | null;
}

export interface BrowserCredentialsSnapshot {
  sites: BrowserCredentialSite[];
  vault: "keyring" | "encrypted-file" | string;
}

export type BrowserCredentialAction = "list" | "set" | "delete" | "forget_session";

export function browserCredentialAction(
  token: string,
  action: BrowserCredentialAction,
  body: Record<string, unknown> = {},
  signal?: AbortSignal,
): Promise<BrowserCredentialsSnapshot> {
  return apiRequest<BrowserCredentialsSnapshot>(
    `/api/browser-credentials?action=${encodeURIComponent(action)}`,
    token,
    { headers: apiBodyHeaders(JSON.stringify(body)), signal },
  );
}

export function listBrowserCredentials(token: string, signal?: AbortSignal): Promise<BrowserCredentialsSnapshot> {
  return browserCredentialAction(token, "list", {}, signal);
}

/**
 * Save the non-empty values only: a blank field means "keep what is saved",
 * never "erase it" (the gateway reads an empty value as a removal).
 */
export function saveBrowserCredentials(
  token: string,
  site: string,
  values: Record<string, string>,
): Promise<BrowserCredentialsSnapshot> {
  const clean: Record<string, string> = {};
  for (const [name, value] of Object.entries(values)) {
    const text = typeof value === "string" ? value : "";
    if (text.trim()) clean[name] = text;
  }
  if (!site.trim()) return Promise.reject(new Error("A site is required."));
  if (Object.keys(clean).length === 0) return Promise.reject(new Error("Nothing to save."));
  return browserCredentialAction(token, "set", { site: site.trim(), values: clean });
}

/** Remove one saved name (keeps the saved browser session). */
export function removeBrowserCredential(token: string, site: string, name: string): Promise<BrowserCredentialsSnapshot> {
  return browserCredentialAction(token, "delete", { site, name });
}

/** Remove every saved name and the saved browser session for a site. */
export function deleteBrowserCredentialSite(token: string, site: string): Promise<BrowserCredentialsSnapshot> {
  return browserCredentialAction(token, "delete", { site });
}

export function forgetBrowserSession(token: string, site: string): Promise<BrowserCredentialsSnapshot> {
  return browserCredentialAction(token, "forget_session", { site });
}

/**
 * ``https://App.Example.com:443/login`` -> ``app.example.com``. Mirrors
 * normalize_site in navin/browser_credentials.py: default ports dropped,
 * other ports kept. Returns "" when the input is not a host or URL.
 */
export function normalizeSite(value: string): string {
  let text = (value || "").trim();
  if (!text) return "";
  if (text.toLowerCase().startsWith(SECRET_REF_PREFIX)) text = text.slice(SECRET_REF_PREFIX.length).split("/")[0] ?? "";
  if (!text.includes("://")) text = `http://${text}`;
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return "";
  }
  const host = url.hostname.toLowerCase().replace(/\.$/, "").replace(/^\[|\]$/g, "");
  if (!host) return "";
  // URL already drops the default port of its own scheme.
  const site = url.port ? `${host}:${url.port}` : host;
  return /^[a-z0-9.-]{1,253}(:[0-9]{1,5})?$/.test(site) ? site : "";
}

export function secretRef(site: string, name: string): string {
  return `${SECRET_REF_PREFIX}${site}/${name}`;
}

/** Best guess of the URL the agent should open for a saved site. */
export function siteUrl(site: string): string {
  const host = site.split(":")[0] ?? site;
  const local = host === "localhost" || host.endsWith(".local") || host.endsWith(".localhost") || /^\d+\.\d+\.\d+\.\d+$/.test(host);
  return `${local || site.includes(":") ? "http" : "https"}://${site}`;
}

/** The line handed to the agent: refs only, the tool fills the values. */
export function siteLoginInstruction(entry: Pick<BrowserCredentialSite, "site" | "names">): string {
  const refs = entry.names.map((name) => secretRef(entry.site, name)).join(", ");
  return (
    `Log in with ${SECRET_REF_PREFIX}${entry.site} first: browser(action=login, url=${siteUrl(entry.site)})` +
    (refs ? ` uses the saved ${refs}` : "") +
    ". The values are filled by the tool and never shown; never ask me for the password."
  );
}
