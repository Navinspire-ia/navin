// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Per-chat composer draft text - survives reload / gateway restart. */

export const COMPOSER_DRAFT_STORAGE_PREFIX = "navin.webui.composerDraft.v1:";
export const COMPOSER_DRAFT_MAX_CHARS = 20_000;

export function composerDraftStorageKey(chatKey?: string | null): string | null {
  const clean = chatKey?.trim();
  return clean ? `${COMPOSER_DRAFT_STORAGE_PREFIX}${clean}` : null;
}

export function readComposerDraft(chatKey?: string | null): string {
  const storageKey = composerDraftStorageKey(chatKey);
  if (!storageKey || typeof window === "undefined") return "";
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (typeof raw !== "string") return "";
    return raw.slice(0, COMPOSER_DRAFT_MAX_CHARS);
  } catch {
    return "";
  }
}

export function writeComposerDraft(chatKey: string | null | undefined, text: string): void {
  const storageKey = composerDraftStorageKey(chatKey);
  if (!storageKey || typeof window === "undefined") return;
  try {
    const next = text.slice(0, COMPOSER_DRAFT_MAX_CHARS);
    if (!next.trim()) {
      window.localStorage.removeItem(storageKey);
      return;
    }
    window.localStorage.setItem(storageKey, next);
  } catch {
    // private mode / quota
  }
}

export function clearComposerDraft(chatKey?: string | null): void {
  const storageKey = composerDraftStorageKey(chatKey);
  if (!storageKey || typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(storageKey);
  } catch {
    // ignore
  }
}
