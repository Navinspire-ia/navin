// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";
import { retainAgentTerminals } from "./agent-terminals";

describe("agent terminal retention", () => {
  it("keeps live commands and user shells while retiring a 61-command history", () => {
    const history = Array.from({ length: 61 }, (_, i) => ({ id: `agent-${i}`, kind: "agent" as const, exited: true }));
    const live = { id: "server", kind: "agent" as const, exited: false };
    const shell = { id: "my-shell", kind: "pty" as const, exited: false };
    const kept = retainAgentTerminals([...history, live, shell], "agent-3");
    expect(kept.map((tab) => tab.id)).toEqual(["agent-3", "agent-58", "agent-59", "agent-60", "server", "my-shell"]);
    expect(retainAgentTerminals(kept, "server").map((tab) => tab.id)).not.toContain("agent-3");
    expect(kept).toContain(live);
    expect(kept).toContain(shell);
  });

  it("does not evict real processes or completed user shells", () => {
    const tabs = Array.from({ length: 61 }, (_, i) => ({ id: `live-${i}`, kind: "agent" as const, exited: false }));
    const shell = { id: "my-shell", kind: "pty" as const, exited: true };
    const all = [...tabs, shell];
    expect(retainAgentTerminals(all, null)).toBe(all);
  });
});
