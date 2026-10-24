// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { guestWorker, siteWorkerReady } from "./guest-worker";

/** What the worker asks (guest-sw.js) and what it gets back. */
interface WorkerFetch {
  type: "site-fetch";
  method: string;
  path: string;
  headers: [string, string][];
  body: ArrayBuffer | null;
}

export type SiteFailure = "off" | "unreachable" | "too_large" | "slow_down" | "timeout" | "bad_request" | "navin";

interface Pending {
  port: MessagePort;
  parts: string[];
  received: number;
  status: number;
  headers: [string, string][];
  timer: number;
}

// The relay drops a guest's frames over 1 MB and past 30 a second: requests
// travel in batches, at most one site frame every 50 ms.
const FRAME_GAP_MS = 50;
const MAX_BATCH = 64;
const MAX_BATCH_BYTES = 600 * 1024;
const MAX_BODY_BYTES = 256 * 1024;
const PENDING_TIMEOUT_MS = 95_000;

/**
 * The guest's end of the shared site: the worker hands over each request of
 * the site frame, this sends it to the host through the encrypted share, and
 * passes the answer back to the worker.
 */
export class GuestSiteTunnel {
  private pending = new Map<string, Pending>();
  private queue: { request: Record<string, unknown>; bytes: number }[] = [];
  private timer: number | null = null;
  private nextFrameAt = 0;
  private seq = 0;
  private listening = false;

  constructor(
    private readonly send: (requests: Record<string, unknown>[]) => void,
    private readonly explain: (failure: SiteFailure) => string,
  ) {}

  /** False when this browser cannot show the site (no service worker at the root). */
  async start(): Promise<boolean> {
    if (!(await siteWorkerReady())) return false;
    if (!this.listening) {
      navigator.serviceWorker.addEventListener("message", this.onWorker);
      this.listening = true;
    }
    (await guestWorker())?.active?.postMessage({ type: "site-owner" });
    return true;
  }

  dispose(): void {
    if (this.listening) navigator.serviceWorker.removeEventListener("message", this.onWorker);
    this.listening = false;
    this.failAll("unreachable");
  }

  /** The host went away: nothing in flight will be answered. */
  failAll(failure: SiteFailure): void {
    for (const id of [...this.pending.keys()]) this.fail(id, failure);
    this.queue = [];
  }

  /** A ``site_res`` frame from the host. */
  handle(payload: Record<string, unknown>): void {
    const id = String(payload.id ?? "");
    const entry = this.pending.get(id);
    if (!entry) return;
    if (payload.error) {
      this.fail(id, String(payload.error) as SiteFailure);
      return;
    }
    const index = Number(payload.index) || 0;
    if (entry.parts[index] === undefined) entry.received += 1;
    entry.parts[index] = String(payload.data ?? "");
    entry.status = Number(payload.status) || 502;
    entry.headers = Array.isArray(payload.headers) ? (payload.headers as [string, string][]) : [];
    if (entry.received < (Number(payload.total) || 1)) return;
    const body = joinBase64(entry.parts);
    this.finish(id, { status: entry.status, headers: entry.headers, body }, [body]);
  }

  private onWorker = (event: MessageEvent) => {
    const data = event.data as WorkerFetch | undefined;
    const port = event.ports[0];
    if (!data || data.type !== "site-fetch" || !port) return;
    if (data.body && data.body.byteLength > MAX_BODY_BYTES) {
      port.postMessage({ error: "too_large", message: this.explain("too_large") });
      return;
    }
    const id = `s${(this.seq += 1)}`;
    const timer = window.setTimeout(() => this.fail(id, "timeout"), PENDING_TIMEOUT_MS);
    this.pending.set(id, { port, parts: [], received: 0, status: 0, headers: [], timer });
    const body = data.body ? toBase64(data.body) : "";
    const request = { id, method: data.method, path: data.path, headers: data.headers, body };
    this.queue.push({ request, bytes: body.length + data.path.length + 1024 });
    this.schedule();
  };

  private schedule(): void {
    if (this.timer !== null || !this.queue.length) return;
    this.timer = window.setTimeout(() => {
      this.timer = null;
      this.flush();
    }, Math.max(0, this.nextFrameAt - Date.now()));
  }

  private flush(): void {
    const batch: Record<string, unknown>[] = [];
    let bytes = 0;
    while (this.queue.length && batch.length < MAX_BATCH) {
      const next = this.queue[0];
      if (batch.length && bytes + next.bytes > MAX_BATCH_BYTES) break;
      batch.push(next.request);
      bytes += next.bytes;
      this.queue.shift();
    }
    if (batch.length) this.send(batch);
    this.nextFrameAt = Date.now() + FRAME_GAP_MS;
    this.schedule();
  }

  private fail(id: string, failure: SiteFailure): void {
    this.finish(id, { error: failure, message: this.explain(failure) });
  }

  private finish(id: string, answer: Record<string, unknown>, transfer: Transferable[] = []): void {
    const entry = this.pending.get(id);
    if (!entry) return;
    this.pending.delete(id);
    window.clearTimeout(entry.timer);
    entry.port.postMessage(answer, transfer);
  }
}

function toBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let out = "";
  for (let i = 0; i < bytes.length; i += 0x8000) out += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(out);
}

function joinBase64(parts: string[]): ArrayBuffer {
  const chunks = parts.map((part) => Uint8Array.from(atob(part), (c) => c.charCodeAt(0)));
  const out = new Uint8Array(chunks.reduce((n, c) => n + c.length, 0));
  let at = 0;
  for (const chunk of chunks) {
    out.set(chunk, at);
    at += chunk.length;
  }
  return out.buffer;
}
