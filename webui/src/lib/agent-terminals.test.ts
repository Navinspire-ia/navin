// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";
import { retainAgentTerminals, terminalBelongsHere } from "./agent-terminals";

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

describe("terminals per project", () => {
  it("keeps the finished-command budget per chat", () => {
    const mine = Array.from({ length: 3 }, (_, i) => ({ id: `a-${i}`, kind: "agent" as const, exited: true, chatId: "shop" }));
    const other = Array.from({ length: 5 }, (_, i) => ({ id: `b-${i}`, kind: "agent" as const, exited: true, chatId: "crm" }));
    const kept = retainAgentTerminals([...mine, ...other], null).map((tab) => tab.id);
    expect(kept).toEqual(["a-0", "a-1", "a-2", "b-2", "b-3", "b-4"]);
  });

  it("shows only this chat's agent commands and this project's shells", () => {
    expect(terminalBelongsHere({ kind: "agent", chatId: "shop" }, "shop", "/p/shop")).toBe(true);
    expect(terminalBelongsHere({ kind: "agent", chatId: "crm" }, "shop", "/p/shop")).toBe(false);
    expect(terminalBelongsHere({ kind: "agent", chatId: "*" }, "shop", "/p/shop")).toBe(true);
    expect(terminalBelongsHere({ kind: "pty", root: "/p/shop" }, "shop", "/p/shop/")).toBe(true);
    expect(terminalBelongsHere({ kind: "pty", root: "/p/crm" }, "shop", "/p/shop")).toBe(false);
    expect(terminalBelongsHere({ kind: "pty" }, "shop", "/p/shop")).toBe(true);
  });
});
