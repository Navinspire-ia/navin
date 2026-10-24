// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { GuestFilesSource, SharedFile } from "./GuestFiles";

type Send = (type: "files" | "file", extra: Record<string, unknown>) => void;

interface ListWait {
  pages: SharedFile[][];
  received: number;
  resolve: (value: { files: SharedFile[]; truncated: boolean; project: string }) => void;
  reject: (reason: Error) => void;
}

interface FileWait {
  parts: string[];
  received: number;
  resolve: (value: { blob: Blob; name: string }) => void;
  reject: (reason: Error) => void;
}

const TIMEOUT_MS = 60_000;

/** The project's files over the encrypted share (navin/share/files.py on the host). */
export class GuestFilesClient implements GuestFilesSource {
  private seq = 0;
  private lists = new Map<string, ListWait>();
  private files = new Map<string, FileWait>();

  constructor(private readonly send: Send) {}

  list(): Promise<{ files: SharedFile[]; truncated: boolean; project: string }> {
    const id = `l${(this.seq += 1)}`;
    return new Promise((resolve, reject) => {
      this.lists.set(id, { pages: [], received: 0, resolve, reject });
      this.send("files", { id });
      window.setTimeout(() => this.failList(id, "timeout"), TIMEOUT_MS);
    });
  }

  /** The file itself, or with ``preview`` what the viewer shows (Excel grid, Office as PDF). */
  fetch(path: string, options: { preview?: boolean } = {}): Promise<{ blob: Blob; name: string }> {
    const id = `f${(this.seq += 1)}`;
    return new Promise((resolve, reject) => {
      this.files.set(id, { parts: [], received: 0, resolve, reject });
      this.send("file", options.preview ? { id, path, preview: true } : { id, path });
      window.setTimeout(() => this.failFile(id, "timeout"), TIMEOUT_MS * 3);
    });
  }

  /** Host frames: files, file_chunk, file_error. */
  handle(payload: Record<string, unknown>): void {
    const id = String(payload.id ?? "");
    if (payload.type === "file_error") {
      this.failList(id, String(payload.reason || "failed"));
      this.failFile(id, String(payload.reason || "failed"));
      return;
    }
    const total = Number(payload.total) || 1;
    const index = Number(payload.index) || 0;
    if (payload.type === "files") {
      const wait = this.lists.get(id);
      if (!wait) return;
      if (!wait.pages[index]) wait.received += 1;
      wait.pages[index] = (payload.files as SharedFile[]) ?? [];
      if (wait.received < total) return;
      this.lists.delete(id);
      wait.resolve({ files: wait.pages.flat(), truncated: Boolean(payload.truncated), project: String(payload.project || "") });
      return;
    }
    if (payload.type === "file_chunk") {
      const wait = this.files.get(id);
      if (!wait) return;
      if (wait.parts[index] === undefined) wait.received += 1;
      wait.parts[index] = String(payload.data ?? "");
      if (wait.received < total) return;
      this.files.delete(id);
      const bytes = wait.parts.map((part) => Uint8Array.from(atob(part), (c) => c.charCodeAt(0)));
      wait.resolve({ blob: new Blob(bytes, { type: String(payload.mime || "application/octet-stream") }), name: String(payload.name || "file") });
    }
  }

  private failList(id: string, reason: string): void {
    const wait = this.lists.get(id);
    if (!wait) return;
    this.lists.delete(id);
    wait.reject(new Error(reason));
  }

  private failFile(id: string, reason: string): void {
    const wait = this.files.get(id);
    if (!wait) return;
    this.files.delete(id);
    wait.reject(new Error(reason));
  }
}
