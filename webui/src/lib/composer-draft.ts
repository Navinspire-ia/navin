// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Per-chat composer draft text - survives reload / gateway restart. */

import {
  usedPastedContent,
  type PastedContentMap,
} from "@/lib/pasted-content";

export const COMPOSER_DRAFT_STORAGE_PREFIX = "navin.webui.composerDraft.v1:";
export const COMPOSER_DRAFT_MAX_CHARS = 20_000;
export const COMPOSER_DRAFT_PASTE_MAX_CHARS = 400_000;
const DRAFT_V2 = 2;

export type ComposerDraftState = {
  text: string;
  pastes: PastedContentMap;
};

function emptyDraft(): ComposerDraftState {
  return { text: "", pastes: {} };
}

function sanitizePastes(value: unknown): PastedContentMap {
  if (!value || typeof value !== "object") return {};
  const out: PastedContentMap = {};
  let total = 0;
  for (const [key, body] of Object.entries(value as Record<string, unknown>)) {
    if (typeof body !== "string" || !key.startsWith("[Pasted Content ")) continue;
    if (total + body.length > COMPOSER_DRAFT_PASTE_MAX_CHARS) break;
    out[key] = body;
    total += body.length;
  }
  return out;
}

function parseDraft(raw: string | null): ComposerDraftState {
  if (!raw) return emptyDraft();
  const trimmed = raw.trim();
  if (trimmed.startsWith("{")) {
    try {
      const parsed = JSON.parse(trimmed) as {
        v?: number;
        text?: string;
        pastes?: PastedContentMap;
      };
      if (parsed && parsed.v === DRAFT_V2 && typeof parsed.text === "string") {
        return {
          text: parsed.text.slice(0, COMPOSER_DRAFT_MAX_CHARS),
          pastes: sanitizePastes(parsed.pastes),
        };
      }
    } catch {
      // Plain text that happens to start with `{`.
    }
  }
  return { text: raw.slice(0, COMPOSER_DRAFT_MAX_CHARS), pastes: {} };
}

export function composerDraftStorageKey(chatKey?: string | null): string | null {
  const clean = chatKey?.trim();
  return clean ? `${COMPOSER_DRAFT_STORAGE_PREFIX}${clean}` : null;
}

export function readComposerDraftState(chatKey?: string | null): ComposerDraftState {
  const storageKey = composerDraftStorageKey(chatKey);
  if (!storageKey || typeof window === "undefined") return emptyDraft();
  try {
    return parseDraft(window.localStorage.getItem(storageKey));
  } catch {
    return emptyDraft();
  }
}

export function readComposerDraft(chatKey?: string | null): string {
  return readComposerDraftState(chatKey).text;
}

export function writeComposerDraft(
  chatKey: string | null | undefined,
  text: string,
  pastes?: PastedContentMap,
): void {
  const storageKey = composerDraftStorageKey(chatKey);
  if (!storageKey || typeof window === "undefined") return;
  try {
    const next = text.slice(0, COMPOSER_DRAFT_MAX_CHARS);
    const used = usedPastedContent(next, sanitizePastes(pastes));
    if (!next.trim()) {
      window.localStorage.removeItem(storageKey);
      return;
    }
    const payload = Object.keys(used).length > 0
      ? JSON.stringify({ v: DRAFT_V2, text: next, pastes: used })
      : next;
    window.localStorage.setItem(storageKey, payload);
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
