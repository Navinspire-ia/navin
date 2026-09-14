// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Pure decision core for the chat metagraph side panel.
 *
 * The panel never opens itself. The user clicks Graph (chat header or the
 * Code workbench tab). Tool events must not reopen a panel the user closed.
 */

export interface MetagraphToolEventLike {
  name?: string;
  phase?: string;
  call_id?: string;
}

export interface MetagraphPanelState {
  open: boolean;
  /** True once the user closed the panel. Tool events must not clear this. */
  dismissed: boolean;
  /** Call ids already seen. Kept for callers that still track them. */
  seenCallIds: Set<string>;
}

export interface MetagraphPanelDecision {
  open: boolean;
  dismissed: boolean;
  seenCallIds: Set<string>;
}

export function nextMetagraphPanelState(
  events: MetagraphToolEventLike[],
  state: MetagraphPanelState,
): MetagraphPanelDecision {
  const seenCallIds = state.seenCallIds;
  for (const event of events) {
    if (event.name !== "metagraph" || event.phase !== "end") continue;
    const callId = typeof event.call_id === "string" ? event.call_id : "";
    if (callId && !seenCallIds.has(callId)) seenCallIds.add(callId);
  }
  return {
    open: state.open,
    dismissed: state.dismissed,
    seenCallIds,
  };
}
