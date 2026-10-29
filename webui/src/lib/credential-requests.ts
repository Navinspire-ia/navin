// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Credential cards: the agent needs a site login, a git account or an app.
 *
 * Contract (websocket):
 *   in  `credential_request` {request_id, kind: site_login|git_host|connector, host, names[], reason, expires_at_ms}
 *   in  `credential_closed`  {request_id, status, refs[], login}
 *   out `credential_answer`  {request_id, status: saved|connected|declined, login?}
 *
 * Values never travel on the socket: the card saves them through the vault
 * HTTP routes (/api/browser-credentials, Settings > Git & GitHub,
 * /api/connectors), then sends only a status. The engine re-checks the vault
 * (or the connector) before releasing the tool. For kind=connector the host
 * is the connector id (slack, hubspot, stripe...).
 */

import type { InboundEvent } from "./types";

export type CredentialKind = "site_login" | "git_host" | "connector";
export type CredentialAnswerStatus = "saved" | "connected" | "declined";

export interface PendingCredentialRequest {
  requestId: string;
  kind: CredentialKind;
  host: string;
  names: string[];
  reason: string;
  expiresAtMs: number | null;
}

export interface ClosedCredentialRequest {
  requestId: string;
  status: string;
  refs: string[];
  login: string;
}

const NAMES = new Set(["username", "password", "totp"]);

export function credentialRequestFromEvent(ev: InboundEvent): PendingCredentialRequest | null {
  if (ev.event !== "credential_request") return null;
  const kind =
    ev.kind === "git_host" || ev.kind === "site_login" || ev.kind === "connector" ? (ev.kind as CredentialKind) : null;
  if (!kind || !ev.request_id || !ev.host) return null;
  const names = Array.isArray(ev.names) ? ev.names.filter((name) => NAMES.has(name)) : [];
  return {
    requestId: ev.request_id,
    kind,
    host: ev.host,
    names: kind === "site_login" ? (names.length ? names : ["username", "password"]) : [],
    reason: typeof ev.reason === "string" ? ev.reason : "",
    expiresAtMs: typeof ev.expires_at_ms === "number" ? ev.expires_at_ms : null,
  };
}

export function credentialClosedFromEvent(ev: InboundEvent): ClosedCredentialRequest | null {
  if (ev.event !== "credential_closed" || !ev.request_id) return null;
  return {
    requestId: ev.request_id,
    status: ev.status ?? "",
    refs: Array.isArray(ev.refs) ? ev.refs.filter((ref) => typeof ref === "string") : [],
    login: typeof ev.login === "string" ? ev.login : "",
  };
}

export function addCredentialRequest(
  queue: PendingCredentialRequest[],
  request: PendingCredentialRequest,
): PendingCredentialRequest[] {
  if (queue.some((item) => item.requestId === request.requestId)) return queue;
  return [...queue, request];
}

export function removeCredentialRequest(
  queue: PendingCredentialRequest[],
  requestId: string,
): PendingCredentialRequest[] {
  const next = queue.filter((item) => item.requestId !== requestId);
  return next.length === queue.length ? queue : next;
}
