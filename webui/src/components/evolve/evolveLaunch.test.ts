// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { evolveLaunchParams } from "./evolveLaunch";

describe("evolveLaunchParams", () => {
  it("proves the working tree by default so pending file edits are included", () => {
    expect(evolveLaunchParams("proof.run", { profile: "quick" })).toEqual({
      start: undefined,
      url: undefined,
      test: undefined,
      profile: "quick",
      dirty: true,
    });
  });

  it("can prove HEAD only when the user unchecks uncommitted edits", () => {
    expect(
      evolveLaunchParams("proof.run", { profile: "standard", includeDirty: false }),
    ).toMatchObject({ dirty: false, profile: "standard" });
  });

  it("does not send dirty on optimize or evolve runs", () => {
    expect(evolveLaunchParams("optimize.run", { objective: "p95" }).dirty).toBeUndefined();
    expect(evolveLaunchParams("evolve.run", { profile: "quick", preset: "opus" }).dirty)
      .toBeUndefined();
  });
});
