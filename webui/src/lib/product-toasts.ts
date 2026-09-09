// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Admin-published release card. Hidden when the install toast already covers it. */
export function isReleaseAnnouncement(item: { id: string; kind?: string }): boolean {
  return item.kind === "release" || item.id.startsWith("release-");
}
