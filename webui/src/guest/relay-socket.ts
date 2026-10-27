// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { ShareCipher } from "./share-crypto";

/** Envelopes a guest may send; the host checks the rights again anyway. */
const GUEST_ENVELOPES = new Set(["message", "choice_answer", "subagents_list", "subagent_history", "subagent_recover", "subagent_relaunch", "subagent_cancel", "subagent_retry"]);

export interface ShareSnapshot {
  chat_id: string;
  thread: unknown;
  context?: ShareContext | null;
  rights: { drive: boolean; download: boolean };
  expires_at: number;
  /** The host shares its running site with this link (guest-site.ts). */
  site?: boolean;
  /** "site": an app link, the running site and nothing of the conversation. */
  scope?: "session" | "site";
}

/** navin/share/context.py */
export interface ShareContext {
  generated_at: number;
  conversation: {
    title: string;
    created_at: string;
    updated_at: string;
    model: string;
    prompts: { index: number; text: string; at: string }[];
    user_messages: number;
    assistant_messages: number;
    tool_calls: number;
    files_touched: string[];
    last_reply: string;
  } | null;
  project: {
    name: string;
    path: string;
    stack: string[];
    commands: Record<string, string>;
    readme: string;
    instructions: string[];
  } | null;
  git: {
    root: string;
    branch: string;
    detached: boolean;
    head: string;
    upstream: string;
    ahead: number;
    behind: number;
    remote: string;
    branches: string[];
    stashes: number;
    commits: { sha: string; author: string; date: string; subject: string }[];
    changes: { status: string; path: string }[];
    changes_total: number;
    insertions: number;
    deletions: number;
  } | null;
  /** Model and context fill of the last turn (host context meter). */
  usage?: {
    model: string;
    tokens: number;
    context_window: number;
    percent: number;
  } | null;
}

export interface RelaySessionHooks {
  onSnapshot: (snapshot: ShareSnapshot) => void;
  /** Host-side payloads that are not chat events: rights, refused, bundle_*. */
  onPayload: (payload: Record<string, unknown>) => void;
  onHostOnline: (online: boolean) => void;
  /** The relay closed the share for good: revoked, expired, removed, full. A
   *  ``closed`` carrying ``retry`` (host not connected yet) only reports the
   *  host offline and lets the client reconnect. */
  onClosed: (reason: string) => void;
  /** Real join steps, in order, until the snapshot shows the session. */
  onJoinStage?: (stage: JoinStage) => void;
}

/**
 * Steps of a guest join, each one observed on the wire (no timer):
 * socket open, relay welcome, hello sent to the host, first sealed frame
 * from the host, snapshot.
 */
export type JoinStage = "socket" | "relay" | "hello" | "data" | "ready";

export const JOIN_STAGE_PERCENT: Record<JoinStage, number> = {
  socket: 20,
  relay: 40,
  hello: 60,
  data: 80,
  ready: 100,
};

/**
 * One guest seat on a share. ``socketFactory`` hands NavinClient a socket that
 * speaks the relay protocol underneath, so the regular chat client (stream,
 * approvals, choices, presence) runs unchanged on top of the encrypted pipe.
 */
export class RelaySession {
  private current: RelaySocket | null = null;
  closedReason: string | null = null;

  constructor(
    readonly url: string,
    readonly cipher: ShareCipher,
    readonly name: string,
    readonly hooks: RelaySessionHooks,
  ) {}

  socketFactory = (): WebSocket => {
    if (this.closedReason) throw new Error(this.closedReason);
    this.current = new RelaySocket(this);
    return this.current as unknown as WebSocket;
  };

  /** request_control, request_download, history, context, media, site: beside the chat envelopes. */
  async request(type: "request_control" | "request_download" | "history" | "context" | "media" | "site" | "files" | "file", extra: Record<string, unknown> = {}): Promise<void> {
    await this.current?.sendSealed({ ...extra, type });
  }
}

type Handler<E> = ((event: E) => void) | null;

export class RelaySocket {
  readyState = 0;
  onopen: Handler<Event> = null;
  onmessage: Handler<MessageEvent> = null;
  onerror: Handler<Event> = null;
  onclose: Handler<{ code?: number; reason?: string }> = null;
  private ws: WebSocket;
  // Frames are decrypted asynchronously; a chain keeps them in order.
  private inbound: Promise<void> = Promise.resolve();
  private eventParts: { id: string; total: number; parts: string[] } | null = null;

