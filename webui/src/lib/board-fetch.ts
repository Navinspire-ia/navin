// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { fetchBoard, type BoardPayload } from "@/lib/api";

/**
 * ``fetchBoard`` shared by the panels that ask for the same board together.
 *
 * One ``board_updated`` broadcast made the rail counters, the plan panels and
 * the board view each read the same board in the same tick. On a WSL project
 * seen from Windows one read is ~0.4 s of 9P traffic, and the copies queued
 * behind each other (desktop log: 1,278 board reads over 1 s in two days).
 *
 * Only reads started within ``START_WINDOW_MS`` of each other share one
 * request: they would observe the same server state anyway. A read started
 * earlier is never reused, so a reload after a write always asks again.
 */
const START_WINDOW_MS = 100;

interface Entry {
  promise: Promise<BoardPayload>;
  startedAt: number;
}

const shared = new Map<string, Entry>();

export function fetchBoardShared(token: string, key: string): Promise<BoardPayload> {
  const now = Date.now();
  const entry = shared.get(key);
  if (entry && now - entry.startedAt < START_WINDOW_MS) return entry.promise;
  const next: Entry = { promise: fetchBoard(token, key), startedAt: now };
  shared.set(key, next);
  const release = () => {
    if (shared.get(key) === next) shared.delete(key);
  };
  next.promise.then(release, release);
  return next.promise;
}
