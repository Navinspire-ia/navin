// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only
export interface DeskArchive { id: string; at: number; path: string; retained_candidates: number }
export interface ArchiveFile { name: string; data_b64: string; mime?: string }

export function saveArchive(file: ArchiveFile) {
  const data = Uint8Array.from(atob(file.data_b64), c => c.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([data], { type: file.mime || "application/zip" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = file.name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
