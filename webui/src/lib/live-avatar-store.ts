// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useSyncExternalStore } from "react";

import type { LiveVoiceState } from "@/lib/live-voice";

/**
 * The Live call, as the avatar needs it. The chat (ThreadShell) owns the call
 * and publishes it; the avatar shows next to the chat, docked in the Code
 * rail under its Collapse button when that rail is on screen.
 */
export type LiveAvatarActions = {
  setMuted: (muted: boolean) => void;
  skipSpeech: () => void;
  stop: () => void;
  openSettings?: () => void;
  switchAvatar?: () => void;
};

export type LiveAvatarState = {
  active: boolean;
  voice: string | null;
  speechLevel: () => number;
  /** The call, for the Live controls under a docked avatar. */
  state: LiveVoiceState;
  muted: boolean;
  actions: LiveAvatarActions | null;
};

const silent = () => 0;
let state: LiveAvatarState = { active: false, voice: null, speechLevel: silent, state: "off", muted: false, actions: null };
let docks = 0;
const listeners = new Set<() => void>();

function notify() {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function publishLiveAvatar(next: LiveAvatarState): void {
  if (
    next.active === state.active && next.voice === state.voice && next.speechLevel === state.speechLevel
    && next.state === state.state && next.muted === state.muted && next.actions === state.actions
  ) return;
  state = next;
  notify();
}

export function useLiveAvatarState(): LiveAvatarState {
  return useSyncExternalStore(subscribe, () => state, () => state);
}

/** True while a dock (the Code rail) shows the avatar: the chat then does not. */
export function useLiveAvatarDocked(): boolean {
  return useSyncExternalStore(subscribe, () => docks > 0, () => false);
}

/** Mount a dock for the avatar for as long as the calling component lives. */
export function useLiveAvatarDock(): void {
  useEffect(() => {
    docks += 1;
    notify();
    return () => {
      docks -= 1;
      notify();
    };
  }, []);
}
