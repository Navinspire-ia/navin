// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Turning gateway answers into something the Evolve panel can render.
 *
 * Two shapes reach the panel and neither is fit to print: a daemon that is
 * simply not there, and a failed HTTP call whose body may be raw library
 * boilerplate. Both are classified here, into keys the panel translates, so
 * no transport detail ever lands in front of a user.
 */

import type { DaemonReason, DaemonStatus } from "@/lib/evolve-api";
import { isTimeoutError, isUnreachableError } from "@/lib/http";

export type DaemonAvailability =
  | { state: "online" }
  /** No daemon is running, but this host could run one. */
  | { state: "offline" }
  /**
   * The gateway says it cannot host one. Every platform can since the daemon
   * moved to a loopback port, so this now only means an out-of-date gateway.
   */
  | { state: "unsupported" }
  /** Something answered, badly. */
  | { state: "unreachable" };

/**
 * Read the daemon block of an overview.
 *
 * Gateways older than the `supported`/`reason` payload only send `online`;
 * for them a missing daemon is the ordinary "not started" case, which is what
 * they always meant.
 */
export function daemonAvailability(
  daemon: DaemonStatus | null | undefined,
): DaemonAvailability {
  if (!daemon) return { state: "offline" };
  if (daemon.online) return { state: "online" };
  if (daemon.supported === false) return { state: "unsupported" };
  const reason: DaemonReason | null | undefined = daemon.reason;
  if (reason === "unsupported") return { state: "unsupported" };
  if (reason === "unreachable") return { state: "unreachable" };
  return { state: "offline" };
}

export type EvolveFailureKind =
  | "unauthorized"
  | "network"
  | "timeout"
  | "server"
  | "notFound"
  | "unknown";

export interface EvolveFailure {
  kind: EvolveFailureKind;
  /**
   * Technical text worth keeping behind a disclosure, `""` when the raw
   * message is boilerplate that explains nothing to anyone.
   */
  detail: string;
}

/**
 * The gateway is a `websockets` server answering HTTP from its handshake
 * hook: any unhandled server-side error comes back as this body. It names a
 * WebSocket the panel never opened, so showing it is worse than showing
 * nothing.
 */
const LIBRARY_BOILERPLATE = [
  /failed to open a websocket connection/i,
  /see server log for more information/i,
  /^internal server error$/i,
];

function isBoilerplate(message: string): boolean {
  return LIBRARY_BOILERPLATE.some((pattern) => pattern.test(message));
}

function messageOf(error: unknown): string {
  if (error instanceof Error) return error.message.trim();
  if (typeof error === "string") return error.trim();
  if (error == null) return "";
  return String(error).trim();
}

/** Classify a failed Evolve request. Never returns raw boilerplate as detail. */
export function classifyEvolveFailure(error: unknown): EvolveFailure {
  const message = messageOf(error);
  const status = (error as { status?: unknown } | null)?.status;
  const code = typeof status === "number" ? status : undefined;

  let kind: EvolveFailureKind = "unknown";
  if (code === 401 || code === 403 || /\bunauthorized\b|\bforbidden\b/i.test(message)) {
    kind = "unauthorized";
  } else if (code === 404 || /\bno such directory\b/i.test(message)) {
    kind = "notFound";
  } else if (isTimeoutError(error) || /timed out|timeout|aborted/i.test(message)) {
    kind = "timeout";
  } else if (
    isUnreachableError(error) ||
    /failed to fetch|network ?error|load failed|networkerror/i.test(message)
  ) {
    kind = "network";
  } else if ((code !== undefined && code >= 500) || isBoilerplate(message)) {
    kind = "server";
  }

  return { kind, detail: isBoilerplate(message) ? "" : message };
}
