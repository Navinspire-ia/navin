// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  nextMetagraphPanelState,
  type MetagraphPanelState,
  type MetagraphToolEventLike,
} from "@/lib/metagraph-panel";

function event(partial: MetagraphToolEventLike): MetagraphToolEventLike {
  return partial;
}

function baseState(partial: Partial<MetagraphPanelState> = {}): MetagraphPanelState {
  return { open: false, dismissed: false, seenCallIds: new Set<string>(), ...partial };
}

describe("nextMetagraphPanelState", () => {
  it("stays closed when no metagraph event ran", () => {
    const decision = nextMetagraphPanelState(
      [event({ name: "grep", phase: "end", call_id: "c1" })],
      baseState(),
    );
    expect(decision.open).toBe(false);
    expect(decision.dismissed).toBe(false);
  });

  it("does not auto-open when the agent finishes a metagraph call", () => {
    const decision = nextMetagraphPanelState(
      [event({ name: "metagraph", phase: "end", call_id: "c1" })],
      baseState(),
    );
    expect(decision.open).toBe(false);
    expect(decision.seenCallIds.has("c1")).toBe(true);
  });

  it("ignores start phases and other tools", () => {
    const decision = nextMetagraphPanelState(
      [
        event({ name: "metagraph", phase: "start", call_id: "c1" }),
        event({ name: "grep", phase: "end", call_id: "c2" }),
      ],
      baseState(),
    );
    expect(decision.open).toBe(false);
  });

  it("keeps a user-closed panel closed for the same and later tool calls", () => {
    const first = nextMetagraphPanelState(
      [event({ name: "metagraph", phase: "end", call_id: "c1" })],
      baseState({ dismissed: true }),
    );
    expect(first.open).toBe(false);
    expect(first.dismissed).toBe(true);

    const again = nextMetagraphPanelState(
      [event({ name: "metagraph", phase: "end", call_id: "c2" })],
      baseState({ dismissed: true, seenCallIds: first.seenCallIds }),
    );
    expect(again.open).toBe(false);
    expect(again.dismissed).toBe(true);
  });

  it("does not close a panel the user already opened", () => {
    const decision = nextMetagraphPanelState(
      [event({ name: "metagraph", phase: "end", call_id: "c9" })],
      baseState({ open: true }),
    );
    expect(decision.open).toBe(true);
  });
});