  constructor(private readonly session: RelaySession) {
    this.ws = new WebSocket(session.url);
    this.ws.onmessage = (ev) => {
      const raw = typeof ev.data === "string" ? ev.data : "";
      this.inbound = this.inbound.then(() => this.handle(raw)).catch(() => undefined);
    };
    this.ws.onopen = () => this.session.hooks.onJoinStage?.("socket");
    this.ws.onerror = () => this.onerror?.(new Event("error"));
    this.ws.onclose = (ev) => {
      this.eventParts = null;
      this.readyState = 3;
      this.onclose?.({ code: ev.code, reason: ev.reason });
    };
  }

  private async handle(raw: string): Promise<void> {
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return;
    }
    const { hooks } = this.session;
    switch (msg.type) {
      case "welcome":
        hooks.onJoinStage?.("relay");
        hooks.onHostOnline(Boolean(msg.host_online));
        await this.sendSealed({ type: "hello", name: this.session.name });
        hooks.onJoinStage?.("hello");
        return;
      case "host_online":
        hooks.onHostOnline(true);
        // A reconnecting host forgot this seat: introduce ourselves again.
        await this.sendSealed({ type: "hello", name: this.session.name });
        return;
      case "host_offline":
        hooks.onHostOnline(false);
        return;
      case "closed":
        if (msg.retry) {
          // The relay has no room for this id right now (host reconnecting,
          // relay restarted): the socket closes and NavinClient dials again.
          hooks.onHostOnline(false);
          return;
        }
        this.session.closedReason = String(msg.reason || "closed");
        hooks.onClosed(this.session.closedReason);
        return;
      case "data":
        break;
      default:
        return;
    }
    if (this.readyState === 0) hooks.onJoinStage?.("data");
    let payload: Record<string, unknown>;
    try {
      payload = await this.session.cipher.open(String(msg.data || ""));
    } catch {
      return; // not sealed with this link's key
    }
    if (payload.type === "snapshot") {
      const snapshot = payload as unknown as ShareSnapshot;
      hooks.onSnapshot(snapshot);
      hooks.onJoinStage?.("ready");
      if (this.readyState === 0) {
        this.readyState = 1;
        this.onopen?.(new Event("open"));
      }
      this.emit({ event: "ready", chat_id: snapshot.chat_id, client_id: "guest" });
      return;
    }
    if (payload.type === "event_part") {
      const index = payload.index;
      const total = payload.total;
      const id = payload.id;
      if (typeof index !== "number" || !Number.isSafeInteger(index) || typeof total !== "number" || !Number.isSafeInteger(total) || total < 1 || index < 0 || index >= total || typeof id !== "string" || typeof payload.frame_part !== "string") {
        this.eventParts = null;
        return;
      }
      if (index === 0) this.eventParts = { id, total, parts: [] };
      const pending = this.eventParts;
      if (!pending || pending.id !== id || pending.total !== total || pending.parts.length !== index) {
        this.eventParts = null;
        return;
      }
      pending.parts.push(payload.frame_part);
      if (pending.parts.length === total) {
        this.eventParts = null;
        if (this.readyState === 1) this.onmessage?.(new MessageEvent("message", { data: pending.parts.join("") }));
      }
      return;
    }
    if (payload.type === "event") {
      if (this.readyState === 1 && typeof payload.frame === "string") {
        this.onmessage?.(new MessageEvent("message", { data: payload.frame }));
      }
      return;
    }
    hooks.onPayload(payload);
  }

  private emit(frame: Record<string, unknown>): void {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(frame) }));
  }

  async sendSealed(payload: Record<string, unknown>): Promise<void> {
    if (this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ type: "data", data: await this.session.cipher.seal(payload) }));
  }

  /** What NavinClient sends. Attach and other host-only frames stay local. */
  send(text: string): void {
    let frame: Record<string, unknown>;
    try {
      frame = JSON.parse(text) as Record<string, unknown>;
    } catch {
      return;
    }
    if (!GUEST_ENVELOPES.has(String(frame.type))) return;
    void this.sendSealed({ type: "envelope", envelope: frame });
  }

  close(): void {
    this.readyState = 2;
    this.ws.close();
  }
}
