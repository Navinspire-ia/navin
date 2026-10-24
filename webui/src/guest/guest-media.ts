// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { UIMessage } from "@/lib/types";

const MEDIA_URL = /\/api\/media\/[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+/g;
const MAX_PARALLEL = 3;

type Entry = { state: "loading" } | { state: "ready"; blobUrl: string } | { state: "error" };

/**
 * The thread's images, audio and files live on the host behind signed URLs a
 * guest cannot reach. They come through the encrypted pipe on demand and are
 * shown from blob: URLs.
 */
export class GuestMedia {
  private entries = new Map<string, Entry>();
  private chunks = new Map<string, string[]>();
  private queue: string[] = [];
  private inFlight = 0;
  private listeners = new Set<() => void>();
  version = 0;

  constructor(private readonly request: (payload: Record<string, unknown>) => void) {}

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** The blob: URL when it is here; asks the host for it otherwise. */
  resolve(url: string): string | undefined {
    const entry = this.entries.get(url);
    if (entry?.state === "ready") return entry.blobUrl;
    if (!entry) {
      this.entries.set(url, { state: "loading" });
      this.queue.push(url);
      this.pump();
    }
    return undefined;
  }

  private pump(): void {
    while (this.inFlight < MAX_PARALLEL && this.queue.length) {
      const url = this.queue.shift()!;
      this.inFlight += 1;
      this.request({ type: "media", id: url, url });
    }
  }

  /** media_chunk / media_error payloads from the host. */
  handle(payload: Record<string, unknown>): void {
    const url = String(payload.id || "");
    if (!this.entries.has(url)) return;
    if (payload.type === "media_error") {
      this.settle(url, { state: "error" });
      return;
    }
    const total = Number(payload.total) || 1;
    const parts = this.chunks.get(url) ?? [];
    parts[Number(payload.index) || 0] = String(payload.data || "");
    this.chunks.set(url, parts);
    if (parts.filter(Boolean).length < total) return;
    this.chunks.delete(url);
    const bytes = parts.map((part) => Uint8Array.from(atob(part), (c) => c.charCodeAt(0)));
    const blob = new Blob(bytes, { type: String(payload.mime || "application/octet-stream") });
    this.settle(url, { state: "ready", blobUrl: URL.createObjectURL(blob) });
  }

  /** The pipe dropped: what was asked will not come, ask again later. */
  reset(): void {
    for (const [url, entry] of this.entries) if (entry.state === "loading") this.entries.delete(url);
    this.queue = [];
    this.chunks.clear();
    this.inFlight = 0;
  }

  private settle(url: string, entry: Entry): void {
    this.entries.set(url, entry);
    this.inFlight = Math.max(0, this.inFlight - 1);
    this.version += 1;
    for (const listener of this.listeners) listener();
    this.pump();
  }
}

/** Messages with host media URLs swapped for local ones (or left out while loading). */
export function withGuestMedia(messages: UIMessage[], media: GuestMedia): UIMessage[] {
  return messages.map((message) => {
    let changed = false;
    const next: UIMessage = { ...message };
    if (message.media?.length) {
      next.media = message.media.map((item) => {
        if (!item.url || !item.url.startsWith("/api/media/")) return item;
        changed = true;
        return { ...item, url: media.resolve(item.url) };
      });
    }
    if (typeof message.content === "string" && message.content.includes("/api/media/")) {
      changed = true;
      next.content = message.content.replace(MEDIA_URL, (url) => media.resolve(url) ?? url);
    }
    return changed ? next : message;
  });
}
