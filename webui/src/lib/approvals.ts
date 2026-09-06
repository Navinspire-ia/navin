/**
 * The list of approval requests a chat is currently waiting on.
 *
 * A request is a suspended tool call: the agent has stopped mid-turn and cannot
 * continue until this is answered or expires. The list therefore has to be right
 * about three awkward cases, which is why the logic lives here rather than in the
 * component:
 *
 * - the same request arriving twice, because the backend sends it to every
 *   connection subscribed to the chat and the client replays buffered frames when
 *   a chat is opened again;
 * - a request that is already over, either because a close frame arrived first or
 *   because a replayed one expired while the user was looking elsewhere;
 * - a close frame for something that was never shown.
 */

export interface PendingApproval {
  requestId: string;
  tool: string;
  action: string;
  reason: string;
  detail: string;
  consequence: string;
  scope: string;
  rememberOffered: boolean;
  /** Epoch milliseconds after which the backend refuses on its own. */
  expiresAt: number | null;
  receivedAt: number;
}

export interface ApprovalRequestFrame {
  request_id: string;
  tool: string;
  action: string;
  reason: string;
  detail?: string;
  consequence?: string;
  scope?: string;
  remember_offered?: boolean;
  expires_at_ms?: number | null;
}

export function toPendingApproval(
  frame: ApprovalRequestFrame,
  now: number,
): PendingApproval {
  return {
    requestId: frame.request_id,
    tool: frame.tool,
    action: frame.action,
    reason: frame.reason,
    detail: frame.detail ?? "",
    consequence: frame.consequence ?? "",
    scope: frame.scope ?? "",
    // Remembering only means something when the backend has a scope to attach it
    // to, so an offer without one is dropped rather than shown and ignored.
    rememberOffered: Boolean(frame.remember_offered) && Boolean(frame.scope),
    expiresAt:
      typeof frame.expires_at_ms === "number" ? frame.expires_at_ms : null,
    receivedAt: now,
  };
}

export function addApproval(
  pending: readonly PendingApproval[],
  frame: ApprovalRequestFrame,
  now: number,
  settled: ReadonlySet<string> = new Set(),
): PendingApproval[] {
  const request = toPendingApproval(frame, now);
  if (!request.requestId) return [...pending];
  // A replayed frame can be older than the answer to it: the close always wins,
  // whatever order the two arrive in.
  if (settled.has(request.requestId)) return [...pending];
  if (isExpired(request, now)) return [...pending];
  const without = pending.filter((entry) => entry.requestId !== request.requestId);
  return [...without, request];
}

export function removeApproval(
  pending: readonly PendingApproval[],
  requestId: string,
): PendingApproval[] {
  return pending.filter((entry) => entry.requestId !== requestId);
}

export function isExpired(request: PendingApproval, now: number): boolean {
  return request.expiresAt !== null && request.expiresAt <= now;
}

export function dropExpired(
  pending: readonly PendingApproval[],
  now: number,
): PendingApproval[] {
  return pending.filter((entry) => !isExpired(entry, now));
}

/** Whole seconds left, for a countdown. Null when the request never expires. */
export function secondsLeft(
  request: PendingApproval,
  now: number,
): number | null {
  if (request.expiresAt === null) return null;
  return Math.max(0, Math.ceil((request.expiresAt - now) / 1000));
}
