// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useSyncExternalStore } from "react";

/** What the side inspector shows: a finished command (with its test report) or a file diff. */
export type InspectorTarget =
  | {
      kind: "command";
      id: string;
      command: string;
      output: string;
      status: "running" | "done" | "error";
      durationMs?: number;
    }
  | {
      kind: "diff";
      id: string;
      path: string;
      language?: string;
      original: string;
      modified: string;
    };

let current: InspectorTarget | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

export function openInspector(target: InspectorTarget): void {
  current = target;
  emit();
}

export function closeInspector(): void {
  if (current === null) return;
  current = null;
  emit();
}

export function getInspectorTarget(): InspectorTarget | null {
  return current;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useInspectorTarget(): InspectorTarget | null {
  return useSyncExternalStore(subscribe, getInspectorTarget, () => null);
}

export const INSPECTOR_WIDTH_KEY = "navin.inspector.width";
export const INSPECTOR_MIN_WIDTH = 320;
export const INSPECTOR_MAX_WIDTH = 960;

export function clampInspectorWidth(width: number): number {
  if (!Number.isFinite(width)) return 480;
  return Math.round(Math.min(INSPECTOR_MAX_WIDTH, Math.max(INSPECTOR_MIN_WIDTH, width)));
}
