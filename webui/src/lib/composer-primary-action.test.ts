// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { composerPrimaryAction } from "./composer-primary-action";

const base = {
  isStreaming: false,
  hasStopHandler: true,
  hasComposerContent: false,
  canQueueGuidance: false,
  modelNeedsSetup: false,
};

describe("composerPrimaryAction", () => {
  it("idle composer sends", () => {
    expect(composerPrimaryAction(base)).toBe("send");
    expect(
      composerPrimaryAction({ ...base, hasComposerContent: true }),
    ).toBe("send");
  });

  it("streaming with an empty composer is the only Stop state", () => {
    expect(
      composerPrimaryAction({ ...base, isStreaming: true }),
    ).toBe("stop");
  });

  it("a typed ordinary message never turns into a stop", () => {
    // Regression: "salut" + click on the round button used to send /stop and
    // kill the running mission ("Stopped 1 task(s).").
    expect(
      composerPrimaryAction({
        ...base,
        isStreaming: true,
        hasComposerContent: true,
        canQueueGuidance: true,
      }),
    ).toBe("queue");
  });

  it("a slash command typed mid-run submits (explicit /stop stays possible)", () => {
    expect(
      composerPrimaryAction({
        ...base,
        isStreaming: true,
        hasComposerContent: true,
        canQueueGuidance: false, // slash content is not queueable guidance
      }),
    ).toBe("send");
  });

  it("without a stop handler streaming behaves like send", () => {
    expect(
      composerPrimaryAction({
        ...base,
        isStreaming: true,
        hasStopHandler: false,
      }),
    ).toBe("send");
  });

  it("model setup wins only when no stop/queue decision applies", () => {
    expect(
      composerPrimaryAction({ ...base, modelNeedsSetup: true }),
    ).toBe("configure");
    expect(
      composerPrimaryAction({
        ...base,
        isStreaming: true,
        modelNeedsSetup: true,
      }),
    ).toBe("stop");
  });
});
