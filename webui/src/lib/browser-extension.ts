// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { apiBodyHeaders, apiRequest } from "./api";
import { saveDownload } from "./save-blob";

export function extensionPackageBrowser(browser: string, downloads?: Record<string, boolean>): string | null {
  if (downloads?.[browser]) return browser;
  // Older servers only advertise Chrome; that identical Chromium package also runs in Edge.
  if (browser === "edge" && downloads?.chrome) return "chrome";
  return null;
}

export async function saveExtensionPackage(pack: { name: string; mime: string; data: string }): Promise<void> {
  const bytes = Uint8Array.from(atob(pack.data), char => char.charCodeAt(0));
  await saveDownload(new Blob([bytes], { type: pack.mime }), pack.name);
}

export interface ExtensionDevice { id: string; label: string; created_at: number; last_seen: number; scopes: string[] }
export interface ExtensionReceipt { device: string; request_id: string; status: string; kind: string; at: number; reason?: string; module?: string }
export interface ExtensionState {
  devices?: ExtensionDevice[]; receipts?: ExtensionReceipt[]; downloads?: Record<string, boolean>;
  code?: string; expires_at?: number; package?: { name: string; mime: string; data: string };
}
export function extensionRequest(token: string, action: string, body: Record<string, unknown> = {}): Promise<ExtensionState> {
  return apiRequest<ExtensionState>(`/api/career?action=extension_${action}`, token, {
    headers: apiBodyHeaders(JSON.stringify(body)), cache: "no-store",
  });
}
