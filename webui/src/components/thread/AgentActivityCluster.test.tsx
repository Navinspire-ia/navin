// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { AgentActivityCluster } from "@/components/thread/AgentActivityCluster";

type MemoComponent = { $$typeof: symbol; type: unknown };

describe("AgentActivityCluster memo", () => {
  it("is a React.memo wrapper so scroll ticks skip unchanged clusters", () => {
    const wrapped = AgentActivityCluster as unknown as MemoComponent;
    expect(wrapped.$$typeof).toBe(Symbol.for("react.memo"));
    expect(typeof wrapped.type).toBe("function");
  });
});
