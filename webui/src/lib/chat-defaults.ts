// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * What a new chat starts with: the model and the team the user picked last.
 *
 * localStorage alone was not enough: the desktop page origin carries the
 * local port, and each engine (WebView2, WKWebView, WebKitGTK) keeps its own
 * storage, so a port change, another window or a reset webview started every
 * chat on the account default again. The gateway keeps the picks in
 * ~/.navin/webui/chat-defaults.json; localStorage stays the synchronous cache
 * the composer reads at mount. Effort and context window are saved on the
 * model itself (Settings), so they follow the model to every chat already.
 */

import { fetchChatDefaults, updateChatDefaults, type ChatDefaultsPayload } from "@/lib/api";

/** Same key thread-model-preset.ts has always used for the last model pick. */
export const LAST_MODEL_PICK_KEY = "navin.thread-model-last-pick";
export const LAST_TEAM_PICK_KEY = "navin.chat-team-last-pick";
/** Fired on window once the gateway copy has been read into localStorage. */
export const CHAT_DEFAULTS_EVENT = "navin:chat-defaults";

type Field = "model_preset" | "team";
const STORAGE_KEYS: Record<Field, string> = {
  model_preset: LAST_MODEL_PICK_KEY,
  team: LAST_TEAM_PICK_KEY,
};

let syncToken: string | null = null;

function readLocal(field: Field): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEYS[field]) || null;
  } catch (err) {
    console.warn("Chat defaults: localStorage unreadable", err);
    return null;
  }
}

function writeLocal(field: Field, value: string | null): void {
  try {
    if (value) window.localStorage.setItem(STORAGE_KEYS[field], value);
    else window.localStorage.removeItem(STORAGE_KEYS[field]);
  } catch (err) {
    // Quota or private mode: the gateway copy still carries the pick.
    console.warn("Chat defaults: localStorage not writable", err);
  }
}

function pushRemote(patch: Partial<Record<Field, string | null>>): void {
  if (!syncToken) return;
  void updateChatDefaults(syncToken, patch).catch((err) => {
    console.warn("Chat defaults: could not save the pick to Navin", err);
  });
}

export function loadLastTeamPick(): string | null {
  return readLocal("team");
}

/** Remember a pick for the next chats, here and on the gateway. */
export function rememberChatDefault(field: Field, value: string | null): void {
  writeLocal(field, value);
  pushRemote({ [field]: value });
}

/**
 * Read the gateway copy into localStorage, then announce it. A value only
 * this page knows (picked before the gateway kept them) is sent up instead.
 */
export async function hydrateChatDefaults(token: string): Promise<ChatDefaultsPayload | null> {
  syncToken = token || null;
  if (!syncToken) return null;
  let remote: ChatDefaultsPayload;
  try {
    remote = await fetchChatDefaults(syncToken);
  } catch (err) {
    console.warn("Chat defaults: could not read them from Navin, using this window's", err);
    return null;
  }
  const upload: Partial<Record<Field, string | null>> = {};
  for (const field of Object.keys(STORAGE_KEYS) as Field[]) {
    const value = typeof remote[field] === "string" && remote[field] ? remote[field] : null;
    if (value) writeLocal(field, value);
    else if (readLocal(field)) upload[field] = readLocal(field);
  }
  if (Object.keys(upload).length) pushRemote(upload);
  window.dispatchEvent(new CustomEvent(CHAT_DEFAULTS_EVENT));
  return remote;
}

/** Test hook. */
export function resetChatDefaultsForTests(): void {
  syncToken = null;
}
